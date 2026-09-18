from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .filesystem import ProjectFilesystem


@dataclass(frozen=True)
class PatchResult:
    applied: bool
    changed_files: tuple[str, ...]
    error: str | None = None
    content_sha256: dict[str, str] | None = None


@dataclass(frozen=True)
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[tuple[str, str], ...]
    no_newline_after: int | None = None


_HUNK_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)


def _parse_header_path(line: str) -> str | None:
    if not (line.startswith("--- ") or line.startswith("+++ ")):
        raise ValueError("Invalid patch file header.")
    token = line[4:].split("\t", 1)[0].strip()
    if token == "/dev/null":
        return None
    if token.startswith(("a/", "b/")):
        token = token[2:]
    if not token or token.startswith("/") or Path(token).is_absolute() or ".." in Path(token).parts:
        raise ValueError("Invalid or unsafe patch path.")
    return token


def _parse_hunk(lines: list[str], start: int) -> tuple[_Hunk, int]:
    match = _HUNK_RE.match(lines[start])
    if not match:
        raise ValueError("Invalid hunk header.")
    old_start = int(match.group(1))
    old_count = int(match.group(2) or "1")
    new_start = int(match.group(3))
    new_count = int(match.group(4) or "1")
    i = start + 1
    hunk_lines: list[tuple[str, str]] = []
    no_newline_after: int | None = None
    while i < len(lines) and not lines[i].startswith(("@@ ", "--- ")):
        line = lines[i]
        if line == "\\ No newline at end of file":
            if not hunk_lines:
                raise ValueError("No-newline marker cannot start a hunk.")
            no_newline_after = len(hunk_lines) - 1
            i += 1
            continue
        if not line or line[0] not in " +-":
            raise ValueError("Invalid hunk line.")
        hunk_lines.append((line[0], line[1:]))
        i += 1

    old_lines = sum(1 for kind, _ in hunk_lines if kind in " -")
    new_lines = sum(1 for kind, _ in hunk_lines if kind in " +")
    if old_lines != old_count or new_lines != new_count:
        raise ValueError("Hunk line count does not match its header.")
    if no_newline_after is not None and no_newline_after >= len(hunk_lines):
        raise ValueError("Invalid no-newline marker.")
    return _Hunk(old_start, old_count, new_start, new_count, tuple(hunk_lines), no_newline_after), i


def _parse_patch(patch: str) -> list[tuple[str | None, str | None, tuple[_Hunk, ...]]]:
    lines = patch.splitlines()
    if not lines:
        raise ValueError("Patch is empty.")
    files: list[tuple[str | None, str | None, tuple[_Hunk, ...]]] = []
    i = 0
    seen: set[str] = set()
    while i < len(lines):
        if not lines[i].startswith("--- "):
            raise ValueError("Expected unified diff file header.")
        old_path = _parse_header_path(lines[i])
        i += 1
        if i >= len(lines) or not lines[i].startswith("+++ "):
            raise ValueError("Missing +++ file header.")
        new_path = _parse_header_path(lines[i])
        i += 1
        if old_path is None and new_path is None:
            raise ValueError("A patch cannot have both files set to /dev/null.")
        if old_path is not None and new_path is not None and old_path != new_path:
            raise ValueError("Rename patches are not supported.")
        key = new_path or old_path
        assert key is not None
        if key in seen:
            raise ValueError(f"Duplicate file section: {key}.")
        seen.add(key)
        hunks: list[_Hunk] = []
        while i < len(lines) and not lines[i].startswith("--- "):
            if not lines[i].startswith("@@ "):
                raise ValueError("Expected hunk header.")
            hunk, i = _parse_hunk(lines, i)
            hunks.append(hunk)
        if not hunks:
            raise ValueError(f"No hunks found for {key}.")
        files.append((old_path, new_path, tuple(hunks)))
    return files


def _split_content(content: str) -> list[str]:
    if not content:
        return []
    return content.splitlines(keepends=True)


def _line_text(line: str) -> str:
    return line.rstrip("\r\n")


