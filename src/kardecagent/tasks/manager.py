from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .task import Subtask, SubtaskStatus, TaskBoard, TaskBoardError


class SubtaskDecompositionError(ValueError):
    pass


class SubtaskManager:
    """Validate and schedule logical subtasks without spawning model processes."""

    def __init__(
        self,
        project_root: Path,
        *,
        max_subtasks: int = 6,
        max_depth: int = 1,
    ) -> None:
        self.project_root = project_root.resolve()
        self.max_subtasks = max_subtasks
        self.max_depth = max_depth

    def should_decompose(self, plan_steps: list[str], project_file_count: int) -> bool:
        return len(plan_steps) >= 4 or project_file_count >= 80

    def parse_decomposition(self, payload: str | dict[str, Any]) -> TaskBoard:
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except json.JSONDecodeError as exc:
            raise SubtaskDecompositionError("decomposition must be valid JSON") from exc

        if not isinstance(data, dict) or not isinstance(data.get("subtasks"), list):
            raise SubtaskDecompositionError("decomposition must contain a subtasks list")

        items = data["subtasks"]
        if not 2 <= len(items) <= self.max_subtasks:
            raise SubtaskDecompositionError(
                f"subtasks count must be between 2 and {self.max_subtasks}"
            )

        board = TaskBoard()
        for item in items:
            if not isinstance(item, dict):
                raise SubtaskDecompositionError("each subtask must be an object")
            sid = item.get("id")
            title = item.get("title")
            objective = item.get("objective")
            if not all(isinstance(value, str) and value.strip() for value in (sid, title, objective)):
                raise SubtaskDecompositionError("id, title and objective must be non-empty strings")
            if len(sid) > 40 or len(title) > 160 or len(objective) > 1000:
                raise SubtaskDecompositionError("subtask text exceeds the configured size limit")

            scope = self._paths(item.get("scope", []))
            dependencies = self._strings(item.get("dependencies", []), "dependencies")
            criteria = self._strings(item.get("completion_criteria", []), "completion_criteria")
            plan_steps = item.get("plan_steps", [])
            if not isinstance(plan_steps, list) or any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in plan_steps):
                raise SubtaskDecompositionError("plan_steps must be a list of positive integers")
            board.add(Subtask(
                id=sid,
                title=title.strip(),
                objective=objective.strip(),
                scope=tuple(scope),
                dependencies=tuple(dependencies),
                completion_criteria=tuple(criteria),
                plan_steps=tuple(plan_steps),
            ))

        self._validate_dependencies(board)
        return board

    def _strings(self, value: Any, field: str) -> list[str]:
        if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
            raise SubtaskDecompositionError(f"{field} must be a list of non-empty strings")
        return [x.strip() for x in value]

    def _paths(self, value: Any) -> list[str]:
        paths = self._strings(value, "scope")
        for raw in paths:
            path = Path(raw)
            if path.is_absolute() or ".." in path.parts:
                raise SubtaskDecompositionError(f"subtask scope escapes project: {raw}")
            candidate = (self.project_root / path).resolve()
            try:
                candidate.relative_to(self.project_root)
            except ValueError as exc:
                raise SubtaskDecompositionError(f"subtask scope escapes project: {raw}") from exc
        return [Path(p).as_posix() for p in paths]

    def _validate_dependencies(self, board: TaskBoard) -> None:
        for task in board.subtasks.values():
            if task.id in task.dependencies:
                raise SubtaskDecompositionError(f"subtask cannot depend on itself: {task.id}")
            for dep in task.dependencies:
                if dep not in board.subtasks:
                    raise SubtaskDecompositionError(f"unknown dependency: {dep}")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise SubtaskDecompositionError("subtask dependency cycle detected")
            if node in visited:
                return
            visiting.add(node)
            for dep in board.get(node).dependencies:
                visit(dep)
            visiting.remove(node)
            visited.add(node)

        for sid in board.subtasks:
            visit(sid)

    def next_subtask(self, board: TaskBoard) -> Subtask | None:
        ready = board.ready()
        return ready[0] if ready else None

    def mark_blocked(self, board: TaskBoard) -> None:
        for task in board.subtasks.values():
            if task.status is not SubtaskStatus.PENDING and task.status is not SubtaskStatus.BLOCKED:
                continue
            if any(board.get(dep).status in {SubtaskStatus.FAILED, SubtaskStatus.BLOCKED} for dep in task.dependencies):
                task.status = SubtaskStatus.BLOCKED
