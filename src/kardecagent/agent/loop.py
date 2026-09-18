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

    def run(self, project_root: Path, task: str) -> TaskState:
        state = TaskState(task, str(project_root))
        state.status = TaskStatus.RUNNING

        if git_is_repo(project_root):
            checkpoint = create_checkpoint(project_root, task)
            if checkpoint.returncode == 0:
                state.record(
                    "checkpoint_created",
                    "Created pre-task Git checkpoint.",
                    branch=checkpoint.command,
                    dirty=git_has_uncommitted_changes(project_root),
                    current_branch=git_current_branch(project_root).stdout.strip(),
                )
            else:
                state.record(
                    "checkpoint_error",
                    "Could not create pre-task Git checkpoint.",
                    error=checkpoint.stderr.strip(),
                )
        else:
            state.record("checkpoint_skipped", "Project is not a Git repository.")

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT + "\n" + tool_instructions(),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": task,
                        "project": {
                            "kind": profile.kind,
                            "language": profile.language,
                            "framework": profile.framework,
                            "package_manager": profile.package_manager,
                            "commands": profile.commands,
                        },
                        "project_files": project_snapshot(project_root),
                    },
                    ensure_ascii=False,
                ),
            },
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
                    {
                        "role": "user",
                        "content": "Invalid tool call: " + str(exc) + ". " + tool_instructions(),
                    },
                ]
                continue

            if action.tool == "finish":
                verification = self._verify_completion(project_root)
                state.record(
                    "verification",
                    "Completion verification executed.",
                    verified=verification["verified"],
                    checks=verification["checks"],
                )
                if verification["verified"]:
                    state.status = TaskStatus.COMPLETED
                    state.record("completed", action.arguments["reason"])
                    return state

                state.record(
                    "verification_failed",
                    "Completion was rejected because verification did not pass.",
                )
                messages += [
                    {"role": "assistant", "content": response.content},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "verification": verification,
                                "instruction": (
                                    "Do not finish yet. Diagnose the failed checks, "
                                    "make the necessary changes, and run the relevant "
                                    "checks again."
                                ),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
                continue

            try:
                result = self._execute(project_root, action)
            except Exception as exc:
                result = json.dumps(
                    {
                        "ok": False,
                        "error": type(exc).__name__,
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                )

            state.record("tool_result", result)
            messages += [
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"tool_result": result},
                        ensure_ascii=False,
                    ),
                },
            ]

        state.status = TaskStatus.MAX_ITERATIONS
        state.record("max_iterations", "Maximum agent iterations reached.")
        return state
