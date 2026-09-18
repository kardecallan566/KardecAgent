from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..tools.workspace import rollback_file_change
from .state import TaskState, TaskStatus


@dataclass(frozen=True)
class RecoveryResult:
    recovered: bool
    rolled_back_events: tuple[int, ...] = ()
    rolled_back_files: tuple[str, ...] = ()
    conflict_paths: tuple[str, ...] = ()
    error: str | None = None
    resume_step: int = 1


class RecoveryManager:
    """Controller-owned, durable recovery transaction manager.

    Recovery itself is journaled before and after every rollback unit. If the
    process dies while rolling back, the next resume can inspect the durable
    transaction and continue without redoing already-recovered files.
    """

    def recover(
        self,
        root: Path,
        state: TaskState,
        *,
        subtask_id: str | None = None,
        iteration: int | None = None,
        min_event_index: int = 0,
        persistence_callback: Callable[[TaskState], None] | None = None,
    ) -> RecoveryResult:
        if state.status is TaskStatus.RECOVERING:
            return self.resume_pending_recovery(
                root, state, persistence_callback=persistence_callback
            )

        candidates = [
            (index, event)
            for index, event in enumerate(state.events)
            if index >= min_event_index
            and event.event_type == "integrity_change"
            and (subtask_id is None or event.data.get("subtask_id") == subtask_id)
            and (iteration is None or event.iteration == iteration)
        ]
        if not candidates:
            state.record(
                "recovery_skipped",
                "No audited integrity changes matched the recovery scope.",
                subtask_id=subtask_id,
                iteration=iteration,
                min_event_index=min_event_index,
            )
            return RecoveryResult(True)

        # Changes belonging to already completed plan steps are part of the
        # last consistent state and must survive recovery. Only roll back
        # mutations from the unfinished portion of the approved plan.
        completed_steps = [
            int(event.data["step"])
            for event in state.events
            if event.event_type == "plan_step_completed"
            and isinstance(event.data.get("step"), int)
        ]
        last_completed_step = max(completed_steps, default=0)
        candidates = [
            item for item in candidates
            if int(item[1].data.get("plan_step", 0)) > last_completed_step
        ]
        if not candidates:
            state.record(
                "recovery_skipped",
                "Audited changes are limited to already completed plan steps; no rollback is required.",
                subtask_id=subtask_id,
                iteration=iteration,
                min_event_index=min_event_index,
                last_completed_step=last_completed_step,
            )
            return RecoveryResult(True, resume_step=last_completed_step + 1)

        if state.status in {TaskStatus.FAILED, TaskStatus.MAX_ITERATIONS}:
            state.transition(TaskStatus.RECOVERING, reason="Starting conflict-safe recovery.")
        else:
            raise ValueError(f"cannot recover task from status {state.status.value}")

        units = self._build_units(candidates)
        conflicts = self._preflight_conflicts(root, [unit["change"] for unit in units])
        recovery_id = uuid.uuid4().hex
        selected_sequences = [unit["event_sequence"] for unit in units]

        state.record(
            "recovery_started",
            "Durable recovery transaction started.",
            recovery_id=recovery_id,
            subtask_id=subtask_id,
            iteration=iteration,
            min_event_index=min_event_index,
            event_sequences=selected_sequences,
            rollback_units=units,
        )
        self._persist(persistence_callback, state)

        if conflicts:
            state.record(
                "recovery_preflight_completed",
                "Recovery preflight detected workspace conflicts.",
                recovery_id=recovery_id,
                conflict_paths=conflicts,
                passed=False,
            )
            state.record(
                "recovery_failed",
                "Recovery blocked because audited after-state hashes no longer match.",
                recovery_id=recovery_id,
                conflict_paths=conflicts,
            )
            state.transition(TaskStatus.FAILED, reason="Recovery blocked by workspace conflicts.")
            self._persist(persistence_callback, state)
            return RecoveryResult(
                False,
                conflict_paths=tuple(conflicts),
                error="recovery conflict: workspace changed after audit",
            )

        state.record(
            "recovery_preflight_completed",
            "Recovery preflight completed successfully.",
            recovery_id=recovery_id,
            conflict_paths=[],
            passed=True,
        )
        self._persist(persistence_callback, state)
        return self._execute_transaction(
            root, state, recovery_id, units, persistence_callback=persistence_callback
        )

    def resume_pending_recovery(
        self,
        root: Path,
        state: TaskState,
        *,
        persistence_callback: Callable[[TaskState], None] | None = None,
    ) -> RecoveryResult:
        """Resume the latest recovery transaction that lacks a completion event."""
        transaction = self._pending_transaction(state)
        if transaction is None:
            raise ValueError("task is RECOVERING but has no pending recovery transaction")

        recovery_id, start_event = transaction
        units = start_event.data.get("rollback_units")
        if not isinstance(units, list) or not units:
            state.record(
                "recovery_failed",
                "Persisted recovery transaction has no rollback units.",
                recovery_id=recovery_id,
            )
            state.transition(TaskStatus.FAILED, reason="Persisted recovery transaction is invalid.")
            self._persist(persistence_callback, state)
            return RecoveryResult(False, error="invalid persisted recovery transaction")

        state.record(
            "recovery_resume_started",
            "Resuming an interrupted recovery transaction.",
            recovery_id=recovery_id,
        )
        self._persist(persistence_callback, state)
        return self._execute_transaction(
            root, state, recovery_id, units, persistence_callback=persistence_callback
        )

    def _execute_transaction(
        self,
        root: Path,
        state: TaskState,
        recovery_id: str,
        units: list[dict],
        *,
        persistence_callback: Callable[[TaskState], None] | None,
    ) -> RecoveryResult:
        completed_keys = {
            (event.data.get("event_sequence"), event.data.get("change_index"))
            for event in state.events
            if event.event_type == "recovery_file_rolled_back"
            and event.data.get("recovery_id") == recovery_id
        }

        rolled_back_events: list[int] = []
        rolled_back_files: list[str] = []
        try:
            for unit in units:
                key = (unit["event_sequence"], unit["change_index"])
                change = unit["change"]
                path = str(change["path"])

                if key in completed_keys:
                    # Durable event says this unit completed. Validate that
                    # the filesystem still has the expected before-state.
                    if not self._matches_digest(root, change, "before"):
                        raise RuntimeError(
                            f"recovery conflict after previously completed rollback: {path}"
                        )
                    continue

                state.record(
                    "recovery_rollback_started",
                    "Rollback unit started.",
                    recovery_id=recovery_id,
                    event_sequence=unit["event_sequence"],
                    change_index=unit["change_index"],
                    path=path,
                )
                self._persist(persistence_callback, state)

                current_kind = self._current_state(root, change)
                expected_after = change.get("sha256_after")
                expected_before = change.get("sha256_before")
                if current_kind == expected_before:
                    # The filesystem is already at the target state, which can
                    # happen if the process crashed after the mutation but
                    # before the durable completion event.
                    pass
                elif current_kind == expected_after:
                    rollback_file_change(root, change)
                else:
                    raise RuntimeError(f"recovery conflict: {path}")

                state.record(
                    "recovery_file_rolled_back",
                    "One audited file change was rolled back safely.",
                    recovery_id=recovery_id,
                    event_sequence=unit["event_sequence"],
                    change_index=unit["change_index"],
                    path=path,
                    sha256_before=expected_before,
                    sha256_after=expected_after,
                )
                self._persist(persistence_callback, state)
                if unit["event_sequence"] not in rolled_back_events:
                    rolled_back_events.append(unit["event_sequence"])
                rolled_back_files.append(path)

            state.record(
                "recovery_rollback_completed",
                "All rollback units completed.",
                recovery_id=recovery_id,
                rolled_back_events=sorted(set(rolled_back_events)),
                rolled_back_files=rolled_back_files,
            )
            completed_steps = [
                int(event.data["step"])
                for event in state.events
                if event.event_type == "plan_step_completed"
                and isinstance(event.data.get("step"), int)
            ]
            resume_step = max(completed_steps, default=0) + 1
            state.transition(
                TaskStatus.RESUMING,
                reason="Recovery completed; task may resume.",
                recovery_id=recovery_id,
                resume_step=resume_step,
            )
            state.record(
                "recovery_completed",
                "Durable recovery transaction completed successfully.",
                recovery_id=recovery_id,
                rolled_back_events=sorted(set(rolled_back_events)),
                rolled_back_files=rolled_back_files,
                resume_step=resume_step,
            )
            self._persist(persistence_callback, state)
            return RecoveryResult(
                True,
                tuple(sorted(set(rolled_back_events))),
                tuple(rolled_back_files),
                resume_step=resume_step,
            )
        except Exception as exc:
            state.record(
                "recovery_failed",
                "Recovery transaction stopped because the workspace no longer matches its audited state.",
                recovery_id=recovery_id,
                error=str(exc),
                rolled_back_files=rolled_back_files,
            )
            if state.status is TaskStatus.RECOVERING:
                state.transition(TaskStatus.FAILED, reason="Recovery transaction failed.")
            self._persist(persistence_callback, state)
            return RecoveryResult(
                False,
                tuple(sorted(set(rolled_back_events))),
                tuple(rolled_back_files),
                error=str(exc),
            )

    @staticmethod
    def _build_units(candidates: list[tuple[int, object]]) -> list[dict]:
        units: list[dict] = []
        for event_index, event in reversed(candidates):
            event_changes = event.data.get("changes", [])
            if not isinstance(event_changes, list):
                raise ValueError("invalid integrity event format")
            for change_index, change in reversed(list(enumerate(event_changes))):
                if not isinstance(change, dict) or "path" not in change:
                    raise ValueError("invalid integrity change")
                units.append({
                    "event_sequence": event.sequence,
                    "event_index": event_index,
                    "change_index": change_index,
                    "change": change,
                })
        return units

    @staticmethod
    def _pending_transaction(state: TaskState) -> tuple[str, object] | None:
        starts = {
            str(event.data.get("recovery_id")): event
            for event in state.events
            if event.event_type == "recovery_started" and event.data.get("recovery_id")
        }
        completed = {
            str(event.data.get("recovery_id"))
            for event in state.events
            if event.event_type == "recovery_completed" and event.data.get("recovery_id")
        }
        pending = [(event.sequence, rid, event) for rid, event in starts.items() if rid not in completed]
        if not pending:
            return None
        _, recovery_id, event = max(pending)
        return recovery_id, event

    @staticmethod
    def _current_state(root: Path, change: dict) -> str | None:
        target = root / str(change["path"])
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise RuntimeError(f"unsafe recovery path: {change['path']}") from exc
        current = target
        while current != root:
            if current.is_symlink():
                raise RuntimeError(f"unsafe recovery path: {change['path']}")
            current = current.parent
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise RuntimeError(f"unsafe recovery path: {change['path']}")
        return hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None

    @classmethod
    def _matches_digest(cls, root: Path, change: dict, which: str) -> bool:
        return cls._current_state(root, change) == change.get(f"sha256_{which}")

    @staticmethod
    def _persist(callback: Callable[[TaskState], None] | None, state: TaskState) -> None:
        if callback is not None:
            callback(state)

    @staticmethod
    def _preflight_conflicts(root: Path, changes: list[dict]) -> list[str]:
        conflicts: list[str] = []
        virtual_digest: dict[str, str | None] = {}

        for change in changes:
            raw = str(change["path"])
            current = root / raw
            try:
                current.relative_to(root.resolve())
            except ValueError:
                conflicts.append(raw)
                continue

            unsafe = False
            cursor = current
            while cursor != root:
                if cursor.is_symlink():
                    unsafe = True
                    break
                cursor = cursor.parent
            if unsafe or current.is_symlink() or (current.exists() and not current.is_file()):
                conflicts.append(raw)
                continue

            if raw not in virtual_digest:
                virtual_digest[raw] = (
                    hashlib.sha256(current.read_bytes()).hexdigest()
                    if current.is_file() else None
                )
            if virtual_digest[raw] != change.get("sha256_after"):
                conflicts.append(raw)
                continue
            virtual_digest[raw] = change.get("sha256_before")

        return sorted(set(conflicts))
