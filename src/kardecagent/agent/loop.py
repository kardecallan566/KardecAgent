from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from ..project import discover_command, project_snapshot
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


class AgentLoop:
    def __init__(self, llm: LocalLLMClient, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

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
            kind = args["kind"]
            command = discover_command(root, kind)
            if not command:
                return json.dumps(
                    {"ok": False, "error": "No command discovered", "kind": kind}
                )
            result = run_command(
                root,
                command,
                timeout=self.settings.command_timeout_seconds,
                max_output_chars=self.settings.max_command_output_chars,
            )
            return json.dumps(
                {"kind": kind, "command": command, **result.__dict__},
                ensure_ascii=False,
            )
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
                    branch=checkpoint.stderr.strip() or checkpoint.command,
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
                state.status = TaskStatus.COMPLETED
                state.record("completed", action.arguments["reason"])
                return state

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
