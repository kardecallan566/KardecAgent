from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

class ToolCallError(ValueError):
    pass

@dataclass(frozen=True)
class ToolCall:
    tool: str
    arguments: dict[str, Any]
    plan_step: int | None = None

TOOL_SCHEMAS = {
    "list_files": {"required": [], "types": {}, "optional": {"limit": int}},
    "read_file": {"required": ["path"], "types": {"path": str}, "optional": {}},
    "search_files": {"required": ["query"], "types": {"query": str}, "optional": {"max_results": int}},
    "write_file": {"required": ["path", "content"], "types": {"path": str, "content": str}, "optional": {}},
    "run_command": {"required": ["command"], "types": {"command": str}, "optional": {}},
    "run_checks": {"required": ["kind"], "types": {"kind": str}, "optional": {}},
    "git_status": {"required": [], "types": {}, "optional": {}},
    "git_diff": {"required": [], "types": {}, "optional": {}},
    "git_log": {"required": [], "types": {}, "optional": {"limit": int}},
    "complete_step": {"required": ["step", "evidence"], "types": {"step": int, "evidence": str}, "optional": {}},
    "finish": {"required": ["reason"], "types": {"reason": str}, "optional": {}},
}

STEP_REQUIRED_TOOLS = {"write_file", "run_command", "run_checks", "complete_step"}

def _validate_arguments(tool: str, arguments: Any) -> dict[str, Any]:
    schema = TOOL_SCHEMAS[tool]
    if not isinstance(arguments, dict): raise ToolCallError("arguments must be an object")
    missing = [k for k in schema["required"] if k not in arguments]
    if missing: raise ToolCallError("missing required argument(s): " + ", ".join(missing))
    for key, expected in {**schema["types"], **schema["optional"]}.items():
        if key in arguments and (isinstance(arguments[key], bool) or not isinstance(arguments[key], expected)):
            raise ToolCallError(f"argument '{key}' must be {expected.__name__}")
    allowed = set(schema["required"]) | set(schema["types"]) | set(schema["optional"])
    unknown = set(arguments) - allowed
    if unknown: raise ToolCallError("unknown argument(s): " + ", ".join(sorted(unknown)))
    if tool == "run_checks" and arguments.get("kind") not in {"test", "lint", "typecheck", "build", "validate"}:
        raise ToolCallError("argument 'kind' must be one of: test, lint, typecheck, build, validate")
    for key in ("limit", "max_results"):
        if key in arguments and arguments[key] < 1: raise ToolCallError(f"argument '{key}' must be at least 1")
    return arguments

def parse_tool_call(content: str) -> ToolCall:
    text = content.strip()
    if text.startswith("```"): raise ToolCallError("markdown code fences are not allowed")
    try: payload = json.loads(text)
    except json.JSONDecodeError as exc: raise ToolCallError("invalid JSON: " + exc.msg) from exc
    if not isinstance(payload, dict): raise ToolCallError("tool call must be a JSON object")
    tool = payload.get("tool")
    if not isinstance(tool, str) or not tool: raise ToolCallError("missing string field 'tool'")
    if tool not in TOOL_SCHEMAS: raise ToolCallError("unknown tool: " + tool)
    plan_step = payload.get("plan_step")
    if plan_step is not None and (isinstance(plan_step, bool) or not isinstance(plan_step, int) or plan_step < 1):
        raise ToolCallError("plan_step must be a positive integer when provided")
    if tool in STEP_REQUIRED_TOOLS and plan_step is None: raise ToolCallError("plan_step is required for this tool")
    return ToolCall(tool, _validate_arguments(tool, payload.get("arguments", {})), plan_step)

def tool_instructions() -> str:
    return ('Return ONLY JSON with tool, arguments and optional plan_step. '
            'plan_step is required for write_file, run_command, run_checks and complete_step. '
            'Use read-only tools without plan_step. Tools: list_files, read_file, search_files, write_file, run_command, run_checks, '
            'git_status, git_diff, git_log, complete_step, finish. '
            'Only execute actions belonging to the active approved plan step. '
            'Complete a step only after verifying its result. If the plan must change, stop and request approval.')
