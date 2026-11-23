from pathlib import Path

IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules",
    "dist", "build", ".next", ".expo", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".kardecagent",
}


def iter_project_files(root: Path):
    """Yield project files without traversing generated or internal state folders."""
    root = root.resolve()
    for path in root.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            yield path


def project_snapshot(root: Path, limit: int = 500) -> list[str]:
    """Return a deterministic, bounded list of workspace-relative file paths."""
    if limit <= 0:
        return []
    files = (p.relative_to(root).as_posix() for p in iter_project_files(root))
    return sorted(files)[:limit]
