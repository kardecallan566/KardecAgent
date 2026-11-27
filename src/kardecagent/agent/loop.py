from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from ..llm.router import ChatClient
from ..project import detect_project, project_snapshot
from ..tools import create_checkpoint, git_current_branch, git_has_uncommitted_changes, git_is_repo
from .executor import AgentExecutor
from .plan import ExecutionPlan, PlanError, parse_plan, plan_instructions
from .security import assess_security, security_requirements_for
from .state import TaskState, TaskStatus
from .persistence import TaskStore, PersistenceError
from .recovery import RecoveryManager
from .diagnostics import diagnose_failure
from .retry_policy import RetryPolicy
from .tools_schema import ToolCallError
from .project_brief import build_project_brief, project_brief_instructions
from .project_memory import ProjectMemoryStore
from .skills import SkillCatalog, SkillError
from .skill_orchestrator import SkillOrchestrator
from ..llm.cleaner import extract_json_payload
from ..harness import load_registry
from ..harness.registry import compose_system_prompt

SYSTEM_PROMPT = (
    "You are KardecAgent, a local software engineering agent. "
    "Work only inside the configured project. Never claim completion before verification. "
    "Use an evidence-first, minimal-change workflow: inspect only files and runtime facts relevant to the reported symptom; "
    "do not install, upgrade, remove, or download dependencies unless a concrete missing/incompatible dependency is proven, "
    "the change is necessary for the approved objective, and the user has approved that dependency change. "
    "Prefer existing project tools, installed packages, lockfiles, and documented commands. Never run broad environment repairs. "
    "Do not create diagnostic files, reports, logs, or test scaffolding unless they directly verify the requested behavior; "
    "keep diagnostics in the execution journal and summarize only actionable evidence. "
    "Bound investigation: formulate a testable hypothesis, run the smallest discriminating check, and change strategy after a repeated failure. "
    "Never repeat an identical command/check without a reason; after two unsuccessful attempts at the same hypothesis, stop and report the blocker. "
    "Do not recursively investigate unrelated warnings or speculative causes. If evidence is insufficient, ask for the missing information instead of guessing."
)

