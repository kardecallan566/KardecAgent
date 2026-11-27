from __future__ import annotations

import base64
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


def rollback_file_change(root: Path, change: dict) -> None:
    """Safely reverse one audited file change, only if its current hash matches the recorded after-state."""
    raw = str(change["path"])
    target = _safe_remediation_path(root.resolve(), raw)
    expected_after = change.get("sha256_after")
    before_exists = bool(change.get("exists_before"))
    before_b64 = change.get("before_data_b64")

    if target.is_symlink():
        raise RuntimeError(f"refusing rollback through symlink: {raw}")
    if target.exists() and not target.is_file():
        raise RuntimeError(f"refusing rollback of non-regular path: {raw}")

    current_digest = None
    if target.is_file():
        current_digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if current_digest != expected_after:
        raise RuntimeError(
            f"rollback conflict for {raw}: current SHA-256 does not match audited after-state"
        )

    if not before_exists:
        if target.exists():
            target.unlink()
        return

    if not isinstance(before_b64, str):
        raise RuntimeError(f"missing audited before-state for {raw}")
    data = base64.b64decode(before_b64.encode("ascii"), validate=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".kardecagent-rollback")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError(f"unsafe rollback target exists: {temporary}")
    temporary.write_bytes(data)
    os.replace(temporary, target)



def verify_recorded_changes(root: Path, changes: list[dict]) -> dict:
    """Independently compare audited after-state fingerprints with current disk state."""
    root = root.resolve()
    results = []
    for change in changes:
        raw = str(change.get("path", ""))
        try:
            target = _safe_remediation_path(root, raw)
        except (ValueError, RuntimeError) as exc:
            results.append({
                "path": raw,
                "status": "unsafe_path",
                "expected_exists": bool(change.get("exists_after", True)),
                "actual_exists": None,
                "expected_sha256": change.get("sha256_after"),
                "actual_sha256": None,
                "error": str(exc),
            })
            continue
        if target.is_symlink():
            actual_exists, actual_digest = True, None
            status = "unsafe_symlink"
        elif target.exists() and not target.is_file():
            actual_exists, actual_digest = True, None
            status = "unsafe_special_file"
        elif target.is_file():
            data = target.read_bytes()
            actual_exists = True
            actual_digest = hashlib.sha256(data).hexdigest()
            status = "verified" if (
                change.get("exists_after", True)
                and actual_digest == change.get("sha256_after")
            ) else "mismatch"
        else:
            actual_exists, actual_digest = False, None
            status = "verified" if not change.get("exists_after", True) else "missing"
        results.append({
            "path": raw,
            "status": status,
            "expected_exists": bool(change.get("exists_after", True)),
            "actual_exists": actual_exists,
            "expected_sha256": change.get("sha256_after"),
            "actual_sha256": actual_digest,
        })
    return {
        "verified": all(item["status"] == "verified" for item in results),
        "files": results,
        "checked": len(results),
    }


def validate_rollback_file_change(root: Path, change: dict) -> None:
    """Preflight one rollback without mutating the workspace."""
    raw = str(change["path"])
    target = _safe_remediation_path(root.resolve(), raw)
    if target.is_symlink():
        raise RuntimeError(f"refusing rollback through symlink: {raw}")
    if target.exists() and not target.is_file():
        raise RuntimeError(f"refusing rollback of non-regular path: {raw}")
    current_digest = (
        hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
    )
    if current_digest != change.get("sha256_after"):
        raise RuntimeError(
            f"rollback conflict for {raw}: current SHA-256 does not match audited after-state"
        )
    if change.get("exists_before") and not isinstance(change.get("before_data_b64"), str):
        raise RuntimeError(f"missing audited before-state for {raw}")
    if isinstance(change.get("before_data_b64"), str):
        base64.b64decode(change["before_data_b64"].encode("ascii"), validate=True)


def rollback_file_changes(root: Path, changes: list[dict]) -> list[str]:
    """Preflight an entire audited transaction before restoring any of its files."""
    seen = set()
    for change in changes:
        raw = str(change["path"])
        if raw in seen:
            raise RuntimeError(f"duplicate path in rollback transaction: {raw}")
        seen.add(raw)
        validate_rollback_file_change(root, change)
    restored = []
    for change in reversed(changes):
        rollback_file_change(root, change)
        restored.append(str(change["path"]))
    return restored
