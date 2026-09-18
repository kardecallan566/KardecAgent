from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TaskBoardError(ValueError):
    pass


class SubtaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class Subtask:
    id: str
    title: str
    objective: str
    scope: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    completion_criteria: tuple[str, ...] = ()
    plan_steps: tuple[int, ...] = ()
    status: SubtaskStatus = SubtaskStatus.PENDING
    result: str | None = None
    evidence: list[str] = field(default_factory=list)
    iterations: int = 0

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "objective": self.objective,
            "scope": list(self.scope),
            "dependencies": list(self.dependencies),
            "completion_criteria": list(self.completion_criteria),
            "plan_steps": list(self.plan_steps),
            "status": self.status.value,
            "result": self.result,
            "evidence": list(self.evidence),
            "iterations": self.iterations,
        }


@dataclass
class TaskBoard:
    subtasks: dict[str, Subtask] = field(default_factory=dict)

    def add(self, subtask: Subtask) -> None:
        if subtask.id in self.subtasks:
            raise TaskBoardError(f"duplicate subtask id: {subtask.id}")
        self.subtasks[subtask.id] = subtask

    def get(self, subtask_id: str) -> Subtask:
        try:
            return self.subtasks[subtask_id]
        except KeyError as exc:
            raise TaskBoardError(f"unknown subtask id: {subtask_id}") from exc

    def ready(self) -> list[Subtask]:
        ready = []
        for subtask in self.subtasks.values():
            if subtask.status is not SubtaskStatus.PENDING:
                continue
            if all(self.get(dep).status is SubtaskStatus.COMPLETED for dep in subtask.dependencies):
                ready.append(subtask)
        return ready

    def mark_running(self, subtask_id: str) -> None:
        subtask = self.get(subtask_id)
        if subtask.status is not SubtaskStatus.PENDING:
            raise TaskBoardError(f"subtask {subtask_id} is not pending")
        if any(self.get(dep).status is not SubtaskStatus.COMPLETED for dep in subtask.dependencies):
            raise TaskBoardError(f"subtask {subtask_id} has incomplete dependencies")
        subtask.status = SubtaskStatus.RUNNING

    def complete(self, subtask_id: str, *, result: str, evidence: list[str]) -> None:
        subtask = self.get(subtask_id)
        if subtask.status is not SubtaskStatus.RUNNING:
            raise TaskBoardError(f"subtask {subtask_id} is not running")
        subtask.status = SubtaskStatus.COMPLETED
        subtask.result = result
        subtask.evidence = list(evidence)

    def fail(self, subtask_id: str, *, result: str) -> None:
        subtask = self.get(subtask_id)
        if subtask.status is not SubtaskStatus.RUNNING:
            raise TaskBoardError(f"subtask {subtask_id} is not running")
        subtask.status = SubtaskStatus.FAILED
        subtask.result = result

    @property
    def completed(self) -> bool:
        return bool(self.subtasks) and all(
            task.status is SubtaskStatus.COMPLETED for task in self.subtasks.values()
        )

    def as_dict(self) -> dict:
        return {
            "subtasks": [task.as_dict() for task in self.subtasks.values()],
            "completed": self.completed,
        }
