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
    "finish": {"required": ["reason"], "types": {"reason": str}, "optional": {}},
}

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
    return ToolCall(tool, _validate_arguments(tool, payload.get("arguments", {})))

def tool_instructions() -> str:
    return (
        'Return ONLY JSON: {"tool":"<name>","arguments":{...}}. Tools: '
        'list_files(limit?), read_file(path), search_files(query,max_results?), '
        'write_file(path,content), run_command(command), run_checks(kind), '
        'git_status(), git_diff(), git_log(limit?), finish(reason). '
        'run_checks kind: test, lint, typecheck, build, validate. '
        'Use validate for project-specific validation. '
        'run_command accepts only approved development commands.'
    )
