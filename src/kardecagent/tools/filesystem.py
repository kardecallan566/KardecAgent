from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


class ProjectFilesystem:
    """Filesystem access constrained to one project root.

    Mutations use same-directory temporary files and atomic replacement. Every
    write is read back before success is reported, so callers never need to
    treat an unchecked write as completed.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def safe_path(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Path escapes the configured project root.") from exc
        return candidate

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def file_fingerprint(self, relative_path: str) -> dict:
        path = self.safe_path(relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        data = path.read_bytes()
        return {
            "path": Path(relative_path).as_posix(),
            "sha256": self._digest(data),
            "size_bytes": len(data),
            "encoding": "utf-8",
        }

    def read_file(self, relative_path: str) -> str:
        path = self.safe_path(relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path.read_text(encoding="utf-8")

    def _atomic_write(self, path: Path, content: str) -> None:
        if path.exists() and not path.is_file():
            raise IsADirectoryError(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = content.encode("utf-8")
        original_mode = None
        if path.exists():
            original_mode = path.stat().st_mode

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".kardecagent-tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if original_mode is not None:
                os.chmod(temporary, original_mode)
            os.replace(temporary, path)
            # Verify the bytes on disk, not merely the in-memory value.
            actual = path.read_bytes()
            if actual != encoded:
                raise IOError(f"post-write verification failed for {path.name}")
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def write_file(self, relative_path: str, content: str) -> None:
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        path = self.safe_path(relative_path)
        self._atomic_write(path, content)

    def create_directory(self, relative_path: str) -> bool:
        path = self.safe_path(relative_path)
        if path.exists() and not path.is_dir():
            raise NotADirectoryError(relative_path)
        created = not path.exists()
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise IOError(f"directory verification failed for {relative_path}")
        return created

    def append_file(self, relative_path: str, content: str) -> None:
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        path = self.safe_path(relative_path)
        if path.exists() and not path.is_file():
            raise IsADirectoryError(relative_path)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        expected = before + content
        self._atomic_write(path, expected)
        actual = self.read_file(relative_path)
        if actual != expected:
            raise IOError(f"append verification failed for {relative_path}")

    def replace_in_file(
        self,
        relative_path: str,
        find: str,
        replacement: str,
        replace_all: bool = False,
    ) -> int:
        if not find:
            raise ValueError("find must not be empty")
        path = self.safe_path(relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        original = path.read_text(encoding="utf-8")
        count = original.count(find)
        if count == 0:
            return 0
        expected = (
            original.replace(find, replacement)
            if replace_all
            else original.replace(find, replacement, 1)
        )
        self._atomic_write(path, expected)
        actual = self.read_file(relative_path)
        if actual != expected:
            raise IOError(f"replacement verification failed for {relative_path}")
        return count if replace_all else 1

    def list_files(self, limit: int = 500, relative_path: str = ".") -> list[str]:
        from ..project.context import iter_project_files

        base = self.safe_path(relative_path)
        if not base.is_dir():
            raise NotADirectoryError(relative_path)
        files = []
        for path in iter_project_files(self.root):
            try:
                path.relative_to(base)
            except ValueError:
                continue
            files.append(path.relative_to(self.root).as_posix())
        return sorted(files)[:limit]
