"""Optional Jedi-backed semantic services for KardecAgent Studio.

The module is deliberately independent from the desktop shell so callers can
keep lexical editor features available when Jedi is not installed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_SOURCE_BYTES = 1_000_000
MAX_RESULTS = 100


def _validated_context(
    source: str, file_path: str, project_root: str, line: int, column: int
) -> tuple[Path, Path]:
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("Source exceeds the 1 MB semantic-analysis limit.")
    if line < 1 or column < 0:
        raise ValueError("Invalid editor position.")
    root = Path(project_root).expanduser().resolve(strict=True)
    path = Path(file_path).expanduser().resolve(strict=False)
    if not root.is_dir() or not path.is_relative_to(root):
        raise ValueError("The source file must be inside the project workspace.")
    if path.suffix.lower() not in {".py", ".pyi"}:
        raise ValueError("Semantic services currently support Python files only.")
    return root, path


def _location(item: Any) -> dict[str, Any]:
    module_path = getattr(item, "module_path", None)
    return {
        "name": str(getattr(item, "name", "") or ""),
        "path": str(module_path) if module_path else None,
        "line": getattr(item, "line", None),
        "column": getattr(item, "column", None),
        "description": str(getattr(item, "description", "") or "")[:500],
        "kind": str(getattr(item, "type", "") or ""),
    }


def python_semantic_request(
    action: str,
    source: str,
    file_path: str,
    project_root: str,
    line: int,
    column: int,
) -> dict[str, Any]:
    """Run one bounded semantic request; never raises optional-tool errors."""
    if action not in {"complete", "definition", "references"}:
        raise ValueError("Unsupported semantic action.")
    root, path = _validated_context(source, file_path, project_root, line, column)

    try:
        import jedi  # type: ignore[import-not-found]
    except ImportError:
        return {
            "available": False,
            "action": action,
            "items": [],
            "message": "Jedi is not installed. Install the optional 'studio' extra to enable Python semantic intelligence.",
        }

    try:
        project = jedi.Project(path=str(root), added_sys_path=[str(root)])
        script = jedi.Script(code=source, path=str(path), project=project)
        if action == "complete":
            values = script.complete(line=line, column=column)
            items = [
                {
                    "name": str(item.name),
                    "insert_text": str(item.name),
                    "kind": str(getattr(item, "type", "") or ""),
                    "detail": str(getattr(item, "description", "") or "")[:500],
                    "doc": str(item.docstring(raw=True) or "")[:1000],
                }
                for item in values[:MAX_RESULTS]
            ]
        elif action == "definition":
            values = script.goto(line=line, column=column, follow_imports=True, follow_builtin_imports=False)
            items = [_location(item) for item in values[:MAX_RESULTS]]
        else:
            values = script.get_references(line=line, column=column, include_builtins=False)
            items = [_location(item) for item in values[:MAX_RESULTS]]
        return {"available": True, "action": action, "items": items, "message": None}
    except Exception as exc:  # Jedi reports parse and project errors with several exception types.
        return {
            "available": True,
            "action": action,
            "items": [],
            "message": f"Semantic analysis failed: {type(exc).__name__}: {str(exc)[:400]}",
        }
