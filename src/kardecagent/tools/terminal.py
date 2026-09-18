from __future__ import annotations

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


@dataclass(frozen=True)
class ParsedCommand:
    argv: tuple[str, ...]
    executable: str


DEFAULT_ALLOWED_COMMANDS = {
    "python", "python3", "py", "pip", "pip3",
    "pytest", "ruff", "mypy",
    "node", "npm", "pnpm", "yarn", "npx",
    "tsc", "eslint", "prettier", "git",
}

# These executables would turn the terminal into a general-purpose shell.
# They are intentionally not part of the development-command allowlist.
SHELL_EXECUTABLES = {
    "cmd", "command", "powershell", "pwsh", "bash", "sh", "zsh",
    "fish", "wsl", "busybox",
}

# Shell syntax is rejected even when it appears after an otherwise allowed
# executable. This prevents the old "first token is allowed" bypass.
SHELL_OPERATORS = {
    ";", "&", "|", "||", "&&", ">", ">>", "<", "<<",
}

MAX_COMMAND_CHARS = 4_000


def _scan_shell_syntax(command: str) -> bool:
    """Return True when shell operators are present outside quoted strings."""
    quote: str | None = None
    escaped = False
    i = 0
    while i < len(command):
        char = command[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if char == "^" and quote is None:
            # Windows cmd escaping is itself shell syntax; reject it rather
            # than attempting to emulate cmd.exe parsing.
            return True
        if char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            i += 1
            continue
        if quote is None:
            if command.startswith("&&", i) or command.startswith("||", i):
                return True
            if char in {";", "&", "|", ">", "<"}:
                return True
        i += 1
    return quote is not None


def _command_executable(command: str) -> str | None:
    parsed = parse_command(command)
    return parsed.executable if parsed else None


def parse_command(command: str) -> ParsedCommand | None:
    """Parse one argv-only command without invoking a shell.

    The parser deliberately rejects shell operators, unclosed quotes, and
    empty commands. Quoted arguments remain ordinary argv values.
    """
    if not isinstance(command, str):
        return None
    command = command.strip()
    if not command or len(command) > MAX_COMMAND_CHARS:
        return None
    if _scan_shell_syntax(command):
        return None
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        return None
    if not parts:
        return None

    executable = parts[0].strip('"').strip("'").lower()
    executable = executable.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if executable.endswith(".exe"):
        executable = executable[:-4]
    return ParsedCommand(tuple(parts), executable)


def is_command_blocked(command: str) -> bool:
    parsed = parse_command(command)
    if parsed is None:
        return True
    return parsed.executable in SHELL_EXECUTABLES


def is_command_allowed(command: str, allowed_commands: set[str] | None = None) -> bool:
    parsed = parse_command(command)
    if parsed is None or is_command_blocked(command):
        return False
    allowed = allowed_commands or DEFAULT_ALLOWED_COMMANDS
    return parsed.executable in {item.lower().removesuffix(".exe") for item in allowed}


def run_command(
    root: Path,
    command: str,
    *,
    timeout: float = 120.0,
    max_output_chars: int = 20_000,
    allowed_commands: set[str] | None = None,
) -> CommandResult:
    parsed = parse_command(command)
    if parsed is None or not is_command_allowed(command, allowed_commands):
        raise PermissionError(
            "Command rejected by terminal policy. "
            "Only approved development commands without shell syntax are allowed."
        )

    try:
        completed = subprocess.run(
            list(parsed.argv),
            cwd=root,
            shell=False,
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
