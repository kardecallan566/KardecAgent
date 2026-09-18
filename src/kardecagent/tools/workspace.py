from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..project.context import IGNORED_DIRS, iter_project_files


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    exists: bool
    is_regular_file: bool
    is_symlink: bool
    data: bytes | None
    digest: str | None


class WorkspaceSnapshot:
    """Exact byte-level snapshot used to detect and safely remediate changes."""

    def __init__(self, root: Path, paths: set[str]) -> None:
        self.root = root.resolve()
        self.files = {path: self._capture(path) for path in sorted(paths)}

    @classmethod
    def for_git_repo(cls, root: Path) -> "WorkspaceSnapshot":
        root = root.resolve()
        paths = _git_tracked_paths(root)
        paths.update(_git_changed_paths(root))
        return cls(root, paths)

    @classmethod
    def for_project(cls, root: Path) -> "WorkspaceSnapshot":
        """Snapshot all regular project files, excluding generated/dependency trees."""
        root = root.resolve()
        paths = {p.relative_to(root).as_posix() for p in iter_project_files(root)}
        return cls(root, paths)

    def _capture(self, raw: str) -> FileSnapshot:
        path = _safe_existing_path(self.root, raw)
        if path.is_symlink():
            return FileSnapshot(raw, True, False, True, None, None)
        if not path.exists():
            return FileSnapshot(raw, False, False, False, None, None)
        if not path.is_file():
            return FileSnapshot(raw, True, False, False, None, None)
        data = path.read_bytes()
        return FileSnapshot(raw, True, True, False, data, hashlib.sha256(data).hexdigest())

    def changed_paths(self, other: "WorkspaceSnapshot") -> set[str]:
        """Return paths whose existence/type/content differs between snapshots."""
        if self.root != other.root:
            raise ValueError("cannot compare snapshots from different roots")
        paths = set(self.files) | set(other.files)
        changed: set[str] = set()
        for path in paths:
            a = self.files.get(path)
            b = other.files.get(path)
            a_state = (a.exists, a.is_regular_file, a.is_symlink, a.digest) if a else (False, False, False, None)
            b_state = (b.exists, b.is_regular_file, b.is_symlink, b.digest) if b else (False, False, False, None)
            if a_state != b_state:
                changed.add(path)
        return changed

    def restore(self, paths: set[str]) -> list[str]:
        """Restore selected paths byte-for-byte, refusing symlink/special-file remediation."""
        restored: list[str] = []
        for raw in sorted(paths):
            snapshot = self.files.get(raw, FileSnapshot(raw, False, False, False, None, None))
            target = _safe_remediation_path(self.root, raw)
            if target.is_symlink():
                raise RuntimeError(f"refusing remediation through symlink: {raw}")

            if not snapshot.exists:
                if target.exists() or target.is_symlink():
                    if not target.is_file() or target.is_symlink():
                        raise RuntimeError(f"refusing removal of non-regular path: {raw}")
                    target.unlink()
                    restored.append(raw)
                continue

            if snapshot.is_symlink or not snapshot.is_regular_file or snapshot.data is None:
                raise RuntimeError(f"refusing automatic remediation for special path: {raw}")

            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".kardecagent-restore")
            if temporary.exists() or temporary.is_symlink():
                raise RuntimeError(f"unsafe restore target exists: {temporary}")
            temporary.write_bytes(snapshot.data)
            os.replace(temporary, target)
            restored.append(raw)
        return restored


def _safe_existing_path(root: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"invalid project-relative path: {raw}")
    candidate = root / path
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes project root: {raw}") from exc
    return candidate


def _safe_remediation_path(root: Path, raw: str) -> Path:
    candidate = _safe_existing_path(root, raw)
    current = candidate
    while current != root:
        if current.is_symlink():
            raise RuntimeError(f"refusing remediation through symlink: {raw}")
        current = current.parent
    return candidate


def _git_tracked_paths(root: Path) -> set[str]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    return {item.decode("utf-8").replace("\\", "/") for item in result.stdout.split(b"\0") if item}


def _git_changed_paths(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root, capture_output=True, check=True,
    )
    paths: set[str] = set()
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        if len(line) < 4:
            continue
        status_path = line[3:]
        if " -> " in status_path:
            old, new = status_path.split(" -> ", 1)
            paths.update({old, new})
        else:
            paths.add(status_path)
    return {path.replace("\\", "/") for path in paths}


def git_has_rename_or_copy(root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root, capture_output=True, check=True,
    )
    return any(
        (len(line) >= 3 and line[1] in {"R", "C"}) or
        (len(line) >= 2 and line[0] in {"R", "C"})
        for line in result.stdout.decode("utf-8", errors="replace").splitlines()
    )
