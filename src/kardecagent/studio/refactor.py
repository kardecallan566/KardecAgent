"""Safe, preview-first Python symbol refactoring backed by Jedi."""
from __future__ import annotations

import hashlib
import keyword
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_SOURCE_BYTES = 1_000_000
MAX_PREVIEWS = 10
MAX_DIFF_CHARS = 40_000


def _workspace_file(root: Path, raw_path: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("file_path is required.")
    candidate = (root / raw_path).resolve(strict=False)
    if not candidate.is_relative_to(root) or candidate.suffix.lower() not in {".py", ".pyi"}:
        raise ValueError("Rename Symbol accepts Python files inside the workspace only.")
    if not candidate.is_file():
        raise ValueError("The target file does not exist.")
    return candidate


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class _PendingRename:
    changes: dict[Path, tuple[str, bytes, int]]
    diff: str


class RenamePreviewManager:
    """Keeps bounded, single-use previews and rejects stale-file application."""

    def __init__(self) -> None:
        self._pending: dict[str, _PendingRename] = {}
        self._lock = threading.RLock()

    def preview(
        self,
        project_root: str,
        file_path: str,
        source: str,
        line: int,
        column: int,
        new_name: str,
    ) -> dict[str, Any]:
        root = Path(project_root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("project_root must be a directory.")
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError("Source exceeds the 1 MB semantic-analysis limit.")
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            raise ValueError("line must be a positive integer.")
        if isinstance(column, bool) or not isinstance(column, int) or column < 0:
            raise ValueError("column must be a non-negative integer.")
        if not isinstance(new_name, str) or not new_name.isidentifier() or keyword.iskeyword(new_name):
            raise ValueError("The new name must be a valid, non-keyword Python identifier.")

        target = _workspace_file(root, file_path)
        original = target.read_bytes()
        if original.decode("utf-8") != source:
            raise ValueError("Save the active file before renaming; its contents changed since it was loaded.")

        try:
            import jedi  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("Jedi is not installed. Install the optional 'studio' extra.") from exc

        project = jedi.Project(path=str(root), added_sys_path=[str(root)])
        script = jedi.Script(code=source, path=str(target), project=project)
        refactoring = script.rename(line=line, column=column, new_name=new_name)
        changed_files = refactoring.get_changed_files()
        if not changed_files:
            raise ValueError("Jedi did not find any files to change at this symbol.")

        pending: dict[Path, tuple[str, bytes, int]] = {}
        display_changes: list[dict[str, Any]] = []
        for changed_path, changed_file in changed_files.items():
            path = Path(changed_path).resolve(strict=True)
            if not path.is_relative_to(root) or path.suffix.lower() not in {".py", ".pyi"}:
                raise ValueError("Jedi proposed a change outside the Python workspace.")
            if not path.is_file():
                raise ValueError("Jedi proposed changing a file that no longer exists.")
            current = path.read_bytes()
            # Jedi's ChangedFile exposes get_new_code() across supported versions.
            getter = getattr(changed_file, "get_new_code", None)
            new_code = getter() if callable(getter) else getattr(changed_file, "new_code", None)
            if not isinstance(new_code, str):
                raise RuntimeError("The installed Jedi version did not expose the refactored source.")
            encoded = new_code.encode("utf-8")
            relative = path.relative_to(root).as_posix()
            pending[path] = (_digest(current), encoded, os.stat(path).st_mode)
            display_changes.append({
                "path": relative,
                "before_bytes": len(current),
                "after_bytes": len(encoded),
            })

        diff = str(refactoring.get_diff() or "")[:MAX_DIFF_CHARS]
        preview_id = uuid.uuid4().hex
        with self._lock:
            self._pending[preview_id] = _PendingRename(pending, diff)
            while len(self._pending) > MAX_PREVIEWS:
                self._pending.pop(next(iter(self._pending)))
        return {
            "available": True,
            "preview_id": preview_id,
            "old_name": str(getattr(getattr(refactoring, "renamed", None), "name", "") or ""),
            "new_name": new_name,
            "changes": sorted(display_changes, key=lambda item: item["path"]),
            "diff": diff,
        }

    def apply(self, project_root: str, preview_id: str) -> dict[str, Any]:
        root = Path(project_root).expanduser().resolve(strict=True)
        with self._lock:
            preview = self._pending.get(preview_id)
            if preview is None:
                raise ValueError("Rename preview was not found, expired, or already applied.")

            for path, (expected_hash, _new_content, _mode) in preview.changes.items():
                if not path.is_relative_to(root) or not path.is_file():
                    raise ValueError("A file in this rename preview is no longer available in the workspace.")
                if _digest(path.read_bytes()) != expected_hash:
                    raise RuntimeError(f"{path.relative_to(root).as_posix()} changed after preview. Generate a new preview.")

            staged: dict[Path, Path] = {}
            originals: dict[Path, bytes] = {}
            replaced: list[Path] = []
            try:
                for path, (_expected_hash, new_content, mode) in preview.changes.items():
                    originals[path] = path.read_bytes()
                    fd, temp_name = tempfile.mkstemp(prefix=".kardec-rename-", dir=str(path.parent))
                    temp_path = Path(temp_name)
                    staged[path] = temp_path
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(new_content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.chmod(temp_path, mode)
                for path, temp_path in staged.items():
                    os.replace(temp_path, path)
                    replaced.append(path)
            except Exception:
                for path in reversed(replaced):
                    fd, temp_name = tempfile.mkstemp(prefix=".kardec-rollback-", dir=str(path.parent))
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(originals[path])
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_name, path)
                raise
            finally:
                for temp_path in staged.values():
                    try:
                        temp_path.unlink(missing_ok=True)
                    except OSError:
                        pass

            del self._pending[preview_id]
            return {"applied": True, "files": sorted(path.relative_to(root).as_posix() for path in preview.changes)}
