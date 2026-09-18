from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class GitResult:
    command: str
    returncode: int
    stdout: str
    stderr: str


def _git(root: Path, args: list[str], timeout: float = 30.0) -> GitResult:
    command = "git " + " ".join(args)
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return GitResult(command, p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired as exc:
        return GitResult(
            command,
            -1,
            str(exc.stdout or ""),
            str(exc.stderr or "timeout"),
        )


def git_status(root: Path) -> GitResult:
    return _git(root, ["status", "--short", "--branch"])


def git_diff(root: Path) -> GitResult:
    return _git(root, ["diff", "--no-ext-diff"])


def git_log(root: Path, limit: int = 10) -> GitResult:
    if limit < 1:
        raise ValueError("Git log limit must be at least 1.")
    return _git(root, ["log", f"-{limit}", "--oneline", "--decorate"])


def git_is_repo(root: Path) -> bool:
    result = _git(root, ["rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def git_current_branch(root: Path) -> GitResult:
    return _git(root, ["branch", "--show-current"])


def git_has_uncommitted_changes(root: Path) -> bool:
    result = _git(root, ["status", "--porcelain"])
    return result.returncode == 0 and bool(result.stdout.strip())


def _safe_branch_fragment(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return value[:40] or "task"


def create_checkpoint(root: Path, task: str = "task") -> GitResult:
    """Create a unique branch pointing at the current HEAD.

    This records the committed repository state only. Uncommitted changes are
    intentionally preserved and are not included in the checkpoint branch.
    """
    if not git_is_repo(root):
        return GitResult("git checkpoint", 128, "", "Not a Git repository.")

    current = _git(root, ["rev-parse", "--verify", "HEAD"])
    if current.returncode != 0:
        return current

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    branch = f"agent/checkpoint-{_safe_branch_fragment(task)}-{timestamp}"
    return _git(root, ["branch", branch, "HEAD"])


def rollback_to(root: Path, ref: str) -> GitResult:
    return _git(root, ["reset", "--hard", ref])


def git_changed_paths(root: Path) -> set[str]:
    """Return project-relative paths currently changed in the working tree."""
    result = _git(root, ["status", "--porcelain", "--untracked-files=all"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git status failed")
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        status_path = line[3:]
        # Rename/copy entries use "old -> new"; the new path is the one that
        # matters for scope enforcement.
        if " -> " in status_path:
            status_path = status_path.split(" -> ", 1)[1]
        paths.add(status_path.replace("\\", "/"))
    return paths


def git_changed_fingerprints(root: Path, paths: set[str] | None = None) -> dict[str, str]:
    """Hash changed working-tree files so pre-existing dirty files can be monitored."""
    import hashlib

    changed = paths if paths is not None else git_changed_paths(root)
    fingerprints: dict[str, str] = {}
    for raw in changed:
        path = (root / raw).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            continue
        if not path.is_file():
            fingerprints[raw] = "<missing>"
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        fingerprints[raw] = digest.hexdigest()
    return fingerprints
