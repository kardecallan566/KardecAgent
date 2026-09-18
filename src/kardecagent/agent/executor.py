from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..project import detect_project, discover_command, project_snapshot, scan_project, validate_project
from ..project.dependencies import discover_audit_command, summarize_audit
from ..project.test_results import format_for_model, parse_test_result
from ..tools import (
    ProjectFilesystem, apply_unified_patch, git_diff, git_has_uncommitted_changes,
    git_is_repo, git_log, git_status, git_changed_paths, git_changed_fingerprints, run_command, search_text, search_web,
    fetch_web_page,
    WorkspaceSnapshot, git_has_rename_or_copy, rollback_file_change,
)
from .plan import ExecutionPlan, PlanError, PlanTracker, parse_plan
from .security import assess_security, security_requirements_for
from .security_review import (
    SecurityReviewError, parse_security_review, security_review_instructions,
)
from .state import TaskState, TaskStatus
from .tools_schema import ToolCallError, parse_tool_call, tool_instructions

CHECK_KINDS = ("test", "typecheck", "lint", "build", "validate", "dependency_audit")
RISK_RANK = {"normal": 0, "low_risk": 1, "sensitive": 2, "high_risk": 3}

class AgentExecutor:
    """Executes an already-approved plan with one reusable LLM instance."""

    def __init__(self, llm, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    def _scope_allows(self, root: Path, raw_path: str, scope: tuple[str, ...] | None) -> bool:
        if not scope:
            return True
        path = Path(raw_path)
        if path.is_absolute() or ".." in path.parts:
            return False
        candidate = (root / path).resolve()
        try:
            rel = candidate.relative_to(root).as_posix()
        except ValueError:
            return False
        for allowed in scope:
            base = Path(allowed)
            if base == Path("."):
                return True
            allowed_abs = (root / base).resolve()
            try:
                rel_allowed = allowed_abs.relative_to(root).as_posix()
            except ValueError:
                continue
            if rel == rel_allowed or rel.startswith(rel_allowed.rstrip("/") + "/"):
                return True
        return False

    def _check_scope(self, root: Path, paths: list[str], scope: tuple[str, ...] | None) -> None:
        for path in paths:
            if not self._scope_allows(root, path, scope):
                raise ValueError(f"subtask scope violation: {path}")

    def _run_check(self, root: Path, kind: str) -> dict:
        if kind == "security":
            result = scan_project(root)
            return {"kind": kind, "available": True, **result.as_dict()}
        if kind == "dependency_audit":
            discovered = discover_audit_command(root)
            if not discovered:
                return {"kind": kind, "available": False}
            ecosystem, command = discovered
            result = run_command(root, command, timeout=self.settings.command_timeout_seconds,
                                 max_output_chars=self.settings.max_command_output_chars)
            audit = summarize_audit(ecosystem, result.stdout, result.stderr,
                                    result.returncode, result.timed_out)
            return {"kind": kind, "available": audit.available, "ecosystem": ecosystem,
                    "command": command, "passed": audit.passed, "summary": audit.summary,
                    "output": audit.raw_output}
        if kind == "validate":
            profile = detect_project(root)
            if profile.kind != "static-html":
                return {"kind": kind, "available": False, "project_kind": profile.kind}
            result = validate_project(root, profile.kind)
            return {"kind": kind, "available": True, "project_kind": profile.kind,
                    **result.as_dict()}
        command = discover_command(root, kind)
        if not command:
            return {"kind": kind, "available": False}
        result = run_command(root, command, timeout=self.settings.command_timeout_seconds,
                                 max_output_chars=self.settings.max_command_output_chars)
        parsed = parse_test_result(kind, result.stdout, result.stderr,
                                   result.returncode, result.timed_out)
        return {"kind": kind, "available": parsed.available, "command": command,
                **format_for_model(parsed)}

    def verify(self, root: Path, *, security_required: bool = False) -> dict:
        checks = [self._run_check(root, kind) for kind in CHECK_KINDS]
        if security_required:
            checks += [self._run_check(root, "security"),
                       self._run_check(root, "dependency_audit")]
        available = [c for c in checks if c["available"]]
        failures = [c for c in available if not c["passed"]]
        result = {"verified": bool(available) and not failures, "checks": checks, "git": None}
        if git_is_repo(root):
            status, diff = git_status(root), git_diff(root)
            result["git"] = {
                "status": status.stdout, "status_returncode": status.returncode,
                "diff": diff.stdout, "diff_returncode": diff.returncode,
                "uncommitted_changes": git_has_uncommitted_changes(root),
            }
        return result

    def _security_review(self, root: Path, task: str, plan: ExecutionPlan) -> dict:
        diff = git_diff(root).stdout if git_is_repo(root) else ""
        messages = [
            {"role": "system", "content": security_review_instructions()},
            {"role": "user", "content": json.dumps({
                "task": task, "security_level": plan.security_level,
                "security_requirements": plan.security_requirements,
                "changed_files_diff": diff,
            }, ensure_ascii=False)},
        ]
        for _ in range(3):
            response = self.llm.chat(messages)
            try:
                return parse_security_review(response.content).as_dict()
            except SecurityReviewError as exc:
                messages += [{"role": "assistant", "content": response.content},
                             {"role": "user", "content": "Invalid review: " + str(exc) +
                              ". Return valid JSON only."}]
        return {"status": "findings", "summary": "Security review integrity failure.",
                "findings": [{"severity": "high", "category": "review_integrity",
                              "message": "Independent review was not validatable.",
                              "recommendation": "Repeat the review before completion."}]}

    def _workspace_snapshot(self, root: Path, scoped: bool) -> WorkspaceSnapshot | None:
        if not scoped:
            return None
        return WorkspaceSnapshot.for_git_repo(root) if git_is_repo(root) else WorkspaceSnapshot.for_project(root)

    def _record_integrity(self, state: TaskState, before: WorkspaceSnapshot,
                          after: WorkspaceSnapshot, changed: list[str],
                          *, tool: str, plan_step: int, subtask: bool, subtask_id: str | None = None) -> None:
        records = []
        for path in changed:
            before_item = before.files.get(path)
            after_item = after.files.get(path)
            records.append({
                "path": path,
                "exists_before": bool(before_item and before_item.exists),
                "exists_after": bool(after_item and after_item.exists),
                "sha256_before": before_item.digest if before_item else None,
                "sha256_after": after_item.digest if after_item else None,
                "before_data_b64": base64.b64encode(before_item.data).decode("ascii") if before_item and before_item.data is not None else None,
                "tool": tool,
                "plan_step": plan_step,
                "subtask": subtask,
                "subtask_id": subtask_id,
            })
        if records:
            state.record("integrity_change", "Workspace changes recorded with before/after SHA-256 fingerprints.",
                         changes=records, subtask_id=subtask_id)

    def _execute_tool(self, root: Path, action, scope: tuple[str, ...] | None, state: TaskState | None = None, subtask_id: str | None = None) -> str:
        args = action.arguments
        fs = ProjectFilesystem(root)
        mutating = action.tool in {"write_file", "apply_patch", "run_command"}
        integrity_before = self._workspace_snapshot(root, True) if mutating else None
        if action.tool == "list_files":
            return json.dumps(fs.list_files(args.get("limit", 500)), ensure_ascii=False)
        if action.tool == "read_file":
            return fs.read_file(args["path"])
        if action.tool == "search_files":
            return json.dumps(search_text(root, args["query"], args.get("max_results", 50)),
                              ensure_ascii=False)
        if action.tool == "search_web":
            return json.dumps(search_web(args["query"], max_results=args.get("max_results", 5),
                allow_domains=self.settings.web_allow_domains, deny_domains=self.settings.web_deny_domains),
                ensure_ascii=False)
        if action.tool == "fetch_web_page":
            return json.dumps(fetch_web_page(args["url"], max_chars=args.get("max_chars", 30000),
                allow_domains=self.settings.web_allow_domains, deny_domains=self.settings.web_deny_domains),
                ensure_ascii=False)
        if action.tool == "write_file":
            self._check_scope(root, [args["path"]], scope)
            fs.write_file(args["path"], args["content"])
            integrity_after = self._workspace_snapshot(root, True)
            if state is not None and integrity_before is not None and integrity_after is not None:
                self._record_integrity(state, integrity_before, integrity_after,
                                      sorted(integrity_before.changed_paths(integrity_after)),
                                      tool=action.tool, plan_step=action.plan_step, subtask=bool(scope), subtask_id=subtask_id)
            return json.dumps({"ok": True, "path": args["path"], "method": "full_file"})
        if action.tool == "apply_patch":
            patch_paths = self._patch_paths(args["patch"])
            self._check_scope(root, patch_paths, scope)
            result = apply_unified_patch(root, args["patch"])
            if not result.applied:
                raise ValueError(result.error or "Patch was not applied.")
            integrity_after = self._workspace_snapshot(root, bool(scope))
            if state is not None and integrity_before is not None and integrity_after is not None:
                self._record_integrity(state, integrity_before, integrity_after,
                                      sorted(integrity_before.changed_paths(integrity_after)),
                                      tool=action.tool, plan_step=action.plan_step, subtask=bool(scope), subtask_id=subtask_id)
            return json.dumps({"ok": True, "changed_files": list(result.changed_files),
                                "method": "unified_patch"})
        if action.tool == "run_command":
            scoped = bool(scope)
            snapshot = self._workspace_snapshot(root, scoped)
            result = run_command(root, args["command"], timeout=self.settings.command_timeout_seconds,
                                 max_output_chars=self.settings.max_command_output_chars)
            if scoped:
                after_snapshot = WorkspaceSnapshot.for_git_repo(root) if git_is_repo(root) else WorkspaceSnapshot.for_project(root)
                changed = sorted(snapshot.changed_paths(after_snapshot) if snapshot else set())
                outside = sorted(
                    path for path in changed if not self._scope_allows(root, path, scope)
                )
                if outside:
                    if git_is_repo(root) and git_has_rename_or_copy(root):
                        raise RuntimeError(
                            "subtask scope violation: automatic remediation refused because "
                            "the working tree contains a rename/copy operation"
                        )
                    try:
                        restored = snapshot.restore(set(outside)) if snapshot else []
                    except Exception as exc:
                        raise RuntimeError(
                            "subtask scope violation: automatic remediation failed for "
                            + ", ".join(outside) + f": {exc}"
                        ) from exc
                    raise ValueError(
                        "subtask scope violation: "
                        + ", ".join(outside)
                        + "; safely remediated: "
                        + (", ".join(restored) if restored else "none")
                    )
                if state is not None and snapshot is not None:
                    self._record_integrity(state, snapshot, after_snapshot, changed,
                                          tool=action.tool, plan_step=action.plan_step, subtask=bool(scope))
                if changed:
                    return json.dumps({
                        **result.__dict__,
                        "scope_verified": True,
                        "changed_paths": changed,
                    }, ensure_ascii=False)
            return json.dumps(result.__dict__, ensure_ascii=False)
        if action.tool == "run_checks":
            return json.dumps(self._run_check(root, args["kind"]), ensure_ascii=False)
        if action.tool == "git_status":
            return json.dumps(git_status(root).__dict__, ensure_ascii=False)
        if action.tool == "git_diff":
            return json.dumps(git_diff(root).__dict__, ensure_ascii=False)
        if action.tool == "git_log":
            return json.dumps(git_log(root, args.get("limit", 10)).__dict__, ensure_ascii=False)
        raise ToolCallError("unsupported tool: " + action.tool)

    def rollback_operation(self, root: Path, state: TaskState, event_index: int) -> TaskState:
        """Rollback one audited integrity event with a conflict guard."""
        events = [event for event in state.events if event.event_type == "integrity_change"]
        if event_index < 0 or event_index >= len(events):
            raise ValueError("invalid integrity event index")
        event = events[event_index]
        changes = event.data.get("changes", [])
        for change in reversed(changes):
            rollback_file_change(root, change)
        state.record("integrity_rollback", "Audited operation rolled back safely.", event_index=event_index, changes=changes)
        return state

    @staticmethod
    def _patch_paths(patch: str) -> list[str]:
        paths = []
        for line in patch.splitlines():
            if line.startswith("--- ") or line.startswith("+++ "):
                raw = line[4:].split("\t", 1)[0]
                if raw.startswith(("a/", "b/")):
                    raw = raw[2:]
                if raw != "/dev/null" and raw not in paths:
                    paths.append(raw)
        if not paths:
            raise ValueError("patch contains no file paths")
        return paths

    def execute(
        self, root: Path, task: str, plan: ExecutionPlan, state: TaskState,
        *, context: dict | None = None, allowed_scope: tuple[str, ...] | None = None,
        max_iterations: int | None = None, allow_plan_changes: bool = True,
        approval_callback: Callable | None = None,
        high_risk_approval_callback: Callable | None = None,
        persistence_callback: Callable[[TaskState, ExecutionPlan], None] | None = None,
        resume_step: int = 1,
    ) -> TaskState:
        tracker = PlanTracker.resume_from(plan, resume_step)
        root = root.resolve()
        profile = detect_project(root)
        execution_context = {
            "task": task,
            "parent_context": context or {},
            "project": {"kind": profile.kind, "language": profile.language,
                        "framework": profile.framework, "package_manager": profile.package_manager,
                        "commands": profile.commands},
            "project_files": project_snapshot(root),
            "approved_plan": plan.as_dict(),
            "security_level": plan.security_level,
            "security_requirements": plan.security_requirements,
            "scope": list(allowed_scope or ()),
            "plan_progress": tracker.as_dict(),
            "resume_step": resume_step,
            "instruction": "Resume from the specified active plan step. Do not redo completed steps unless verification requires it. " +
                           "Execute only the approved plan. Mutating actions must use the active plan_step. " +
                           "Complete each step with evidence. Never expand scope.",
        }
        messages = [
            {"role": "system", "content": "You are KardecAgent, a local software engineering agent. " +
             tool_instructions()},
            {"role": "user", "content": json.dumps(execution_context, ensure_ascii=False)},
        ]
        state.record("plan_progress", "Plan execution initialized.", progress=tracker.as_dict(),
                     resume_step=resume_step)
        state.record("execution_started", "Approved plan execution started.",
                     scope=list(allowed_scope or ()), subtask=bool(context))

        limit = max_iterations or self.settings.max_iterations
        for iteration in range(1, limit + 1):
            state.iteration = iteration
            state.record("iteration_started", f"Starting iteration {iteration}.")
            if persistence_callback is not None:
                persistence_callback(state, plan)
            response = self.llm.chat(messages)
            state.record("model_response", response.content)
            try:
                action = parse_tool_call(response.content)
            except ToolCallError as exc:
                state.record("model_error", str(exc))
                messages += [{"role": "assistant", "content": response.content},
                             {"role": "user", "content": "Invalid tool call: " + str(exc) +
                              ". " + tool_instructions()}]
                continue

            if action.tool == "request_plan_change":
                if not allow_plan_changes:
                    state.record("plan_change_blocked",
                                 "Subtask cannot change the approved parent plan.")
                    messages += [{"role": "assistant", "content": response.content},
                                 {"role": "user", "content":
                                  "Plan changes are forbidden inside a subtask. Stop and report the blocker."}]
                    continue
                try:
                    proposed = parse_plan(json.dumps(action.arguments["plan"], ensure_ascii=False))
                    required = assess_security(task).level
                    if RISK_RANK[proposed.security_level] < RISK_RANK[required]:
                        raise PlanError("replacement plan security level cannot be lower than task-required level")
                    if required in {"sensitive", "high_risk"} and not proposed.security_requirements:
                        proposed = ExecutionPlan(proposed.summary, proposed.steps, proposed.validation,
                            proposed.risks, proposed.completion_criteria, required,
                            security_requirements_for(required))
                except PlanError as exc:
                    state.record("plan_change_error", str(exc))
                    messages += [{"role": "assistant", "content": response.content},
                                 {"role": "user", "content": "Invalid proposed plan: " + str(exc)}]
                    continue
                state.record("plan_change_proposed", "Model proposed a replacement execution plan.",
                             previous_plan=plan.as_dict(), proposed_plan=proposed.as_dict())
                if approval_callback is None or not approval_callback(proposed):
                    state.record("plan_change_approval", "Replacement plan rejected.", approved=False)
                    messages += [{"role": "assistant", "content": response.content},
                                 {"role": "user", "content":
                                  "Continue using the currently approved plan. Do not deviate."}]
                    continue
                if proposed.security_level == "high_risk":
                    if high_risk_approval_callback is None or not high_risk_approval_callback(proposed):
                        state.record("high_risk_plan_change_approval",
                                     "High-risk replacement plan rejected.", approved=False)
                        continue
                plan, tracker = proposed, PlanTracker(proposed)
                state.record("plan_changed", "Approved replacement plan is now active.",
                             plan=plan.as_dict(), progress=tracker.as_dict())
                if persistence_callback is not None:
                    persistence_callback(state, plan)
                continue

            if action.tool in {"write_file", "apply_patch", "run_command", "run_checks", "complete_step"}:
                if action.plan_step != tracker.current_step:
                    state.record("plan_scope_violation", "Action rejected: outside active approved plan step.",
                                 tool=action.tool, requested_step=action.plan_step,
                                 active_step=tracker.current_step)
                    messages += [{"role": "assistant", "content": response.content},
                                 {"role": "user", "content": json.dumps({
                                     "error": "plan_scope_violation",
                                     "active_plan_step": tracker.current_step,
                                 })}]
                    continue
                if action.plan_step not in tracker.started_steps:
                    tracker.start(action.plan_step)
                    state.record("plan_step_started", f"Started plan step {action.plan_step}.",
                                 step=action.plan_step,
                                 description=plan.steps[action.plan_step - 1])

            if action.tool == "complete_step":
                step = action.arguments["step"]
                if step != action.plan_step or step != tracker.current_step:
                    state.record("plan_scope_violation", "Step completion rejected.",
                                 requested_step=step, active_step=tracker.current_step)
                    continue
                tracker.complete(step)
                state.record("plan_step_completed", f"Completed plan step {step}.",
                             step=step, evidence=action.arguments["evidence"], progress=tracker.as_dict())
                messages += [{"role": "assistant", "content": response.content},
                             {"role": "user", "content": json.dumps({
                                 "plan_progress": tracker.as_dict(),
                                 "instruction": "Continue with the new active plan step.",
                             })}]
                continue

            if action.tool == "finish":
                if not tracker.completed:
                    state.record("plan_scope_violation", "Finish rejected: plan has incomplete steps.",
                                 progress=tracker.as_dict())
                    messages += [{"role": "assistant", "content": response.content},
                                 {"role": "user", "content": "Complete every approved plan step before finishing."}]
                    continue
                evidence = action.arguments["criteria_evidence"]
                if not evidence:
                    state.record("completion_criteria_failed", "Finish requires completion evidence.")
                    messages += [{"role": "assistant", "content": response.content},
                                 {"role": "user", "content": "Provide non-empty evidence for the completion criteria."}]
                    continue
                security_required = plan.security_level in {"sensitive", "high_risk"}
                verification = self.verify(root, security_required=security_required)
                state.record("verification", "Completion verification executed.", verification=verification)
                if not verification["verified"]:
                    state.record("verification_failed", "Completion verification failed.", verification=verification)
                    messages += [{"role": "assistant", "content": response.content},
                                 {"role": "user", "content": json.dumps({
                                     "verification": verification,
                                     "instruction": "Diagnose the failed checks, fix the implementation, and retest before finishing.",
                                 }, ensure_ascii=False)}]
                    continue
                if security_required:
                    review = self._security_review(root, task, plan)
                    state.record("security_review", "Independent security review completed.", review=review)
                    if review.get("status") != "pass":
                        state.record("verification_failed", "Security review reported findings.", review=review)
                        messages += [{"role": "assistant", "content": response.content},
                                     {"role": "user", "content": json.dumps({
                                         "security_review": review,
                                         "instruction": "Address security findings before finishing.",
                                     }, ensure_ascii=False)}]
                        continue
                state.record("completed", "Task completed and verified.",
                             reason=action.arguments["reason"], criteria_evidence=evidence,
                             verification=verification)
                state.status = TaskStatus.COMPLETED
                return state

            try:
                result = self._execute_tool(root, action, allowed_scope, state, (context or {}).get("subtask", {}).get("id"))
                state.record("tool_result", f"{action.tool} executed.", tool=action.tool, result=result)
                messages += [{"role": "assistant", "content": response.content},
                             {"role": "user", "content": json.dumps({
                                 "tool": action.tool, "result": result,
                                 "plan_progress": tracker.as_dict(),
                             }, ensure_ascii=False)}]
            except Exception as exc:
                state.record("tool_error", f"{action.tool} failed: {exc}",
                             tool=action.tool, error=str(exc))
                messages += [{"role": "assistant", "content": response.content},
                             {"role": "user", "content": json.dumps({
                                 "tool": action.tool, "error": str(exc),
                                 "instruction": "Diagnose the error and choose a safe correction.",
                             }, ensure_ascii=False)}]

        state.status = TaskStatus.MAX_ITERATIONS
        state.record("max_iterations", "Maximum execution iterations reached.")
        if persistence_callback is not None:
            persistence_callback(state, plan)
        return state
