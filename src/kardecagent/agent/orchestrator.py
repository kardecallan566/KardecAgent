from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..llm import LocalLLMClient
from ..project import project_snapshot
from .loop import AgentLoop
from .plan import ExecutionPlan\nfrom .state import TaskStatus
from ..tasks import Subtask, SubtaskManager, TaskBoard, SubtaskStatus

DECOMPOSITION_PROMPT = """You are the KardecAgent task decomposer.
The main execution plan has already been explicitly approved by the user.
Decompose it into 2-6 logical subtasks only when useful.

Rules:
- Never expand the approved scope.
- Every subtask must map to one or more approved plan steps.
- Every implementation subtask must declare a non-empty project-relative scope.
- Dependencies must be explicit.
- Execution is sequential.
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
    """Decomposes an approved plan and executes logical subtasks with one loaded LLM."""

    def __init__(self, llm: LocalLLMClient, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings
        self.agent_loop = AgentLoop(llm, settings)

    def should_decompose(self, plan: ExecutionPlan, project_root: Path) -> bool:
        manager = SubtaskManager(project_root)
        return manager.should_decompose(plan.steps, len(project_snapshot(project_root)))

    def decompose(self, plan: ExecutionPlan, project_root: Path) -> TaskBoard:
        manager = SubtaskManager(project_root)
        context = {
            "approved_plan": plan.as_dict(),
            "project_files": project_snapshot(project_root, limit=500),
            "constraints": {"max_subtasks": manager.max_subtasks, "max_depth": manager.max_depth},
        }
        messages = [
            {"role": "system", "content": DECOMPOSITION_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]
        last_error = None
        for _ in range(3):
            response = self.llm.chat(messages)
            try:
                data = json.loads(response.content.strip())
                board = manager.parse_decomposition(data)
                self._validate_plan_steps(data, plan)
                self._validate_scopes(board)
                return board
            except (json.JSONDecodeError, OrchestrationError, ValueError) as exc:
                last_error = str(exc)
                messages += [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": "Invalid decomposition: " + last_error +
                     ". Return valid JSON only and remain inside the approved plan."},
                ]
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

    @staticmethod
    def _validate_scopes(board: TaskBoard) -> None:
        for subtask in board.subtasks.values():
            if not subtask.scope:
                raise OrchestrationError(f"subtask {subtask.id} must declare a non-empty scope")

    def _subtask_plan(self, parent: ExecutionPlan, subtask: Subtask) -> ExecutionPlan:
        selected = [parent.steps[i - 1] for i in subtask.plan_steps]
        return ExecutionPlan(
            summary=f"{subtask.title}: {subtask.objective}",
            steps=selected,
            validation=list(parent.validation),
            risks=list(parent.risks),
            completion_criteria=list(subtask.completion_criteria) or list(parent.completion_criteria),
            security_level=parent.security_level,
            security_requirements=list(parent.security_requirements),
        )

    def execute_sequentially(
        self,
        board: TaskBoard,
        executor: Callable[[Subtask], SubtaskExecutionResult],
    ) -> TaskBoard:
        while not board.completed:
            ready = board.ready()
            if not ready:
                unfinished = [
                    task.id for task in board.subtasks.values()
                    if task.status in {SubtaskStatus.PENDING, SubtaskStatus.RUNNING}
                ]
                if unfinished:
                    self._mark_blocked(board)
                    raise OrchestrationError(
                        "no runnable subtasks remain; dependency graph may be blocked: " +
                        ", ".join(unfinished)
                    )
                break
            subtask = ready[0]
            board.mark_running(subtask.id)
            result = executor(subtask)
            subtask.iterations += 1
            if result.status == "completed":
                board.complete(subtask.id, result=result.summary, evidence=result.evidence)
            else:
                board.fail(subtask.id, result=result.summary)
                self._mark_blocked(board)
                break
        return board

    @staticmethod
    def _mark_blocked(board: TaskBoard) -> None:
        for task in board.subtasks.values():
            if task.status not in {SubtaskStatus.PENDING, SubtaskStatus.BLOCKED}:
                continue
            if any(board.get(dep).status in {SubtaskStatus.FAILED, SubtaskStatus.BLOCKED}
                   for dep in task.dependencies):
                task.status = SubtaskStatus.BLOCKED

    def execute_approved_plan(
        self, project_root: Path, parent_task: str, parent_plan: ExecutionPlan,
        board: TaskBoard,
    ) -> TaskBoard:
        """Execute subtasks without replanning or asking for approval again."""
        def execute(subtask: Subtask) -> SubtaskExecutionResult:
            subplan = self._subtask_plan(parent_plan, subtask)
            from .state import TaskState
            state = TaskState(
                task=f"{parent_task} :: {subtask.title}",
                project_root=str(project_root.resolve()),
            )
            state.status = TaskStatus.RUNNING
            result_state = self.agent_loop.execute_approved_plan(
                project_root,
                f"{parent_task} :: {subtask.objective}",
                subplan,
                state,
                context={
                    "parent_task": parent_task,
                    "parent_plan": parent_plan.as_dict(),
                    "subtask": subtask.as_dict(),
                },
                allowed_scope=subtask.scope,
                max_iterations=self.settings.max_iterations,
                allow_plan_changes=False,
            )
            evidence = [
                event.data.get("result") or event.message
                for event in result_state.events
                if event.event_type in {"completed", "verification", "plan_step_completed"}
            ]
            summary = "Subtask completed and verified." if result_state.status.value == "completed" else (
                f"Subtask stopped with status {result_state.status.value}."
            )
            return SubtaskExecutionResult(subtask.id, result_state.status.value, summary, evidence[-10:])

        return self.execute_sequentially(board, execute)
