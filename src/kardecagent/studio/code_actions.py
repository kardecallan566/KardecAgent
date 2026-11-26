"""Bounded, preview-first Python quick fixes powered by Ruff."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

MAX_SOURCE_BYTES = 1_000_000
MAX_ACTIONS = 20
MAX_EDITS_PER_ACTION = 20
MAX_RUFF_OUTPUT_BYTES = 1_000_000
RUFF_TIMEOUT_SECONDS = 6


def _context(source: str, file_path: str, project_root: str, line: int, column: int) -> tuple[Path, Path]:
    if not isinstance(source, str) or len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("Source exceeds the 1 MB code-action limit.")
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        raise ValueError("line must be a positive integer.")
    if isinstance(column, bool) or not isinstance(column, int) or column < 0:
        raise ValueError("column must be a non-negative integer.")
    root = Path(project_root).expanduser().resolve(strict=True)
    path = (root / file_path).resolve(strict=False) if not Path(file_path).is_absolute() else Path(file_path).resolve(strict=False)
    if not root.is_dir() or not path.is_relative_to(root):
        raise ValueError("Code actions are restricted to files inside the active workspace.")
    if path.suffix.lower() not in {".py", ".pyi"}:
        raise ValueError("Code Actions currently support Python files only.")
    if not path.parent.is_dir():
        raise ValueError("The target file directory does not exist.")
    return root, path


def _offset(source: str, row: int, column: int) -> int:
    lines = source.splitlines(keepends=True)
    if row < 1 or row > len(lines) + (1 if source.endswith("\\n") else 0):
        raise ValueError("Ruff returned an edit outside the source document.")
    start = sum(len(item) for item in lines[:row - 1])
    line_text = lines[row - 1] if row <= len(lines) else ""
    offset = start + column - 1
    if column < 1 or offset > start + len(line_text):
        raise ValueError("Ruff returned an invalid edit position.")
    return offset


def python_code_actions(
    source: str,
    file_path: str,
    project_root: str,
    line: int,
    column: int,
) -> dict[str, Any]:
    """Return safe Ruff fixes as source-range edits; never modifies workspace files."""
    root, path = _context(source, file_path, project_root, line, column)
    relative = path.relative_to(root).as_posix()
    try:
        completed = subprocess.run(
            [
                sys.executable, "-m", "ruff", "check",
                "--output-format", "json",
                "--stdin-filename", relative,
                "-",
            ],
            input=source,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(root),
            timeout=RUFF_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"available": False, "actions": [], "message": "Ruff excedeu o limite de 6 segundos."}
    except OSError as exc:
        return {"available": False, "actions": [], "message": f"Não foi possível iniciar Ruff: {str(exc)[:250]}"}

    if len(completed.stdout.encode("utf-8")) > MAX_RUFF_OUTPUT_BYTES:
        return {"available": False, "actions": [], "message": "A resposta do Ruff excedeu o limite de tamanho."}
    if completed.returncode not in {0, 1}:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        if "No module named ruff" in detail:
            return {
                "available": False,
                "actions": [],
                "message": "Ruff não está instalado. Instale o extra 'studio' do KardecAgent para habilitar Code Actions.",
            }
        return {"available": False, "actions": [], "message": f"Ruff não conseguiu analisar o arquivo: {detail[:400]}"}

    try:
        diagnostics = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return {"available": False, "actions": [], "message": "Ruff retornou um resultado JSON inválido."}
    if not isinstance(diagnostics, list):
        return {"available": False, "actions": [], "message": "Formato de resposta do Ruff não reconhecido."}

    actions: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        fix = diagnostic.get("fix")
        edits_data = fix.get("edits") if isinstance(fix, dict) else None
        if not isinstance(edits_data, list) or not edits_data or len(edits_data) > MAX_EDITS_PER_ACTION:
            continue
        if fix.get("applicability") != "safe":
            continue
        edits: list[dict[str, Any]] = []
        try:
            for edit in edits_data:
                location = edit["location"]
                end_location = edit["end_location"]
                start = _offset(source, int(location["row"]), int(location["column"]))
                end = _offset(source, int(end_location["row"]), int(end_location["column"]))
                replacement = edit.get("content")
                if not isinstance(replacement, str) or end < start:
                    raise ValueError("Ruff returned an invalid edit.")
                edits.append({"start": start, "end": end, "content": replacement})
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        ordered = sorted(edits, key=lambda item: (item["start"], item["end"]))
        if any(ordered[index]["end"] > ordered[index + 1]["start"] for index in range(len(ordered) - 1)):
            continue
        code = str(diagnostic.get("code") or "RUFF")[:30]
        message = str(diagnostic.get("message") or "Correção rápida")[:180]
        actions.append({
            "id": f"{code}-{len(actions)}",
            "title": f"{code}: {message}",
            "code": code,
            "applicability": "safe",
            "edits": sorted(edits, key=lambda item: item["start"], reverse=True),
        })
        if len(actions) >= MAX_ACTIONS:
            break

    return {
        "available": True,
        "actions": actions,
        "message": None if actions else "Nenhuma correção rápida segura disponível neste ponto.",
    }
