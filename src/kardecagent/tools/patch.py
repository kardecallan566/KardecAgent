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


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _parse_header_path(line: str) -> str | None:
    if not (line.startswith("--- ") or line.startswith("+++ ")):
        raise ValueError("Invalid patch file header.")
    token = line[4:].split("\t", 1)[0].strip()
    if token == "/dev/null":
        return None
    if token.startswith(("a/", "b/")):
        token = token[2:]
    path = Path(token)
    if not token or path.is_absolute() or ".." in path.parts:
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
    if old_start == 0 and old_count != 0:
        raise ValueError("Old hunk start 0 is valid only for an empty range.")
    if new_start == 0 and new_count != 0:
        raise ValueError("New hunk start 0 is valid only for an empty range.")

    i = start + 1
    hunk_lines: list[tuple[str, str]] = []
    no_newline_after: int | None = None
    old_seen = new_seen = 0

    while i < len(lines) and (old_seen < old_count or new_seen < new_count):
        line = lines[i]
        if line == "\\ No newline at end of file":
            if not hunk_lines:
                raise ValueError("No-newline marker cannot start a hunk.")
            if no_newline_after is not None:
                raise ValueError("Duplicate no-newline marker in hunk.")
            no_newline_after = len(hunk_lines) - 1
            i += 1
            continue
        if not line or line[0] not in " +-":
            raise ValueError("Invalid hunk line.")
        kind, text = line[0], line[1:]
        hunk_lines.append((kind, text))
        if kind in " -":
            old_seen += 1
        if kind in " +":
            new_seen += 1
        i += 1

    if old_seen != old_count or new_seen != new_count:
        raise ValueError("Hunk line count does not match its header.")
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
    return content.splitlines(keepends=True) if content else []


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
    previous_old_end = 0
    offset = 0

    for hunk in hunks:
        if hunk.old_count == 0:
            old_pos = hunk.old_start
        else:
            old_pos = hunk.old_start - 1
        if old_pos < previous_old_end:
            raise ValueError(f"Overlapping hunks in {path}.")
        pos = old_pos + offset
        if pos < 0 or pos > len(output):
            raise ValueError(f"Out-of-range hunk in {path}.")

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
            if hunk.no_newline_after == index and replacement:
                replacement[-1] = replacement[-1].rstrip("\r\n")

        output[pos:cursor] = replacement
        offset += len(replacement) - (cursor - pos)
        previous_old_end = old_pos + hunk.old_count

    return "".join(output)


def apply_unified_patch(root: Path, patch: str) -> PatchResult:
    """Apply a strict unified diff transactionally to UTF-8 text files."""
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
                if fs.safe_path(path).exists():
                    raise ValueError(f"Cannot create existing file: {path}.")
                original = ""
                if hunks[0].old_start != 0 or hunks[0].old_count != 0:
                    raise ValueError(f"New file {path} must start at -0,0.")
            else:
                original = fs.read_file(old_path)

            if new_path is None and not fs.safe_path(path).is_file():
                raise FileNotFoundError(path)

            result = _apply_hunks(original, hunks, path)
            if new_path is None:
                if result:
                    raise ValueError(f"Deletion patch for {path} did not remove the complete file.")
                prepared[path] = None
            else:
                prepared[path] = result

        for path, content in prepared.items():
            target = fs.safe_path(path)
            if content is None:
                if target.is_symlink() or (target.exists() and not target.is_file()):
                    raise ValueError(f"Cannot delete non-regular file: {path}")
                target.unlink()
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
                expected = content.encode("utf-8")
                actual = target.read_bytes()
                if actual != expected:
                    raise RuntimeError(f"Post-patch content verification failed: {path}.")
                digests[path] = hashlib.sha256(actual).hexdigest()

        return PatchResult(True, tuple(prepared), None, digests)
    except (OSError, UnicodeError, ValueError) as exc:
        return PatchResult(False, (), str(exc), {})
