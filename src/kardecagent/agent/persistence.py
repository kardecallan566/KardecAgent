from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .plan import ExecutionPlan, parse_plan
from .state import AgentEvent, TaskState, TaskStatus
from ..tasks import Subtask, SubtaskStatus, TaskBoard


class PersistenceError(ValueError):
    pass


def task_id(task: str, project_root: Path) -> str:
    return hashlib.sha256(
        (str(project_root.resolve()) + "\0" + task).encode("utf-8")
    ).hexdigest()[:20]


class TaskJournal:
    """Append-only, hash-chained execution journal for one task."""

    VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def _canonical(payload: dict) -> bytes:
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def append_events(self, state: TaskState) -> tuple[int, str | None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.read()
        expected = len(existing) + 1
        previous_hash = existing[-1]["checksum"] if existing else ""
        current_status = existing[-1]["status"] if existing else TaskStatus.PENDING.value
        pending = [event for event in state.events if event.sequence >= expected]

        if pending and pending[0].sequence != expected:
            raise PersistenceError(
                f"journal sequence gap: expected {expected}, got {pending[0].sequence}"
            )
        if not pending:
            return len(existing), previous_hash or None

        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            with os.fdopen(fd, "a", encoding="utf-8", newline="") as handle:
                for event in pending:
                    if event.sequence != expected:
                        raise PersistenceError(
                            f"journal sequence gap: expected {expected}, got {event.sequence}"
                        )
                    if event.event_type == "status_changed":
                        current_status = str(event.data.get("to_status", current_status))
                    cursor = journal_cursor_from_event(
                        event,
                        current_plan_step=state.active_plan_step,
                        current_subtask_id=state.active_subtask_id,
                    )
                    record = {
                        "version": self.VERSION,
                        "sequence": event.sequence,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "task": state.task,
                        "event": asdict(event),
                        "status": current_status,
                        "iteration": event.iteration,
                        "cursor": cursor,
                        "previous_checksum": previous_hash,
                    }
                    record["checksum"] = hashlib.sha256(
                        self._canonical(record)
                    ).hexdigest()
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    previous_hash = record["checksum"]
                    expected += 1
        except OSError as exc:
            raise PersistenceError("failed to append execution journal") from exc
        return expected - 1, previous_hash

    def read(self) -> list[dict]:
        if not self.path.is_file():
            return []
        raw = self.path.read_bytes()
        lines = raw.splitlines(keepends=True)
        complete = bool(not raw or lines[-1].endswith((b"\n", b"\r")))
        if raw and not complete:
            lines = lines[:-1]

        records: list[dict] = []
        for index, raw_line in enumerate(lines, start=1):
            try:
                record = json.loads(raw_line.decode("utf-8"))
                self._validate_record(record, index, records[-1] if records else None)
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                if index == len(lines) and raw and not complete:
                    self._truncate_to_valid_lines(lines, records)
                    break
                raise PersistenceError(
                    f"execution journal corruption at line {index}"
                ) from exc
            records.append(record)

        # A final incomplete line is an interrupted append; the last durable
        # newline-delimited record is the authoritative journal tail.
        if raw and not complete:
            self._truncate_to_valid_lines(lines, records)
        return records

    def _validate_record(self, record: dict, line_number: int, previous: dict | None) -> None:
        if record.get("version") != self.VERSION:
            raise PersistenceError(f"unsupported journal version at line {line_number}")
        if int(record["sequence"]) != line_number:
            raise PersistenceError(f"journal sequence mismatch at line {line_number}")
        expected_previous = previous["checksum"] if previous else ""
        if record.get("previous_checksum", "") != expected_previous:
            raise PersistenceError(f"journal hash chain mismatch at line {line_number}")
        supplied = record.get("checksum")
        unsigned = dict(record)
        unsigned.pop("checksum", None)
        if supplied != hashlib.sha256(self._canonical(unsigned)).hexdigest():
            raise PersistenceError(f"journal checksum mismatch at line {line_number}")
        event = record["event"]
        if int(event["sequence"]) != line_number:
            raise PersistenceError(f"journal event sequence mismatch at line {line_number}")
        if not record.get("task"):
            raise PersistenceError(f"journal task missing at line {line_number}")

    def _truncate_to_valid_lines(self, lines: list[bytes], records: list[dict]) -> None:
        valid_bytes = b"".join(lines[:len(records)])
        temp = self.path.with_suffix(self.path.suffix + ".repair.tmp")
        try:
            temp.write_bytes(valid_bytes)
            with temp.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except OSError as exc:
            raise PersistenceError("failed to repair execution journal") from exc
        finally:
            if temp.exists():
                temp.unlink()

    def last(self) -> dict | None:
        records = self.read()
        return records[-1] if records else None


class TaskStore:
    """Durable local task state with an atomic snapshot and append-only journal."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.directory = self.project_root / ".kardecagent" / "tasks"

    def path_for(self, task: str) -> Path:
        return self.directory / f"{task_id(task, self.project_root)}.json"

    def journal_path_for(self, task: str) -> Path:
        return self.directory / f"{task_id(task, self.project_root)}.journal"

    def save(
        self,
        state: TaskState,
        *,
        plan: ExecutionPlan,
        board: TaskBoard | None = None,
        approved: bool = True,
    ) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        journal = TaskJournal(self.journal_path_for(state.task))
        journal_sequence, journal_checksum = journal.append_events(state)

        payload = {
            "version": 2,
            "task": state.task,
            "project_root": state.project_root,
            "status": state.status.value,
            "iteration": state.iteration,
            "approved": approved,
            "plan": plan.as_dict(),
            "board": board.as_dict() if board is not None else None,
            "events": [asdict(event) for event in state.events],
            "journal_sequence": journal_sequence,
            "journal_checksum": journal_checksum,
            "cursor": {"plan_step": state.active_plan_step, "subtask_id": state.active_subtask_id},
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        payload["integrity_sha256"] = hashlib.sha256(encoded).hexdigest()

        target = self.path_for(state.task)
        backup = target.with_suffix(target.suffix + ".bak")
        fd, temp_name = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=self.directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            if target.is_file():
                os.replace(target, backup)
            os.replace(temp_name, target)
            self._fsync_directory()
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return target

    def load(self, task: str) -> tuple[TaskState, ExecutionPlan, TaskBoard | None]:
        target = self.path_for(task)
        if not target.is_file():
            raise PersistenceError(f"no persisted task found: {task}")

        payload = self._load_snapshot_with_backup(target)
        self._validate_payload(payload, target)
        plan = parse_plan(json.dumps(payload["plan"], ensure_ascii=False))
        state = self._state_from_payload(payload)

        journal = TaskJournal(self.journal_path_for(task))
        records = journal.read()
        snapshot_sequence = int(payload.get("journal_sequence", 0))
        snapshot_checksum = payload.get("journal_checksum")

        if records:
            if snapshot_sequence > len(records):
                raise PersistenceError("snapshot references missing journal entries")
            if snapshot_sequence and records[snapshot_sequence - 1]["checksum"] != snapshot_checksum:
                raise PersistenceError("snapshot journal checksum does not match journal")
            # A crash after journaling but before snapshot replacement leaves
            # valid events beyond the snapshot. Replay them into the state.
            if len(records) > snapshot_sequence:
                for record in records[snapshot_sequence:]:
                    state.events.append(self._event_from_dict(record["event"]))
                    state.status = TaskStatus(record["status"])
                    state.iteration = int(record["iteration"])
            # The journal is authoritative for event history.
            state.events = [self._event_from_dict(record["event"]) for record in records]
            latest = records[-1]
            state.status = TaskStatus(latest["status"])
            state.iteration = int(latest["iteration"])
            cursor = replay_journal_cursor(records)
            state.active_plan_step = cursor["plan_step"]
            state.active_subtask_id = cursor["subtask_id"]
        elif snapshot_sequence:
            raise PersistenceError("persisted task references a missing execution journal")

        board = _board_from_dict(payload.get("board"))
        # Journaled board snapshots are authoritative when the journal is ahead
        # of the atomic snapshot (for example, a crash between the two writes).
        for record in records:
            event_data = record.get("event", {}).get("data", {})
            if isinstance(event_data.get("board"), dict):
                board = _board_from_dict(event_data["board"])
        if board is not None:
            for subtask in board.subtasks.values():
                if subtask.status is SubtaskStatus.RUNNING:
                    subtask.status = SubtaskStatus.PENDING
        return state, plan, board

    def _load_snapshot_with_backup(self, target: Path) -> dict:
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            self._validate_payload(payload, target)
            return payload
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, PersistenceError):
            backup = target.with_suffix(target.suffix + ".bak")
            if not backup.is_file():
                raise PersistenceError(f"invalid persisted task: {target.name}")
            try:
                payload = json.loads(backup.read_text(encoding="utf-8"))
                self._validate_payload(payload, backup)
                self._restore_snapshot_bytes(backup, target)
                return payload
            except Exception as exc:
                raise PersistenceError(
                    f"invalid persisted task and backup: {target.name}"
                ) from exc

    def _restore_snapshot_bytes(self, backup: Path, target: Path) -> None:
        fd, temp_name = tempfile.mkstemp(
            prefix=target.name + ".restore.", suffix=".tmp", dir=self.directory
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(backup.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
            self._fsync_directory()
        except OSError as exc:
            raise PersistenceError("failed to restore snapshot backup") from exc
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _validate_payload(self, payload: dict, source: Path) -> None:
        try:
            if payload.get("version") not in {1, 2} or payload.get("approved") is not True:
                raise PersistenceError("persisted task is not an approved resumable task")
            if payload.get("version") == 2:
                supplied = payload.get("integrity_sha256")
                unsigned = dict(payload)
                unsigned.pop("integrity_sha256", None)
                encoded = json.dumps(unsigned, ensure_ascii=False, indent=2).encode("utf-8")
                if supplied != hashlib.sha256(encoded).hexdigest():
                    raise PersistenceError("persisted task integrity check failed")
            if Path(payload["project_root"]).resolve() != self.project_root:
                raise PersistenceError("persisted project root does not match current project")
        except PersistenceError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PersistenceError(f"invalid persisted task: {source.name}") from exc

    @staticmethod
    def _event_from_dict(event: dict) -> AgentEvent:
        return AgentEvent(
            int(event["iteration"]),
            event["event_type"],
            event["message"],
            dict(event.get("data", {})),
            str(event.get("timestamp", "")),
            int(event.get("sequence", 0)),
        )

    def _state_from_payload(self, payload: dict) -> TaskState:
        events = [self._event_from_dict(event) for event in payload.get("events", [])]
        for index, event in enumerate(events, start=1):
            event.sequence = index
        return TaskState(
            payload["task"],
            payload["project_root"],
            TaskStatus(payload["status"]),
            int(payload["iteration"]),
            events,
        )

    def _fsync_directory(self) -> None:
        directory_fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _board_from_dict(payload: dict | None) -> TaskBoard | None:
    if payload is None:
        return None
    board = TaskBoard()
    for item in payload.get("subtasks", []):
        board.add(Subtask(
            id=item["id"], title=item["title"], objective=item["objective"],
            scope=tuple(item.get("scope", [])),
            dependencies=tuple(item.get("dependencies", [])),
            completion_criteria=tuple(item.get("completion_criteria", [])),
            plan_steps=tuple(item.get("plan_steps", [])),
            status=SubtaskStatus(item.get("status", "pending")),
            result=item.get("result"),
            evidence=list(item.get("evidence", [])),
            iterations=int(item.get("iterations", 0)),
        ))
    return board


def _event_from_journal_record(record: dict) -> AgentEvent:
    event = record["event"]
    return AgentEvent(
        int(event["iteration"]),
        event["event_type"],
        event["message"],
        dict(event.get("data", {})),
        str(event.get("timestamp", "")),
        int(event.get("sequence", 0)),
    )


def journal_cursor_from_event(
    event: AgentEvent,
    *,
    current_plan_step: int,
    current_subtask_id: str | None,
) -> dict:
    """Apply one event to the durable execution cursor."""
    plan_step = current_plan_step
    subtask_id = current_subtask_id
    data = event.data
    if event.event_type == "plan_step_started":
        step = data.get("step")
        if isinstance(step, int) and step > 0:
            plan_step = step
    elif event.event_type == "plan_step_completed":
        step = data.get("step")
        if isinstance(step, int) and step > 0:
            plan_step = step + 1
    elif event.event_type in {"recovery_completed", "subtask_resume_retry_started"}:
        step = data.get("resume_step")
        if isinstance(step, int) and step > 0:
            plan_step = step
    if event.event_type == "subtask_started":
        value = data.get("subtask_id")
        subtask_id = value if isinstance(value, str) else None
    elif event.event_type in {"subtask_completed", "subtask_failed", "subtask_blocked"}:
        if data.get("subtask_id") == subtask_id:
            subtask_id = None
    return {"plan_step": plan_step, "subtask_id": subtask_id}


def replay_journal_cursor(records: list[dict]) -> dict:
    """Reconstruct the latest execution cursor exclusively from journal events."""
    cursor = {"plan_step": 1, "subtask_id": None}
    for record in records:
        cursor = journal_cursor_from_event(
            _event_from_journal_record(record),
            current_plan_step=cursor["plan_step"],
            current_subtask_id=cursor["subtask_id"],
        )
    return cursor
