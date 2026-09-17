from pathlib import Path

IGNORED_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "dist", "build", ".next", ".expo", "__pycache__", ".pytest_cache", ".mypy_cache"}

def iter_project_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and not any(part in IGNORED_DIRS for part in path.parts): yield path

def project_snapshot(root: Path, limit: int = 500) -> list[str]:
    files = [p.relative_to(root).as_posix() for p in iter_project_files(root)]
    return sorted(files[:limit])
