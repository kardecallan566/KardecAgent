from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


DEFAULT_ALLOWED_COMMANDS = {
    "python", "python3", "py", "pip", "pip3",
    "pytest", "ruff", "mypy",
    "node", "npm", "pnpm", "yarn", "npx",
    "tsc", "eslint", "prettier", "git",
}

DEFAULT_BLOCKED_PATTERNS = (
    r"(^|[;&|])\s*(format|diskpart|shutdown|restart-computer|stop-computer)\b",
    r"(^|[;&|])\s*(reg\s+delete)\b",
    r"(^|[;&|])\s*(rm\s+-[a-z]*r|del\s+/s|remove-item\s+.*-recurse)\b",
)


def _command_executable(command: str) -> str | None:
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        return None
    if not parts:
        return None
    executable = parts[0].strip('"').lower()
    executable = executable.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if executable.endswith(".exe"):
        executable = executable[:-4]
    return executable


def is_command_blocked(command: str) -> bool:
    normalized = " ".join(command.strip().lower().split())
    return any(re.search(pattern, normalized) for pattern in DEFAULT_BLOCKED_PATTERNS)


def is_command_allowed(command: str, allowed_commands: set[str] | None = None) -> bool:
    if is_command_blocked(command):
        return False
    allowed = allowed_commands or DEFAULT_ALLOWED_COMMANDS
    executable = _command_executable(command)
    return executable in {item.lower() for item in allowed}


def run_command(
    root: Path,
    command: str,
    *,
    timeout: float = 120.0,
    max_output_chars: int = 20_000,
    allowed_commands: set[str] | None = None,
) -> CommandResult:
    if not is_command_allowed(command, allowed_commands):
        raise PermissionError(
            "Command rejected by terminal policy. "
            "Only approved development commands are allowed."
        )
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return CommandResult(
            command,
            completed.returncode,
            completed.stdout[-max_output_chars:],
            completed.stderr[-max_output_chars:],
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command,
            -1,
            str(exc.stdout or "")[-max_output_chars:],
            str(exc.stderr or "")[-max_output_chars:],
            True,
        )
