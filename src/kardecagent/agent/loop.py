from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from ..llm import LocalLLMClient
from ..project import detect_project, project_snapshot
from ..tools import create_checkpoint, git_current_branch, git_has_uncommitted_changes, git_is_repo
from .executor import AgentExecutor
from .plan import ExecutionPlan, PlanError, parse_plan, plan_instructions
from .security import assess_security, security_requirements_for
from .state import TaskState, TaskStatus
from .persistence import TaskStore, PersistenceError
from .recovery import RecoveryManager
from .tools_schema import ToolCallError

SYSTEM_PROMPT = (
    "You are KardecAgent, a local software engineering agent. "
    "Work only inside the configured project. Never claim completion before verification."
)

class AgentLoop:
    """Public task workflow: inspect, plan, obtain approval, then execute."""

    def __init__(self, llm: LocalLLMClient, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings
        self.executor = AgentExecutor(llm, settings)
        self.recovery = RecoveryManager()

    def _build_context(self, root: Path, task: str) -> dict:
        profile = detect_project(root)
        return {
            "task": task,
            "project": {
                "kind": profile.kind, "language": profile.language,
                "framework": profile.framework, "package_manager": profile.package_manager,
                "commands": profile.commands,
            },
            "project_files": project_snapshot(root),
        }

    def _create_plan(self, root: Path, task: str, state: TaskState) -> ExecutionPlan | None:
        context = self._build_context(root, task)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n" + plan_instructions() +
             " You are in the planning phase. Do not call implementation tools."},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]
        for _ in range(min(5, self.settings.max_iterations)):
            response = self.llm.chat(messages)
            state.record("plan_response", response.content)
            try:
                plan = parse_plan(response.content)
                assessment = assess_security(task)
                state.record("security_assessment", "Task security sensitivity assessed.",
                             level=assessment.level, matched_signals=list(assessment.matched_signals))
                if assessment.sensitive:
                    plan = ExecutionPlan(
                        plan.summary, plan.steps, plan.validation, plan.risks,
                        plan.completion_criteria, assessment.level,
                        security_requirements_for(assessment.level),
                    )
                    state.record("security_mode_enabled", "Security-sensitive execution controls enabled.",
                                 level=assessment.level, requirements=plan.security_requirements)
                return plan
            except PlanError as exc:
                state.record("plan_error", str(exc))
                messages += [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": "Invalid plan: " + str(exc) + ". " + plan_instructions()},
                ]
        return None

    def run(
        self,
        project_root: Path,
        task: str,
        *,
        approval_callback=None,
        high_risk_approval_callback=None,
    ) -> TaskState:
        root = project_root.resolve()
        state = TaskState(task, str(root))
        state.status = TaskStatus.RUNNING
        profile = detect_project(root)
        state.record("project_detected", f"Detected project kind: {profile.kind}.",
                     kind=profile.kind, language=profile.language, framework=profile.framework,
                     package_manager=profile.package_manager, commands=profile.commands)

        plan = self._create_plan(root, task, state)
        if plan is None:
            state.status = TaskStatus.FAILED
            state.record("plan_failed", "Could not produce a valid execution plan.")
            return state
        state.record("plan_created", "Execution plan created.", plan=plan.as_dict())

        if approval_callback is None:
            state.status = TaskStatus.FAILED
            state.record("approval_required", "Execution stopped: explicit plan approval is required.")
            return state
        approved = bool(approval_callback(plan))
        state.record("plan_approval", "Execution plan approved." if approved else "Execution plan rejected.",
                     approved=approved)
        if not approved:
            state.status = TaskStatus.FAILED
            return state

        if plan.security_level == "high_risk":
            if high_risk_approval_callback is None or not high_risk_approval_callback(plan):
                state.status = TaskStatus.FAILED
                state.record("high_risk_approval", "High-risk execution rejected.", approved=False)
                return state
            state.record("high_risk_approval", "High-risk execution approved.", approved=True)

        store = TaskStore(root)
        store.save(state, plan=plan, approved=True)
        state.record("task_persisted", "Approved task state persisted for resume.", path=str(store.path_for(task)))

        if git_is_repo(root):
            checkpoint = create_checkpoint(root, task)
            if checkpoint.returncode == 0:
                state.record("checkpoint_created", "Created post-approval Git checkpoint.",
                             branch=checkpoint.command, dirty=git_has_uncommitted_changes(root),
                             current_branch=git_current_branch(root).stdout.strip())
            else:
                state.record("checkpoint_error", "Could not create post-approval Git checkpoint.",
                             error=checkpoint.stderr.strip())
        else:
            state.record("checkpoint_skipped", "Project is not a Git repository.")

        context = self._build_context(root, task)
        from .orchestrator import Orchestrator, OrchestrationError
        orchestrator = Orchestrator(self.llm, self.settings)
        if orchestrator.should_decompose(plan, root):
            state.record("subtask_decomposition_started",
                         "Approved plan is large enough for logical subtask decomposition.")
            try:
                board = orchestrator.decompose(plan, root)
                store.save(state, plan=plan, board=board, approved=True)
                state.record("subtask_decomposed", "Approved plan decomposed into logical subtasks.",
                             board=board.as_dict())
                board = orchestrator.execute_approved_plan(root, task, plan, board,
                    progress_callback=lambda current: store.save(state, plan=plan, board=current, approved=True))
                state.record("subtask_execution_finished", "Logical subtask execution finished.",
                             board=board.as_dict())
                if not board.completed:
                    state.status = TaskStatus.FAILED
                    state.record("subtask_execution_failed", "At least one required subtask did not complete.")
                    return state
                verification = self.executor.verify(
                    root, security_required=plan.security_level in {"sensitive", "high_risk"}
                )
                state.record("verification", "Parent task verification executed.", verification=verification)
                if not verification["verified"]:
                    state.status = TaskStatus.FAILED
                    state.record("verification_failed", "Parent verification failed after subtask integration.")
                    return state
                state.status = TaskStatus.COMPLETED
                state.record("completed", "Task completed through logical subtasks and verified.",
                             subtask_board=board.as_dict(), verification=verification)
                return state
            except OrchestrationError as exc:
                state.record("subtask_decomposition_failed", str(exc))
                state.record("subtask_fallback", "Falling back to the single approved-plan executor.")

        return self.execute_approved_plan(root, task, plan, state, context=context,
                                          approval_callback=approval_callback,
                                          high_risk_approval_callback=high_risk_approval_callback,
                                          persistence_callback=lambda s, p: store.save(s, plan=p, approved=True))

    def resume(self, project_root: Path, task: str) -> TaskState:
        """Resume an approved persisted task without creating a new plan or approval gate."""
        root = project_root.resolve()
        store = TaskStore(root)
        state, plan, board = store.load(task)
        if state.status is TaskStatus.COMPLETED:
            return state
        state.status = TaskStatus.RUNNING
        state.record("resume_started", "Resuming previously approved persisted task.")
        if board is not None:
            from .orchestrator import Orchestrator
            orchestrator = Orchestrator(self.llm, self.settings)
            board = orchestrator.execute_approved_plan(
                root, task, plan, board,
                progress_callback=lambda current: store.save(state, plan=plan, board=current, approved=True),
            )
            state.record("subtask_execution_resumed", "Persisted logical subtask board resumed.",
                         board=board.as_dict())
            if board.completed:
                verification = self.executor.verify(
                    root, security_required=plan.security_level in {"sensitive", "high_risk"}
                )
                state.record("verification", "Parent task verification executed after resume.",
                             verification=verification)
                state.status = TaskStatus.COMPLETED if verification["verified"] else TaskStatus.FAILED
            else:
                state.status = TaskStatus.FAILED
        else:
            state = self.execute_approved_plan(
                root, task, plan, state,
                persistence_callback=lambda s, p: store.save(s, plan=p, approved=True),
            )
        store.save(state, plan=plan, board=board, approved=True)
        state.record("resume_finished", "Persisted task resume finished.", status=state.status.value)
        store.save(state, plan=plan, board=board, approved=True)
        return state

    def execute_approved_plan(
        self, project_root: Path, task: str, plan: ExecutionPlan, state: TaskState,
        *, context: dict | None = None, allowed_scope: tuple[str, ...] | None = None,
        max_iterations: int | None = None, allow_plan_changes: bool = True,
        approval_callback=None, high_risk_approval_callback=None, persistence_callback=None,
    ) -> TaskState:
        """Execute an already-approved plan without creating another plan or approval gate."""
        recovery_start_index = len(state.events)
        result = self.executor.execute(
            project_root, task, plan, state, context=context,
            allowed_scope=allowed_scope, max_iterations=max_iterations,
            allow_plan_changes=allow_plan_changes,
            approval_callback=approval_callback,
            high_risk_approval_callback=high_risk_approval_callback,
            persistence_callback=persistence_callback,
        )
        # Parent execution owns recovery when no logical subtask scope exists.
        # Subtasks are recovered by the orchestrator using their subtask ID.
        if result.status in {TaskStatus.FAILED, TaskStatus.MAX_ITERATIONS} and not (context or {}).get("subtask"):
            recovery = self.recovery.recover(project_root, result, min_event_index=recovery_start_index)
            result.record(
                "task_recovery",
                "Recovery attempted after approved-plan execution failure.",
                recovered=recovery.recovered,
                rolled_back_files=list(recovery.rolled_back_files),
                conflict_paths=list(recovery.conflict_paths),
            )
            if persistence_callback is not None:
                persistence_callback(result, plan)
        return result
