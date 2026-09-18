from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from ..project import discover_command, project_snapshot, detect_project, validate_project
from ..llm import LocalLLMClient
from ..tools import (
    ProjectFilesystem,
    create_checkpoint,
    git_current_branch,
    git_diff,
    git_has_uncommitted_changes,
    git_is_repo,
    git_log,
    git_status,
    run_command,
    search_text,
)
from .state import TaskState, TaskStatus
from .plan import ExecutionPlan, PlanError, PlanTracker, parse_plan, plan_instructions
from .tools_schema import ToolCallError, parse_tool_call, tool_instructions


SYSTEM_PROMPT = (
    "You are KardecAgent, a local software engineering agent. "
    "Work only inside the configured project. Never claim completion before "
    "verification. Prefer run_checks after changes."
)

CHECK_KINDS = ("test", "typecheck", "lint", "build", "validate")


class AgentLoop:
    def __init__(self, llm: LocalLLMClient, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    def _run_check(self, root: Path, kind: str) -> dict:
        if kind == "validate":
            profile = detect_project(root)
            if profile.kind != "static-html":
                return {"kind": kind, "available": False, "project_kind": profile.kind}
            result = validate_project(root, profile.kind)
            return {
                "kind": kind,
                "available": True,
                "project_kind": profile.kind,
                **result.as_dict(),
            }

        command = discover_command(root, kind)
        if not command:
            return {"kind": kind, "available": False}

        result = run_command(
            root,
            command,
            timeout=self.settings.command_timeout_seconds,
            max_output_chars=self.settings.max_command_output_chars,
        )
        return {
            "kind": kind,
            "available": True,
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
            "passed": result.returncode == 0 and not result.timed_out,
        }

    def _verify_completion(self, root: Path) -> dict:
        """Run all checks the project exposes and require every available check to pass."""
        checks = [self._run_check(root, kind) for kind in CHECK_KINDS]
        available = [check for check in checks if check["available"]]
        failures = [check for check in available if not check["passed"]]

        verification = {
            "verified": bool(available) and not failures,
            "checks": checks,
            "git": None,
        }

        if git_is_repo(root):
            status = git_status(root)
            diff = git_diff(root)
            verification["git"] = {
                "status_returncode": status.returncode,
                "status": status.stdout,
                "diff_returncode": diff.returncode,
                "diff": diff.stdout,
                "uncommitted_changes": git_has_uncommitted_changes(root),
            }

        return verification

    def _execute(self, root: Path, action) -> str:
        fs = ProjectFilesystem(root)
        args = action.arguments

        if action.tool == "list_files":
            return json.dumps(fs.list_files(args.get("limit", 500)), ensure_ascii=False)
        if action.tool == "read_file":
            return fs.read_file(args["path"])
        if action.tool == "search_files":
            return json.dumps(
                search_text(root, args["query"], args.get("max_results", 50)),
                ensure_ascii=False,
            )
        if action.tool == "write_file":
            fs.write_file(args["path"], args["content"])
            return json.dumps({"ok": True, "path": args["path"]})
        if action.tool == "run_command":
            result = run_command(
                root,
                args["command"],
                timeout=self.settings.command_timeout_seconds,
                max_output_chars=self.settings.max_command_output_chars,
            )
            return json.dumps(result.__dict__, ensure_ascii=False)
        if action.tool == "git_status":
            return json.dumps(git_status(root).__dict__, ensure_ascii=False)
        if action.tool == "git_diff":
            return json.dumps(git_diff(root).__dict__, ensure_ascii=False)
        if action.tool == "git_log":
            return json.dumps(
                git_log(root, args.get("limit", 10)).__dict__,
                ensure_ascii=False,
            )
        if action.tool == "run_checks":
            result = self._run_check(root, args["kind"])
            return json.dumps(result, ensure_ascii=False)
        raise ToolCallError("unsupported tool: " + action.tool)

    def run(
        self,
        project_root: Path,
        task: str,
        *,
        approval_callback=None,
    ) -> TaskState:
        """Create a plan, wait for explicit approval, then execute it.

        approval_callback receives ExecutionPlan and must return True to approve.
        If omitted, the loop is non-interactive and requires explicit approval
        from the caller through the CLI layer.
        """
        state = TaskState(task, str(project_root))
        state.status = TaskStatus.RUNNING

        profile = detect_project(project_root)
        state.record("project_detected", f"Detected project kind: {profile.kind}.",
                     kind=profile.kind, language=profile.language,
                     framework=profile.framework, package_manager=profile.package_manager,
                     commands=profile.commands)

        context = {
            "task": task,
            "project": {
                "kind": profile.kind,
                "language": profile.language,
                "framework": profile.framework,
                "package_manager": profile.package_manager,
                "commands": profile.commands,
            },
            "project_files": project_snapshot(project_root),
        }

        plan_messages = [
            {"role": "system", "content": (
                SYSTEM_PROMPT + "\n" + plan_instructions() +
                " You are in the planning phase. Do not call implementation tools."
            )},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]

        plan = None
        for _ in range(min(5, self.settings.max_iterations)):
            response = self.llm.chat(plan_messages)
            state.record("plan_response", response.content)
            try:
                plan = parse_plan(response.content)
                break
            except PlanError as exc:
                state.record("plan_error", str(exc))
                plan_messages += [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": "Invalid plan: " + str(exc) + ". " + plan_instructions()},
                ]

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

        if git_is_repo(project_root):
            checkpoint = create_checkpoint(project_root, task)
            if checkpoint.returncode == 0:
                state.record("checkpoint_created", "Created post-approval Git checkpoint.",
                             branch=checkpoint.command,
                             dirty=git_has_uncommitted_changes(project_root),
                             current_branch=git_current_branch(project_root).stdout.strip())
            else:
                state.record("checkpoint_error", "Could not create post-approval Git checkpoint.",
                             error=checkpoint.stderr.strip())
        else:
            state.record("checkpoint_skipped", "Project is not a Git repository.")

        tracker = PlanTracker(plan)
        state.record("plan_progress", "Plan execution initialized.", progress=tracker.as_dict())
        state.record("execution_started", "Approved plan execution started.")

        messages = [
            {"role": "system", "content": (
                SYSTEM_PROMPT + "\n" + tool_instructions() +
                "\nThe following execution plan was explicitly approved by the user. "
                "Follow it. If the plan becomes impossible or a requirement changes, stop and report it."
            )},
            {"role": "user", "content": json.dumps({
                **context,
                "approved_plan": plan.as_dict(),
                "completion_criteria": plan.completion_criteria,
                "instruction": "Execute the approved plan step by step. Mutating actions must name the active plan_step. After each step, call complete_step with evidence. Do not deviate without asking for approval.",
                "plan_progress": tracker.as_dict(),
            }, ensure_ascii=False)},
        ]

        for iteration in range(1, self.settings.max_iterations + 1):
            state.iteration = iteration
            state.record("iteration_started", f"Starting iteration {iteration}.")
            response = self.llm.chat(messages)
            state.record("model_response", response.content)

            try:
                action = parse_tool_call(response.content)
            except ToolCallError as exc:
                state.record("model_error", str(exc))
                messages += [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": "Invalid tool call: " + str(exc) + ". " + tool_instructions()},
                ]
                continue

            if action.tool == "request_plan_change":
                try:
                    proposed_plan = parse_plan(json.dumps(action.arguments["plan"], ensure_ascii=False))
                except PlanError as exc:
                    state.record("plan_change_error", str(exc))
                    messages += [
                        {"role": "assistant", "content": response.content},
                        {"role": "user", "content": "Invalid proposed plan: " + str(exc)},
                    ]
                    continue

                state.record("plan_change_proposed", "Model proposed a replacement execution plan.",
                             previous_plan=plan.as_dict(), proposed_plan=proposed_plan.as_dict())

                if approval_callback is None:
                    state.status = TaskStatus.FAILED
                    state.record("approval_required", "Plan change requires explicit user approval.")
                    return state

                changed = bool(approval_callback(proposed_plan))
                state.record("plan_change_approval",
                             "Replacement plan approved." if changed else "Replacement plan rejected.",
                             approved=changed)

                if not changed:
                    messages += [
                        {"role": "assistant", "content": response.content},
                        {"role": "user", "content": json.dumps({
                            "plan_change": "rejected",
                            "instruction": "Continue using the currently approved plan. Do not deviate.",
                            "plan_progress": tracker.as_dict(),
                        }, ensure_ascii=False)},
                    ]
                    continue

                plan = proposed_plan
                tracker = PlanTracker(plan)
                state.record("plan_changed", "Approved replacement plan is now active.",
                             plan=plan.as_dict(), progress=tracker.as_dict())
                messages += [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": json.dumps({
                        "plan_change": "approved",
                        "approved_plan": plan.as_dict(),
                        "plan_progress": tracker.as_dict(),
                        "instruction": "Use only the newly approved plan. Start at its active step.",
                    }, ensure_ascii=False)},
                ]
                continue

            if action.tool in {"write_file", "run_command", "run_checks", "complete_step"}:
                if action.plan_step != tracker.current_step:
                    state.record("plan_scope_violation", "Action rejected: outside active approved plan step.",
                                 tool=action.tool, requested_step=action.plan_step,
                                 active_step=tracker.current_step)
                    messages += [
                        {"role": "assistant", "content": response.content},
                        {"role": "user", "content": json.dumps({
                            "error": "plan_scope_violation",
                            "active_plan_step": tracker.current_step,
                            "requested_plan_step": action.plan_step,
                            "instruction": "Use only the active plan_step and complete it before moving on.",
                        }, ensure_ascii=False)},
                    ]
                    continue
                if action.plan_step not in tracker.started_steps:
                    tracker.start(action.plan_step)
                    state.record("plan_step_started", f"Started plan step {action.plan_step}.",
                                 step=action.plan_step, description=plan.steps[action.plan_step - 1])

            if action.tool == "complete_step":
                step = action.arguments["step"]
                if step != action.plan_step or step != tracker.current_step:
                    state.record("plan_scope_violation", "Step completion rejected: wrong active plan step.",
                                 requested_step=step, active_step=tracker.current_step)
                    continue
                tracker.complete(step)
                state.record("plan_step_completed", f"Completed plan step {step}.",
                             step=step, evidence=action.arguments["evidence"], progress=tracker.as_dict())
                messages += [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": json.dumps({
                        "plan_progress": tracker.as_dict(),
                        "instruction": "Continue with the new active plan step.",
                    }, ensure_ascii=False)},
                ]
                continue

            if action.tool == "finish":
                if not tracker.completed:
                    state.record("plan_scope_violation", "Finish rejected: approved plan has incomplete steps.",
                                 progress=tracker.as_dict())
                    messages += [
                        {"role": "assistant", "content": response.content},
                        {"role": "user", "content": json.dumps({
                            "error": "plan_incomplete",
                            "plan_progress": tracker.as_dict(),
                            "instruction": "Complete every approved plan step before finishing.",
                        }, ensure_ascii=False)},
                    ]
                    continue

                evidence = action.arguments["criteria_evidence"]
                criteria_count = len(plan.completion_criteria)
                if len(evidence) != criteria_count:
                    state.record("completion_criteria_failed", "Finish rejected: evidence must cover every completion criterion.",
                                 expected=criteria_count, received=len(evidence))
                    messages += [
                        {"role": "assistant", "content": response.content},
                        {"role": "user", "content": json.dumps({
                            "error": "completion_criteria_incomplete",
                            "completion_criteria": plan.completion_criteria,
                            "received_evidence": len(evidence),
                            "instruction": "Provide exactly one concrete evidence item for every completion criterion before finishing.",
                        }, ensure_ascii=False)},
                    ]
                    continue

                verification = self._verify_completion(project_root)
                verification["completion_criteria"] = [
                    {"criterion": criterion, "evidence": evidence[index]}
                    for index, criterion in enumerate(plan.completion_criteria)
                ]
                state.record("verification", "Completion verification executed.",
                             verified=verification["verified"], checks=verification["checks"],
                             completion_criteria=verification["completion_criteria"])
                if verification["verified"]:
                    state.status = TaskStatus.COMPLETED
                    state.record("completed", action.arguments["reason"],
                                 completion_criteria=verification["completion_criteria"])
                    return state
                state.record("verification_failed", "Completion was rejected because verification did not pass.")
                messages += [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": json.dumps({
                        "verification": verification,
                        "instruction": "Do not finish yet. Diagnose failed checks, make necessary changes, and run checks again.",
                    }, ensure_ascii=False)},
                ]
                continue

            try:
                result = self._execute(project_root, action)
            except Exception as exc:
                result = json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)},
                                    ensure_ascii=False)

            state.record("tool_result", result, tool=action.tool, plan_step=action.plan_step)
            messages += [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": json.dumps({"tool_result": result}, ensure_ascii=False)},
            ]

        state.status = TaskStatus.MAX_ITERATIONS
        state.record("max_iterations", "Maximum agent iterations reached.")
        return state
