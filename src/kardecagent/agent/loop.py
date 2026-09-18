from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from ..project import discover_command, project_snapshot, detect_project, validate_project, scan_project
from ..project.dependencies import discover_audit_command, summarize_audit
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
    search_web,
    fetch_web_page,
)
from .state import TaskState, TaskStatus
from .plan import ExecutionPlan, PlanError, PlanTracker, parse_plan, plan_instructions
from .security import assess_security, security_requirements_for
from .security_review import SecurityReviewError, parse_security_review, security_review_instructions
from .tools_schema import ToolCallError, parse_tool_call, tool_instructions


SYSTEM_PROMPT = (
    "You are KardecAgent, a local software engineering agent. "
    "Work only inside the configured project. Never claim completion before "
    "verification. Prefer run_checks after changes."
)

CHECK_KINDS = ("test", "typecheck", "lint", "build", "validate", "dependency_audit")


class AgentLoop:
    def __init__(self, llm: LocalLLMClient, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    def _run_check(self, root: Path, kind: str) -> dict:
        if kind == "dependency_audit":
            discovered = discover_audit_command(root)
            if not discovered:
                return {"kind": kind, "available": False}
            ecosystem, command = discovered
            result = run_command(
                root, command,
                timeout=self.settings.command_timeout_seconds,
                max_output_chars=self.settings.max_command_output_chars,
            )
            audit = summarize_audit(
                ecosystem, result.stdout, result.stderr,
                result.returncode, result.timed_out,
            )
            return {
                "kind": kind, "available": audit.available,
                "ecosystem": ecosystem, "command": command,
                "passed": audit.passed, "summary": audit.summary,
                "output": audit.raw_output,
            }

        if kind == "security":
            result = scan_project(root)
            return {"kind": kind, "available": True, **result.as_dict()}

        if kind == "validate":
            profile = detect_project(root)
            if profile.kind != "static-html":
                return {"kind": kind, "available": False, "project_kind": profile.kind}
            result = validate_project(root, profile.kind)
            return {
                "kind": kind, "available": True,
                "project_kind": profile.kind, **result.as_dict(),
            }

        command = discover_command(root, kind)
        if not command:
            return {"kind": kind, "available": False}

        result = run_command(
            root, command,
            timeout=self.settings.command_timeout_seconds,
            max_output_chars=self.settings.max_command_output_chars,
        )
        return {
            "kind": kind, "available": True, "command": command,
            "returncode": result.returncode, "stdout": result.stdout,
            "stderr": result.stderr, "timed_out": result.timed_out,
            "passed": result.returncode == 0 and not result.timed_out,
        }

    def _verify_completion(self, root: Path, security_required: bool = False) -> dict:
        """Run all checks the project exposes and require every available check to pass."""
        checks = [self._run_check(root, kind) for kind in CHECK_KINDS]
        if security_required:
            checks.append(self._run_check(root, "security"))
            checks.append(self._run_check(root, "dependency_audit"))
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

    def _run_security_review(self, root: Path, task: str, plan: ExecutionPlan) -> dict:
        diff = git_diff(root).stdout if git_is_repo(root) else ""
        review_messages = [
            {"role": "system", "content": security_review_instructions()},
            {"role": "user", "content": json.dumps({
                "task": task,
                "security_level": plan.security_level,
                "security_requirements": plan.security_requirements,
                "changed_files_diff": diff,
                "instruction": "Review the current implementation and its diff. Return only the structured security review JSON.",
            }, ensure_ascii=False)},
        ]
        for _ in range(3):
            response = self.llm.chat(review_messages)
            try:
                review = parse_security_review(response.content)
                return review.as_dict()
            except SecurityReviewError as exc:
                review_messages += [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": "Invalid review: " + str(exc) + ". Return valid JSON only."},
                ]
        return {"status": "findings", "summary": "Security reviewer failed to produce a valid review.", "findings": [{
            "severity": "high", "category": "review_integrity",
            "message": "The independent security review could not be validated.",
            "recommendation": "Repeat the security review before completion.",
        }]}

    def _execute(self, root: Path, action) -> str:
        fs = ProjectFilesystem(root)
        args = action.arguments

        if action.tool == "list_files":
            return json.dumps(fs.list_files(args.get("limit", 500)), ensure_ascii=False)
        if action.tool == "read_file":
            return fs.read_file(args["path"])
        if action.tool == "search_web":
            return json.dumps(search_web(args["query"], max_results=args.get("max_results", 5), allow_domains=self.settings.web_allow_domains, deny_domains=self.settings.web_deny_domains), ensure_ascii=False)
        if action.tool == "fetch_web_page":
            return json.dumps(fetch_web_page(args["url"], max_chars=args.get("max_chars", 30000), allow_domains=self.settings.web_allow_domains, deny_domains=self.settings.web_deny_domains), ensure_ascii=False)
        if action.tool == "search_web":
            return json.dumps(search_web(args["query"], max_results=args.get("max_results", 5)), ensure_ascii=False)
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
        high_risk_approval_callback=None,
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
                SYSTEM_PROMPT + "
" + plan_instructions() +
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

        assessment = assess_security(task)
        state.record("security_assessment", "Task security sensitivity assessed.",
                     level=assessment.level, matched_signals=list(assessment.matched_signals))
        if assessment.sensitive:
            plan = ExecutionPlan(
                plan.summary, plan.steps, plan.validation, plan.risks,
                plan.completion_criteria, assessment.level,
                security_requirements_for(assessment.level),
            )
            state.record("security_mode_enabled",
                         "Security-sensitive execution controls enabled.",
                         level=assessment.level,
                         requirements=plan.security_requirements)

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
            if high_risk_approval_callback is None:
                state.status = TaskStatus.FAILED
                state.record("high_risk_approval_required",
                             "High-risk execution requires a separate explicit approval.")
                return state
            security_approved = bool(high_risk_approval_callback(plan))
            state.record("high_risk_approval",
                         "High-risk execution approved." if security_approved else "High-risk execution rejected.",
                         approved=security_approved)
            if not security_approved:
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
                SYSTEM_PROMPT + "
" + tool_instructions() +
                "
The following execution plan was explicitly approved by the user. "
                "Follow it. If the plan becomes impossible or a requirement changes, stop and report it."
            )},
            {"role": "user", "content": json.dumps({
                **context,
                "approved_plan": plan.as_dict(),
                "completion_criteria": plan.completion_criteria,
                "security_level": plan.security_level,
                "security_requirements": plan.security_requirements,
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
                    required_level = assess_security(task).level
                    rank = {"normal": 0, "low_risk": 1, "sensitive": 2, "high_risk": 3}
                    if rank[proposed_plan.security_level] < rank[required_level]:
                        raise PlanError(
                            "replacement plan security level cannot be lower than task-required level: "
                            + required_level
                        )
                    if required_level in {"sensitive", "high_risk"} and not proposed_plan.security_requirements:
                        proposed_plan = ExecutionPlan(
                            proposed_plan.summary, proposed_plan.steps, proposed_plan.validation,
                            proposed_plan.risks, proposed_plan.completion_criteria,
                            required_level, security_requirements_for(required_level),
                        )
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

                if changed and proposed_plan.security_level == "high_risk":
                    if high_risk_approval_callback is None:
                        state.status = TaskStatus.FAILED
                        state.record("high_risk_approval_required", "Elevated replacement plan requires a second explicit approval.")
                        return state
                    changed = bool(high_risk_approval_callback(proposed_plan))
                    state.record(
                        "high_risk_plan_change_approval",
                        "High-risk replacement plan approved." if changed else "High-risk replacement plan rejected.",
                        approved=changed,
                    )

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

                verification = self._verify_completion(project_root, plan.security_level in {"sensitive", "high_risk"})
                if verification["verified"] and plan.security_level in {"sensitive", "high_risk"}:
                    security_review = self._run_security_review(project_root, task, plan)
                    verification["security_review"] = security_review
                    state.record("security_review", "Independent security review executed.", review=security_review)
                    if security_review["status"] != "pass" or security_review["findings"]:
                        verification["verified"] = False
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
