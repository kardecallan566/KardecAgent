from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path

from .plan import ExecutionPlan, parse_plan
from .state import AgentEvent, TaskState, TaskStatus
from ..tasks import Subtask, SubtaskStatus, TaskBoard


class PersistenceError(ValueError):
    pass


def task_id(task: str, project_root: Path) -> str:
    import hashlib
    return hashlib.sha256(
        (str(project_root.resolve()) + "\0" + task).encode("utf-8")
    ).hexdigest()[:20]


class TaskStore:
    """Durable local task state. Writes are atomic and scoped to the project."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.directory = self.project_root / ".kardecagent" / "tasks"

    def path_for(self, task: str) -> Path:
        return self.directory / f"{task_id(task, self.project_root)}.json"

    def save(
        self,
        state: TaskState,
        *,
        plan: ExecutionPlan,
        board: TaskBoard | None = None,
        approved: bool = True,
    ) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "task": state.task,
            "project_root": state.project_root,
            "status": state.status.value,
            "iteration": state.iteration,
            "approved": approved,
            "plan": plan.as_dict(),
            "board": board.as_dict() if board is not None else None,
            "events": [asdict(event) for event in state.events],
        }
        target = self.path_for(state.task)
        fd, temp_name = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=self.directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return target

    def load(self, task: str) -> tuple[TaskState, ExecutionPlan, TaskBoard | None]:
        target = self.path_for(task)
        if not target.is_file():
            raise PersistenceError(f"no persisted task found: {task}")
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            if payload.get("version") != 1 or payload.get("approved") is not True:
                raise PersistenceError("persisted task is not an approved resumable task")
            if Path(payload["project_root"]).resolve() != self.project_root:
                raise PersistenceError("persisted project root does not match current project")
            plan = parse_plan(json.dumps(payload["plan"], ensure_ascii=False))
            state = TaskState(
                payload["task"], payload["project_root"],
                TaskStatus(payload["status"]), int(payload["iteration"]),
                [AgentEvent(
                    int(event["iteration"]), event["event_type"],
                    event["message"], dict(event.get("data", {})),
                    str(event.get("timestamp", ""))
                ) for event in payload.get("events", [])],
            )
            board = _board_from_dict(payload.get("board"))
            # A process may stop while a subtask is running. It is safe to
            # retry that subtask because its completion was not durably recorded.
            if board is not None:
                for subtask in board.subtasks.values():
                    if subtask.status is SubtaskStatus.RUNNING:
                        subtask.status = SubtaskStatus.PENDING
            return state, plan, board
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, PersistenceError):
                raise
            raise PersistenceError(f"invalid persisted task: {target.name}") from exc


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