class AgentLoop:
    """Public task workflow: inspect, plan, obtain approval, then execute."""

    def __init__(self, llm: ChatClient, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings
        self.executor = AgentExecutor(llm, settings)
        self.recovery = RecoveryManager()
        self.retry_policy = RetryPolicy()

    def _build_context(self, root: Path, task: str, conversation_history: list[dict] | None = None, active_editor_context: dict | None = None) -> dict:
        profile = detect_project(root)
        files = project_snapshot(root, limit=500)
        brief = build_project_brief(task, profile, files)
        project_memory = ProjectMemoryStore(root).load()
        return {
            "task": task,
            "project_brief": brief.as_dict(),
            "project_memory": project_memory,
            "project": {
                "kind": profile.kind, "language": profile.language,
                "framework": profile.framework, "package_manager": profile.package_manager,
                "commands": profile.commands,
            },
            "project_files": files,
            "workspace_access": {"root": str(root), "tools": ["list_files", "read_file", "search_files", "write_file", "append_file", "replace_in_file", "create_directory", "apply_patch", "run_command", "git_status", "git_diff", "git_log", "git_branches", "git_remotes", "git_fetch", "git_pull", "git_push", "git_create_branch", "git_checkout", "git_commit", "git_merge", "git_create_pr", "git_pr_status", "git_merge_pr"], "path_rule": "All tool paths are relative to this workspace root; list_files accepts an optional relative directory path."},
            "conversation_history": conversation_history or [],
            "active_editor": active_editor_context or None,
            "available_skills": self._available_skills(root),
        }

    @staticmethod
    def _available_skills(root: Path) -> list[dict]:
        try:
            return SkillCatalog(root).list_available()
        except SkillError:
            # An invalid optional package must not block ordinary project tasks.
            return []

    def _create_plan(self, root: Path, task: str, state: TaskState, conversation_history: list[dict] | None = None, active_editor_context: dict | None = None) -> ExecutionPlan | None:
        context = self._build_context(root, task, conversation_history, active_editor_context)
        base_messages = [
            {"role": "system", "content": compose_system_prompt(
                SYSTEM_PROMPT, load_registry(root / ".kardecagent" / "harness.json"),
                phase=plan_instructions() + project_brief_instructions(
                    build_project_brief(task, detect_project(root), context["project_files"])
                ) + " You are in the planning phase. Do not call implementation tools.",
            )},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]
        messages = list(base_messages)
        for _ in range(min(2, self.settings.max_iterations)):
            response = self.llm.chat(messages)
            state.record("plan_response", response.content)
            plan = None
            try:
                plan = parse_plan(response.content)
            except PlanError as exc:
                if "completion_criteria" in str(exc):
                    try:
                        raw_payload = extract_json_payload(response.content)
                        if isinstance(raw_payload, dict) and raw_payload.get("summary") and raw_payload.get("steps"):
                            synth_criteria = raw_payload.get("validation") or [raw_payload["summary"]]
                            if isinstance(synth_criteria, list) and synth_criteria:
                                raw_payload["completion_criteria"] = synth_criteria
                                plan = parse_plan(json.dumps(raw_payload, ensure_ascii=False))
                    except Exception:
                        pass
                if plan is None:
                    state.record("plan_error", str(exc))
                    messages = base_messages + [
                        {"role": "assistant", "content": response.content},
                        {"role": "user", "content": "Previous output invalid. Return concise JSON with at most 8 distinct steps; do not pad or repeat. Error: " + str(exc) + ". " + plan_instructions()},
                    ]
                    continue

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
        return None

    def run(
        self,
        project_root: Path,
        task: str,
        *,
        approval_callback=None,
        high_risk_approval_callback=None,
        event_callback=None,
        conversation_history: list[dict] | None = None,
        active_editor_context: dict | None = None,
        model_switch_approval_callback=None,
        skill_approval_callback=None,
    ) -> TaskState:
        root = project_root.resolve()
        setter = getattr(self.llm, "set_approval_callback", None)
        if callable(setter):
            setter(model_switch_approval_callback)
        state = TaskState(task, str(root), event_callback=event_callback)
        state.transition(TaskStatus.RUNNING, reason="Task execution initialized.")
        profile = detect_project(root)
        state.record("project_detected", f"Detected project kind: {profile.kind}.",
                     kind=profile.kind, language=profile.language, framework=profile.framework,
                     package_manager=profile.package_manager, commands=profile.commands)
        brief = build_project_brief(task, profile, project_snapshot(root, limit=500))
        state.record(
            "project_brief_created",
            "Project delivery scope classified for planning and quality assurance.",
            brief=brief.as_dict(),
        )

        plan = self._create_plan(root, task, state, conversation_history, active_editor_context)
        if plan is None:
            state.transition(TaskStatus.FAILED, reason="Could not produce a valid execution plan.")
            state.record("plan_failed", "Could not produce a valid execution plan.")
            return state
        state.record("plan_created", "Execution plan created.", plan=plan.as_dict())

        if approval_callback is None:
            state.transition(TaskStatus.FAILED, reason="Execution stopped: explicit plan approval is required.")
            state.record("approval_required", "Execution stopped: explicit plan approval is required.")
            return state
        approved = bool(approval_callback(plan))
        state.record("plan_approval", "Execution plan approved." if approved else "Execution plan rejected.",
                     approved=approved)
        if not approved:
            state.transition(TaskStatus.FAILED, reason="Execution plan was rejected.")
            return state

        if plan.security_level == "high_risk":
            if high_risk_approval_callback is None or not high_risk_approval_callback(plan):
                state.transition(TaskStatus.FAILED, reason="High-risk execution rejected.")
                state.record("high_risk_approval", "High-risk execution rejected.", approved=False)
                return state
            state.record("high_risk_approval", "High-risk execution approved.", approved=True)

        skill_preparation = None
        try:
            skill_preparation = SkillOrchestrator(SkillCatalog(root)).prepare(
                task, approval_callback=skill_approval_callback
            )
            if skill_preparation.candidate_ids:
                state.record(
                    "skills_selected",
                    "Relevant skills were selected from the project catalog.",
                    selection=skill_preparation.as_dict(),
                )
                if skill_preparation.approval_status == "approval_required":
                    state.record(
                        "skill_approval_required",
                        "Skill activation is waiting for explicit user approval.",
                        skill_ids=list(skill_preparation.candidate_ids),
                    )
                elif skill_preparation.approval_status == "rejected":
                    state.record(
                        "skill_approval_rejected",
                        "User declined the selected skills; execution continues without them.",
                        skill_ids=list(skill_preparation.candidate_ids),
                    )
                elif skill_preparation.approval_status == "approved":
                    state.record(
                        "skills_loaded",
                        "Approved skills were loaded as untrusted reference instructions.",
                        skill_ids=list(skill_preparation.loaded_ids),
                    )
        except SkillError as exc:
            state.record(
                "skills_unavailable",
                "Skill catalog could not be used; continuing without skills.",
                error=str(exc),
            )

        memory_path = ProjectMemoryStore(root).record_approved_plan(
            task, brief.as_dict(), plan
        )
        state.record(
            "project_memory_updated",
            "Approved project requirements and acceptance criteria were persisted.",
            path=str(memory_path),
        )

        store = TaskStore(root)
        state.record("task_persisted", "Approved task state is being durably persisted for resume.", path=str(store.path_for(task)), journal_path=str(store.journal_path_for(task)))
        store.save(state, plan=plan, approved=True)

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

        context = self._build_context(root, task, conversation_history, active_editor_context)
        if skill_preparation is not None and skill_preparation.prompt_sections:
            context["skill_prompt_sections"] = list(skill_preparation.prompt_sections)
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
                    progress_callback=lambda current: store.save(state, plan=plan, board=current, approved=True),
                    parent_state=state)
                state.record("subtask_execution_finished", "Logical subtask execution finished.",
                             board=board.as_dict())
                if not board.completed:
                    state.transition(TaskStatus.FAILED, reason="At least one required subtask did not complete.")
                    state.record("subtask_execution_failed", "At least one required subtask did not complete.")
                    return state
                state.transition(TaskStatus.VERIFYING, reason="Parent verification after subtask execution started.")
                verification = self.executor.verify(
                    root, security_required=plan.security_level in {"sensitive", "high_risk"}
                )
                state.record("verification", "Parent task verification executed.", verification=verification)
                if not verification["verified"]:
                    state.transition(TaskStatus.FAILED, reason="Parent verification failed after subtask integration.")
                    state.record("verification_failed", "Parent verification failed after subtask integration.")
                    return state
                state.transition(TaskStatus.VERIFIED, reason="Parent verification after subtask execution passed.")
                state.transition(TaskStatus.COMPLETED, reason="Task completed through logical subtasks and verified.")
                state.record("completed", "Task completed through logical subtasks and verified.",
                             subtask_board=board.as_dict(), verification=verification)
                if skill_preparation is not None and skill_preparation.loaded_ids:
                    state.record(
                        "skill_outcome",
                        "Skill use was evaluated against the verified task result.",
                        **SkillOrchestrator.outcome(skill_preparation, success=True),
                    )
                return state
            except OrchestrationError as exc:
                state.record("subtask_decomposition_failed", str(exc))
                state.record("subtask_fallback", "Falling back to the single approved-plan executor.")

        result = self.execute_approved_plan(root, task, plan, state, context=context,
                                          approval_callback=approval_callback,
                                          high_risk_approval_callback=high_risk_approval_callback,
                                          persistence_callback=lambda s, p: store.save(s, plan=p, approved=True))
        if skill_preparation is not None and skill_preparation.loaded_ids:
            result.record(
                "skill_outcome",
                "Skill use was evaluated against the task verification result.",
                **SkillOrchestrator.outcome(
                    skill_preparation,
                    success=result.status in {TaskStatus.VERIFIED, TaskStatus.COMPLETED},
                ),
            )
        return result

    def resume(self, project_root: Path, task: str, *, event_callback=None,
               model_switch_approval_callback=None) -> TaskState:
        """Resume an approved persisted task without creating a new plan or approval gate."""
        root = project_root.resolve()
        setter = getattr(self.llm, "set_approval_callback", None)
        if callable(setter):
            setter(model_switch_approval_callback)
        store = TaskStore(root)
        state, plan, board = store.load(task)
        state.event_callback = event_callback
        if state.status is TaskStatus.COMPLETED:
            return state

        if state.status is TaskStatus.RECOVERING:
            recovery = self.recovery.resume_pending_recovery(
                root,
                state,
                persistence_callback=lambda s: store.save(
                    s, plan=plan, board=board, approved=True
                ),
            )
            if not recovery.recovered:
                store.save(state, plan=plan, board=board, approved=True)
                return state
        elif state.status in {TaskStatus.FAILED, TaskStatus.MAX_ITERATIONS}:
            recovery = self.recovery.recover(
                root,
                state,
                persistence_callback=lambda s: store.save(
                    s, plan=plan, board=board, approved=True
                ),
            )
            if not recovery.recovered:
                store.save(state, plan=plan, board=board, approved=True)
                return state
        elif state.status is TaskStatus.VERIFYING:
            state.transition(TaskStatus.RUNNING, reason="Resuming after interrupted verification.")
        elif state.status is TaskStatus.PENDING:
            state.transition(TaskStatus.RUNNING, reason="Persisted task had not started.")
        elif state.status is not TaskStatus.RUNNING and state.status is not TaskStatus.RESUMING:
            raise PersistenceError(f"cannot resume task from status {state.status.value}")

        state.record("resume_started", "Resuming previously approved persisted task.")
        if board is not None:
            recovered_subtasks = board.recover_interrupted()
            if recovered_subtasks:
                state.record(
                    "subtasks_recovered",
                    "Interrupted parallel subtasks were returned to the scheduler with their individual cursors preserved.",
                    subtask_ids=recovered_subtasks,
                    board=board.as_dict(),
                )
                store.save(state, plan=plan, board=board, approved=True)
            from .orchestrator import Orchestrator
            orchestrator = Orchestrator(self.llm, self.settings)
            board = orchestrator.execute_approved_plan(
                root, task, plan, board,
                progress_callback=lambda current: store.save(state, plan=plan, board=current, approved=True),
                parent_state=state,
                resume_subtask_id=state.active_subtask_id,
                resume_step=state.active_subtask_step,
            )
            state.record("subtask_execution_resumed", "Persisted logical subtask board resumed.",
                         board=board.as_dict())
            if board.completed:
                state.transition(TaskStatus.VERIFYING, reason="Parent verification after resume started.")
                verification = self.executor.verify(
                    root, security_required=plan.security_level in {"sensitive", "high_risk"}
                )
                state.record("verification", "Parent task verification executed after resume.",
                             verification=verification)
                if verification["verified"]:
                    state.transition(TaskStatus.VERIFIED, reason="Parent verification after resume passed.")
                    state.transition(TaskStatus.COMPLETED, reason="Persisted task resume completed.")
                else:
                    state.transition(TaskStatus.FAILED, reason="Parent verification after resume failed.")
            else:
                state.transition(TaskStatus.FAILED, reason="Persisted logical subtask resume did not complete.")
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
        resume_step: int = 1,
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
            resume_step=resume_step,
        )
        if result.status in {TaskStatus.FAILED, TaskStatus.MAX_ITERATIONS} and not (context or {}).get("subtask"):
            failure_status = result.status.value
            failure = diagnose_failure(
                result.events[recovery_start_index:],
                failure_status,
                fallback_message=f"Execution stopped with status {failure_status}.",
            )
            recovery = self.recovery.recover(
                project_root,
                result,
                min_event_index=recovery_start_index,
                persistence_callback=(
                    (lambda s: persistence_callback(s, plan))
                    if persistence_callback is not None else None
                ),
            )
            result.record(
                "task_recovery",
                "Recovery attempted after approved-plan execution failure.",
                recovered=recovery.recovered,
                resume_step=recovery.resume_step,
                rolled_back_files=list(recovery.rolled_back_files),
                conflict_paths=list(recovery.conflict_paths),
            )
            decision = self.retry_policy.decide(
                events=result.events,
                retry_event="resume_retry_started",
                status=failure_status,
                diagnostic=failure,
                recovery=recovery,
                base_iteration_limit=max_iterations or self.settings.max_iterations,
                max_retries=self.settings.max_retries,
                execution_events=result.events[recovery_start_index:],
            )
            result.record(
                "retry_decision",
                "Bounded retry policy evaluated the recovered approved plan.",
                allowed=decision.allowed,
                reason=decision.reason,
                attempt=decision.attempt,
                iteration_limit=decision.iteration_limit,
                failure_category=failure.category.value,
            )
            if persistence_callback is not None:
                persistence_callback(result, plan)
            if decision.allowed:
                result.record(
                    "resume_retry_started",
                    "Retrying the approved plan from the last consistent plan step.",
                    resume_step=recovery.resume_step,
                    attempt=decision.attempt,
                    iteration_limit=decision.iteration_limit,
                )
                retry_start_index = len(result.events)
                result = self.executor.execute(
                    project_root, task, plan, result, context=context,
                    allowed_scope=allowed_scope, max_iterations=decision.iteration_limit,
                    allow_plan_changes=allow_plan_changes,
                    approval_callback=approval_callback,
                    high_risk_approval_callback=high_risk_approval_callback,
                    persistence_callback=persistence_callback,
                    resume_step=recovery.resume_step,
                )
                result.record(
                    "resume_retry_finished",
                    "Approved-plan retry finished without requesting a new approval.",
                    resume_step=recovery.resume_step,
                    status=result.status.value,
                    events_from_index=retry_start_index,
                )
                if persistence_callback is not None:
                    persistence_callback(result, plan)
            else:
                if result.status is TaskStatus.RESUMING:
                    result.transition(
                        TaskStatus.FAILED,
                        reason="Automatic retry was denied: " + decision.reason,
                    )
                if persistence_callback is not None:
                    persistence_callback(result, plan)
        return result
