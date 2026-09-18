from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..llm import LocalLLMClient
from ..project import project_snapshot
from ..config import Settings
from .plan import ExecutionPlan
from .state import TaskState
from ..tasks import Subtask, SubtaskManager, TaskBoard
from ..tools_schema import ToolCallError


DECOMPOSITION_PROMPT = """You are the KardecAgent task decomposer.
The main execution plan has already been explicitly approved by the user.
Decompose it into 2-6 logical subtasks only when useful.

Rules:
- Never expand the approved scope.
- Every subtask must map to one or more approved plan steps.
- Prefer clear file/component scope.
- Dependencies must be explicit.
- Do not create parallel work assumptions; execution is sequential.
- Return ONLY JSON:
{"subtasks":[{"id":"1","title":"...","objective":"...","scope":["src/..."],"dependencies":[],"completion_criteria":["..."],"plan_steps":[1]}]}
"""


class OrchestrationError(ValueError):
    pass


@dataclass(frozen=True)
class SubtaskExecutionResult:
    subtask_id: str
    status: str
    summary: str
    evidence: list[str]


class Orchestrator:
    """Coordinates approved plans and logical subagents using one loaded LLM."""

    def __init__(self, llm: LocalLLMClient, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    def should_decompose(self, plan: ExecutionPlan, project_root: Path) -> bool:
        manager = SubtaskManager(project_root)
        return manager.should_decompose(plan.steps, len(project_snapshot(project_root)))

    def decompose(self, plan: ExecutionPlan, project_root: Path) -> TaskBoard:
        manager = SubtaskManager(project_root)
        context = {
            "approved_plan": plan.as_dict(),
            "project_files": project_snapshot(project_root, limit=500),
            "constraints": {
                "max_subtasks": manager.max_subtasks,
                "max_depth": manager.max_depth,
            },
        }
        messages = [
            {"role": "system", "content": DECOMPOSITION_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]

        last_error: str | None = None
        for _ in range(3):
            response = self.llm.chat(messages)
            try:
                data = json.loads(response.content.strip())
                board = manager.parse_decomposition(data)
                self._validate_plan_steps(data, plan)
                return board
            except (json.JSONDecodeError, OrchestrationError, ValueError) as exc:
                last_error = str(exc)
                messages.extend([
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": "Invalid decomposition: " + last_error + ". Return valid JSON only and remain inside the approved plan."},
                ])
        raise OrchestrationError(last_error or "could not create subtask decomposition")

    @staticmethod
    def _validate_plan_steps(data: dict, plan: ExecutionPlan) -> None:
        known = set(range(1, len(plan.steps) + 1))
        for item in data["subtasks"]:
            steps = item.get("plan_steps")
            if not isinstance(steps, list) or not steps or any(
                isinstance(step, bool) or not isinstance(step, int) or step not in known
                for step in steps
            ):
                raise OrchestrationError(
                    f"subtask {item.get('id', '<unknown>')} must reference valid approved plan_steps"
                )

    def execute_sequentially(
        self,
        board: TaskBoard,
        executor: Callable[[Subtask], SubtaskExecutionResult],
    ) -> TaskBoard:
        """Run ready subtasks one at a time.

        The executor is deliberately injected: the Orchestrator does not create
        another model process. A future AgentExecutor adapter can reuse the
        already-loaded LLM and approved parent context.
        """
        manager = SubtaskManager(Path.cwd(), max_subtasks=max(1, len(board.subtasks)))
        while not board.completed:
            manager.mark_blocked(board)
            subtask = manager.next_subtask(board)
            if subtask is None:
                unfinished = [
                    task.id for task in board.subtasks.values()
                    if task.status in {task.status.PENDING, task.status.RUNNING}
                ]
                if unfinished:
                    raise OrchestrationError(
                        "no runnable subtasks remain; dependency graph may be blocked: "
                        + ", ".join(unfinished)
                    )
                break

            board.mark_running(subtask.id)
            result = executor(subtask)
            subtask.iterations += 1
            if result.status == "completed":
                board.complete(subtask.id, result=result.summary, evidence=result.evidence)
            else:
                board.fail(subtask.id, result=result.summary)
                manager.mark_blocked(board)
                break
        return board
