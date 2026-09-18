from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..tools.workspace import rollback_file_change
from .state import TaskState


@dataclass(frozen=True)
class RecoveryResult:
    recovered: bool
    rolled_back_events: tuple[int, ...] = ()
    rolled_back_files: tuple[str, ...] = ()
    conflict_paths: tuple[str, ...] = ()
    error: str | None = None


class RecoveryManager:
    """Controller-owned recovery for audited changes.

    Recovery is deterministic and never exposed as a model tool. Only integrity
    events belonging to the failed execution scope are considered.
    """

    def recover(
        self,
        root: Path,
        state: TaskState,
        *,
        subtask_id: str | None = None,
        iteration: int | None = None,
        min_event_index: int = 0,
    ) -> RecoveryResult:
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

        state.record(
            "recovery_started",
            "Starting conflict-safe recovery of audited workspace changes.",
            subtask_id=subtask_id,
            iteration=iteration,
            min_event_index=min_event_index,
            event_count=len(candidates),
        )

        # Preflight every file before changing anything. This prevents a known
        # concurrent modification from causing a partially-applied recovery.
        changes: list[dict] = []
        for _, event in reversed(candidates):
            event_changes = event.data.get("changes", [])
            if not isinstance(event_changes, list):
                state.record("recovery_failed", "Invalid integrity event format.", event_index=_)
                return RecoveryResult(False, error="invalid integrity event format")
            changes.extend(reversed(event_changes))

        conflicts = self._preflight_conflicts(root, changes)
        if conflicts:
            state.record(
                "recovery_failed",
                "Recovery blocked because audited after-state hashes no longer match.",
                conflict_paths=conflicts,
            )
            return RecoveryResult(False, conflict_paths=tuple(conflicts),
                                  error="recovery conflict: workspace changed after audit")

        rolled_back_files: list[str] = []
        rolled_back_events: list[int] = []
        try:
            for event_index, event in reversed(candidates):
                for change in reversed(event.data.get("changes", [])):
                    rollback_file_change(root, change)
                    rolled_back_files.append(str(change["path"]))
                rolled_back_events.append(event_index)
                state.record(
                    "integrity_rollback",
                    "Audited operation rolled back safely during recovery.",
                    event_index=event_index,
                    changes=event.data.get("changes", []),
                    subtask_id=subtask_id,
                )
        except Exception as exc:
            state.record(
                "recovery_failed",
                "Recovery stopped after an unexpected rollback error.",
                error=str(exc),
                rolled_back_files=rolled_back_files,
            )
            return RecoveryResult(
                False,
                tuple(rolled_back_events),
                tuple(rolled_back_files),
                error=str(exc),
            )

        state.record(
            "recovery_completed",
            "Audited workspace changes were recovered successfully.",
            rolled_back_events=rolled_back_events,
            rolled_back_files=rolled_back_files,
            subtask_id=subtask_id,
        )
        return RecoveryResult(
            True,
            tuple(rolled_back_events),
            tuple(rolled_back_files),
        )

    @staticmethod
    def _preflight_conflicts(root: Path, changes: list[dict]) -> list[str]:
        import hashlib

        conflicts: list[str] = []
        seen: set[str] = set()
        for change in changes:
            raw = str(change["path"])
            if raw in seen:
                # Multiple operations may touch the same path. The newest
                # audited after-state is the state that must be present.
                continue
            seen.add(raw)
            target = root / raw
            try:
                target.relative_to(root.resolve())
            except ValueError:
                conflicts.append(raw)
                continue
            current = target
            unsafe = False
            while current != root:
                if current.is_symlink():
                    unsafe = True
                    break
                current = current.parent
            if unsafe or target.is_symlink() or (target.exists() and not target.is_file()):
                conflicts.append(raw)
                continue
            digest = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
            if digest != change.get("sha256_after"):
                conflicts.append(raw)
        return sorted(set(conflicts))
