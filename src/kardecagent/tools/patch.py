from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .filesystem import ProjectFilesystem


@dataclass(frozen=True)
class PatchResult:
    applied: bool
    changed_files: tuple[str, ...]
    error: str | None = None


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_PATH_RE = re.compile(r"^(?:---|\+\+\+) [ab/](.+?)(?:\t.*)?$")


def _parse_path(line: str) -> str:
    match = _PATH_RE.match(line)
    if not match:
        raise ValueError("Invalid patch file header.")
    path = match.group(1).strip()
    if path == "/dev/null":
        raise ValueError("New/deleted files are not supported by patch editing yet.")
    return path


def apply_unified_patch(root: Path, patch: str) -> PatchResult:
    """Apply a strict unified diff to existing UTF-8 text files.

    The patch must contain paired ---/+++ headers and standard @@ hunks.
    Changes are applied only when every context/removal line matches exactly.
    """
    if not isinstance(patch, str) or not patch.strip():
        return PatchResult(False, (), "Patch is empty.")
    lines = patch.splitlines()
    i = 0
    changes: dict[str, list[str]] = {}
    while i < len(lines):
        if not lines[i].startswith("--- "):
            return PatchResult(False, (), "Expected unified diff file header.")
        old_path = _parse_path(lines[i])
        i += 1
        if i >= len(lines) or not lines[i].startswith("+++ "):
            return PatchResult(False, (), "Missing +++ file header.")
        new_path = _parse_path(lines[i])
        if old_path != new_path:
            return PatchResult(False, (), "Rename patches are not supported.")
        i += 1
        fs = ProjectFilesystem(root)
        original = fs.read_file(old_path).splitlines(keepends=True)
        output = list(original)
        offset = 0
        saw_hunk = False
        while i < len(lines) and not lines[i].startswith("--- "):
            if not lines[i].startswith("@@ "):
                return PatchResult(False, (), "Expected hunk header.")
            match = _HUNK_RE.match(lines[i])
            if not match:
                return PatchResult(False, (), "Invalid hunk header.")
            old_start = int(match.group(1))
            old_count = int(match.group(2) or "1")
            i += 1
            hunk: list[tuple[str, str]] = []
            while i < len(lines) and not lines[i].startswith(("@@ ", "--- ")):
                line = lines[i]
                if line == "\ No newline at end of file":
                    i += 1
                    continue
                if not line or line[0] not in " +-":
                    return PatchResult(False, (), "Invalid hunk line.")
                hunk.append((line[0], line[1:]))
                i += 1
            context_count = sum(1 for kind, _ in hunk if kind in " -")
            if context_count != old_count:
                return PatchResult(False, (), "Hunk line count does not match its header.")
            pos = old_start - 1 + offset
            cursor = pos
            replacement: list[str] = []
            for kind, text in hunk:
                expected = text + "\n"
                if kind in " -":
                    if cursor >= len(output) or output[cursor].rstrip("\n\r") != text.rstrip("\n\r"):
                        return PatchResult(False, (), f"Patch context mismatch in {old_path}.")
                    if kind == " ":
                        replacement.append(output[cursor])
                    cursor += 1
                else:
                    replacement.append(expected)
            output[pos:cursor] = replacement
            offset += len(replacement) - (cursor - pos)
            saw_hunk = True
        if not saw_hunk:
            return PatchResult(False, (), f"No hunks found for {old_path}.")
        changes[old_path] = output

    for path, content in changes.items():
        fs = ProjectFilesystem(root)
        fs.write_file(path, "".join(content))
    return PatchResult(True, tuple(changes), None)