def _newline_for(lines: list[str], index: int) -> str:
    if 0 <= index < len(lines):
        ending = lines[index][len(lines[index].rstrip("\r\n")):]
        if ending:
            return ending
    for line in lines:
        ending = line[len(line.rstrip("\r\n")):]
        if ending:
            return ending
    return "\n"


def _apply_hunks(original: str, hunks: tuple[_Hunk, ...], path: str) -> str:
    output = _split_content(original)
    previous_end = 0
    offset = 0

    for hunk in hunks:
        pos = hunk.old_start - 1 + offset
        if hunk.old_start < 1 and not (hunk.old_start == 0 and hunk.old_count == 0):
            raise ValueError(f"Invalid hunk position in {path}.")
        if pos < previous_end or pos > len(output):
            raise ValueError(f"Overlapping or out-of-range hunk in {path}.")

        cursor = pos
        replacement: list[str] = []
        for index, (kind, text) in enumerate(hunk.lines):
            if kind in " -":
                if cursor >= len(output) or _line_text(output[cursor]) != text:
                    raise ValueError(f"Patch context mismatch in {path}.")
                if kind == " ":
                    replacement.append(output[cursor])
                cursor += 1
            else:
                replacement.append(text + _newline_for(output, cursor))
            if hunk.no_newline_after == index:
                if replacement:
                    replacement[-1] = replacement[-1].rstrip("\r\n")
                elif cursor > pos:
                    output[cursor - 1] = output[cursor - 1].rstrip("\r\n")

        output[pos:cursor] = replacement
        previous_end = pos + len(replacement)
        offset += len(replacement) - (cursor - pos)

    return "".join(output)


def apply_unified_patch(root: Path, patch: str) -> PatchResult:
    """Apply a strict unified diff transactionally to UTF-8 text files.

    Supports multiple file sections plus creation/deletion through /dev/null.
    Every hunk is validated before any file is written. The resulting bytes are
    hashed after writing and compared with the in-memory result.
    """
    if not isinstance(patch, str) or not patch.strip():
        return PatchResult(False, (), "Patch is empty.", {})

    try:
        sections = _parse_patch(patch)
        fs = ProjectFilesystem(root)
        prepared: dict[str, str | None] = {}

        for old_path, new_path, hunks in sections:
            path = new_path or old_path
            assert path is not None
            if old_path is None:
                original = ""
                if hunks[0].old_start != 0 or hunks[0].old_count != 0:
                    raise ValueError(f"New file {path} must start at -0,0.")
            else:
                original = fs.read_file(old_path)
            if new_path is None:
                expected_old = sum(h.old_count for h in hunks)
                if expected_old != len(_split_content(original)):
                    raise ValueError(f"Deletion patch for {path} does not cover the complete file.")
            result = _apply_hunks(original, hunks, path)
            if new_path is None:
                if result:
                    raise ValueError(f"Deletion patch for {path} did not remove the complete file.")
                prepared[path] = None
            else:
                prepared[path] = result

        for path, content in prepared.items():
            if content is None:
                target = fs.safe_path(path)
                if target.exists() and target.is_file():
                    target.unlink()
                elif target.exists():
                    raise ValueError(f"Cannot delete non-regular file: {path}")
            else:
                fs.write_file(path, content)

        digests: dict[str, str] = {}
        for path, content in prepared.items():
            target = fs.safe_path(path)
            if content is None:
                if target.exists():
                    raise RuntimeError(f"Post-patch verification failed: {path} still exists.")
                digests[path] = hashlib.sha256(b"").hexdigest()
            else:
                actual = target.read_bytes()
                expected = content.encode("utf-8")
                if actual != expected:
                    raise RuntimeError(f"Post-patch content verification failed: {path}.")
                digests[path] = hashlib.sha256(actual).hexdigest()

        return PatchResult(True, tuple(prepared), None, digests)
    except (OSError, UnicodeError, ValueError) as exc:
        return PatchResult(False, (), str(exc), {})

