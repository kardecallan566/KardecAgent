from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..llm import LocalLLMClient
from ..project import project_snapshot
from .loop import AgentLoop
from .plan import ExecutionPlan
from .recovery import RecoveryManager
from .state import TaskStatus
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
    resume_step: int = 1

class Orchestrator:
    """Decomposes an approved plan and executes logical subtasks with one loaded LLM."""

    def __init__(self, llm: LocalLLMClient, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings
        self.agent_loop = AgentLoop(llm, settings)
        self.recovery = RecoveryManager()

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
        progress_callback: Callable[[TaskBoard], None] | None = None,
        parent_state=None,
        resume_subtask_id: str | None = None,
        resume_step: int = 1,
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
            subtask = next(
                (item for item in ready if item.id == resume_subtask_id),
                ready[0],
            )
            board.mark_running(subtask.id)
            if parent_state is not None:
                parent_state.record(
                    "subtask_started",
                    "Logical subtask execution started.",
                    subtask_id=subtask.id,
                    plan_steps=list(subtask.plan_steps),
                    board=board.as_dict(),
                )
                if progress_callback is not None:
                    progress_callback(board)
            requested_step = resume_step if subtask.id == resume_subtask_id else 1
            result = executor(subtask, requested_step) if resume_subtask_id is not None else executor(subtask)
            resume_subtask_id = None
            resume_step = 1
            subtask.iterations += 1
            if result.status == "completed":
                board.complete(subtask.id, result=result.summary, evidence=result.evidence)
                if parent_state is not None:
                    parent_state.record(
                        "subtask_completed",
                        "Logical subtask completed.",
                        subtask_id=subtask.id,
                        plan_steps=list(subtask.plan_steps),
                        result=result.summary,
                        evidence=list(result.evidence),
                        resume_step=result.resume_step,
                        board=board.as_dict(),
                    )
            else:
                board.fail(subtask.id, result=result.summary)
                if parent_state is not None:
                    parent_state.record(
                        "subtask_failed",
                        "Logical subtask failed.",
                        subtask_id=subtask.id,
                        plan_steps=list(subtask.plan_steps),
                        result=result.summary,
                        resume_step=result.resume_step,
                        board=board.as_dict(),
                    )
                self._mark_blocked(board)
                if parent_state is not None:
                    for blocked in board.subtasks.values():
                        if blocked.status is SubtaskStatus.BLOCKED:
                            parent_state.record(
                                "subtask_blocked",
                                "Logical subtask blocked by a failed dependency.",
                                subtask_id=blocked.id,
                                board=board.as_dict(),
                            )
                if progress_callback is not None:
                    progress_callback(board)
                break
            if progress_callback is not None:
                progress_callback(board)
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
        progress_callback: Callable[[TaskBoard], None] | None = None,
        parent_state=None,
        resume_subtask_id: str | None = None,
        resume_step: int = 1,
    ) -> TaskBoard:
        """Execute subtasks without replanning or asking for approval again."""
        def execute(subtask: Subtask, requested_resume_step: int = 1) -> SubtaskExecutionResult:
            if not subtask.scope:
                return SubtaskExecutionResult(
                    subtask.id, "failed", "Implementation subtasks require an explicit scope.", []
                )
            subplan = self._subtask_plan(parent_plan, subtask)
            from .state import TaskState
            state = TaskState(
                task=f"{parent_task} :: {subtask.title}",
                project_root=str(project_root.resolve()),
            )
            state.transition(TaskStatus.RUNNING, reason="Subtask execution initialized.")
            def persist_subtask_progress(current_state, current_plan):
                if parent_state is not None:
                    parent_state.record(
                        "subtask_progress",
                        "Logical subtask execution cursor persisted.",
                        subtask_id=subtask.id,
                        plan_step=current_state.active_plan_step,
                        parent_plan_step=(
                            subtask.plan_steps[current_state.active_plan_step - 1]
                            if 1 <= current_state.active_plan_step <= len(subtask.plan_steps)
                            else subtask.plan_steps[-1]
                        ),
                        status=current_state.status.value,
                    )
                    if progress_callback is not None:
                        progress_callback(board)

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
                persistence_callback=persist_subtask_progress,
                resume_step=max(1, requested_resume_step),
            )
            if result_state.status.value != "completed":
                recovery = self.recovery.recover(
                    project_root,
                    result_state,
                    subtask_id=subtask.id,
                )
                result_state.record(
                    "subtask_recovery",
                    "Recovery attempted after subtask failure.",
                    recovered=recovery.recovered,
                    rolled_back_files=list(recovery.rolled_back_files),
                    conflict_paths=list(recovery.conflict_paths),
                )
                if recovery.recovered:
                    result_state.record(
                        "subtask_resume_retry_started",
                        "Retrying the approved subtask plan from the last consistent step.",
                        resume_step=recovery.resume_step,
                    )
                    if parent_state is not None:
                        parent_state.record(
                            "subtask_retry_started",
                            "Logical subtask retry started from the last consistent plan step.",
                            subtask_id=subtask.id,
                            resume_step=recovery.resume_step,
                            parent_plan_step=(
                                subtask.plan_steps[recovery.resume_step - 1]
                                if 1 <= recovery.resume_step <= len(subtask.plan_steps)
                                else subtask.plan_steps[-1]
                            ),
                            board=board.as_dict(),
                        )
                        if progress_callback is not None:
                            progress_callback(board)
                    result_state = self.agent_loop.execute_approved_plan(
                        project_root,
                        f"{parent_task} :: {subtask.objective}",
                        subplan,
                        result_state,
                        context={
                            "parent_task": parent_task,
                            "parent_plan": parent_plan.as_dict(),
                            "subtask": subtask.as_dict(),
                        },
                        allowed_scope=subtask.scope,
                        max_iterations=self.settings.max_iterations,
                        allow_plan_changes=False,
                        persistence_callback=persist_subtask_progress,
                        resume_step=recovery.resume_step,
                    )
                    summary = (
                        "Subtask retry completed and verified."
                        if result_state.status is TaskStatus.COMPLETED
                        else f"Subtask retry stopped with status {result_state.status.value}."
                    )
                elif not recovery.recovered:
                    summary = (
                        f"Subtask stopped with status {result_state.status.value}; "
                        "recovery was blocked by workspace conflicts."
                    )
                else:
                    summary = (
                        f"Subtask stopped with status {result_state.status.value}; "
                        "there were no audited changes to retry."
                    )
            else:
                summary = "Subtask completed and verified."
            evidence = [
                event.data.get("result") or event.message
                for event in result_state.events
                if event.event_type in {
                    "completed", "verification", "plan_step_completed",
                    "recovery_started", "recovery_completed", "recovery_failed",
                    "subtask_recovery",
                }
            ]
            return SubtaskExecutionResult(
                subtask.id, result_state.status.value, summary, evidence[-10:],
                resume_step=result_state.active_plan_step,
            )

        return self.execute_sequentially(
            board,
            execute,
            progress_callback=progress_callback,
            parent_state=parent_state,
            resume_subtask_id=resume_subtask_id,
            resume_step=max(1, resume_step),
        )
