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


def _safe_ref(value: str, label: str = "Git ref") -> str:
    value = value.strip()
    if not value or value.startswith("-") or "\x00" in value:
        raise ValueError(f"{label} is invalid.")
    return value


def git_branch_list(root: Path) -> GitResult:
    return _git(root, ["branch", "--all", "--verbose", "--no-abbrev"])


def git_remote_list(root: Path) -> GitResult:
    return _git(root, ["remote", "-v"])


def git_fetch(root: Path, remote: str = "origin", *, prune: bool = False) -> GitResult:
    remote = _safe_ref(remote, "Remote")
    args = ["fetch"]
    if prune:
        args.append("--prune")
    args.append(remote)
    return _git(root, args, timeout=120.0)


def git_pull(root: Path, remote: str = "origin", branch: str | None = None) -> GitResult:
    remote = _safe_ref(remote, "Remote")
    args = ["pull", "--ff-only", remote]
    if branch:
        args.append(_safe_ref(branch, "Branch"))
    return _git(root, args, timeout=120.0)


def git_push(root: Path, remote: str = "origin", branch: str | None = None,
             *, set_upstream: bool = False) -> GitResult:
    remote = _safe_ref(remote, "Remote")
    args = ["push"]
    if set_upstream:
        args.append("--set-upstream")
    args.append(remote)
    if branch:
        args.append(_safe_ref(branch, "Branch"))
    return _git(root, args, timeout=120.0)


def git_create_branch(root: Path, branch: str, base: str = "HEAD") -> GitResult:
    branch = _safe_ref(branch, "Branch")
    base = _safe_ref(base, "Base ref")
    checked = _git(root, ["check-ref-format", "--branch", branch])
    if checked.returncode != 0:
        return GitResult("git switch -c", checked.returncode, "", checked.stderr or "Invalid branch name.")
    return _git(root, ["switch", "-c", branch, base])


def git_checkout(root: Path, branch: str) -> GitResult:
    branch = _safe_ref(branch, "Branch")
    return _git(root, ["switch", branch])


def git_commit(root: Path, message: str, paths: list[str]) -> GitResult:
    message = message.strip()
    if not message or "\x00" in message:
        raise ValueError("Commit message cannot be empty.")
    normalized = [str(path).strip() for path in paths if str(path).strip()]
    if not normalized:
        raise ValueError("Select at least one path to stage; implicit git add -A is not allowed.")
    if any(Path(path).is_absolute() or ".." in Path(path).parts for path in normalized):
        raise ValueError("Commit paths must stay inside the workspace.")
    staged_before = _git(root, ["diff", "--cached", "--name-only"])
    if staged_before.returncode != 0:
        return staged_before
    unrelated_staged = sorted(set(staged_before.stdout.splitlines()) - set(normalized))
    if unrelated_staged:
        return GitResult("git commit", 1, "", "Unrelated staged changes detected; refusing to include them: " + ", ".join(unrelated_staged))
    staged = _git(root, ["add", "--", *[":(literal)" + path for path in normalized]])
    if staged.returncode != 0:
        return staged
    staged_diff = _git(root, ["diff", "--cached", "--quiet"])
    if staged_diff.returncode == 0:
        return GitResult("git commit", 1, "", "No staged changes in the selected paths.")
    if staged_diff.returncode != 1:
        return staged_diff
    return _git(root, ["commit", "-m", message], timeout=120.0)


def git_merge(root: Path, branch: str) -> GitResult:
    branch = _safe_ref(branch, "Branch")
    return _git(root, ["merge", "--no-ff", "--no-edit", branch], timeout=120.0)


def gh_create_pr(root: Path, title: str, body: str, base: str = "main",
                 *, draft: bool = False) -> GitResult:
    title = title.strip()
    if not title:
        raise ValueError("Pull request title cannot be empty.")
    base = _safe_ref(base, "Base branch")
    args = ["pr", "create", "--title", title, "--body", body, "--base", base]
    if draft:
        args.append("--draft")
    return _run_gh(root, args, timeout=120.0)


def gh_pr_status(root: Path) -> GitResult:
    return _run_gh(root, ["pr", "status"], timeout=60.0)


def gh_merge_pr(root: Path, number: int, method: str = "squash") -> GitResult:
    if number < 1:
        raise ValueError("Pull request number must be positive.")
    if method not in {"merge", "squash", "rebase"}:
        raise ValueError("Merge method must be merge, squash, or rebase.")
    return _run_gh(root, ["pr", "merge", str(number), "--" + method], timeout=120.0)


def _run_gh(root: Path, args: list[str], timeout: float = 60.0) -> GitResult:
    command = "gh " + " ".join(args)
    try:
        process = subprocess.run(
            ["gh", *args], cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return GitResult(command, process.returncode, process.stdout, process.stderr)
    except FileNotFoundError:
        return GitResult(command, 127, "", "GitHub CLI (gh) is not installed.")
    except subprocess.TimeoutExpired as exc:
        return GitResult(command, -1, str(exc.stdout or ""), str(exc.stderr or "timeout"))
