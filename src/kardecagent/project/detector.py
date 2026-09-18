from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .context import iter_project_files


@dataclass(frozen=True)
class ProjectProfile:
    kind: str
    language: str
    framework: str | None
    package_manager: str | None
    commands: dict[str, str]


def detect_project(root: Path) -> ProjectProfile:
    names = {p.name for p in root.iterdir()}
    language = "unknown"
    kind = "unknown"
    framework = None
    manager = None
    commands: dict[str, str] = {}

    if "package.json" in names:
        language = "typescript" if any(
            p.suffix in {".ts", ".tsx"} for p in iter_project_files(root)
        ) else "javascript"
        kind = "node"
        if "pnpm-lock.yaml" in names:
            manager, runner = "pnpm", "pnpm"
        elif "yarn.lock" in names:
            manager, runner = "yarn", "yarn"
        elif "package-lock.json" in names:
            manager, runner = "npm", "npm"
        else:
            manager, runner = "npm", "npm"
        try:
            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
            deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
            if "expo" in deps:
                framework, kind = "expo", "expo"
            elif "next" in deps:
                framework, kind = "next", "web"
            elif "react" in deps:
                framework, kind = "react", "web"
            scripts = package.get("scripts", {})
            for key in ("test", "lint", "typecheck", "build"):
                if key in scripts:
                    commands[key] = f"{runner} run {key}"
        except (OSError, ValueError, TypeError):
            pass

    elif "pyproject.toml" in names or "requirements.txt" in names:
        language = "python"
        kind = "python"
        manager = "pip"
        commands = {"test": "python -m pytest"}
        if (root / "ruff.toml").exists() or (root / "ruff").exists():
            commands["lint"] = "ruff check ."

    else:
        files = list(iter_project_files(root))
        if any(p.suffix.lower() == ".html" for p in files):
            language = "html"
            kind = "static-html"

    if kind == "unknown":
        files = list(iter_project_files(root))
        if any(p.suffix.lower() in {".ts", ".tsx"} for p in files):
            language, kind = "typescript", "typescript"
        elif any(p.suffix.lower() == ".py" for p in files):
            language, kind = "python", "python"

    return ProjectProfile(kind, language, framework, manager, commands)


def discover_command(root: Path, kind: str) -> str | None:
    return detect_project(root).commands.get(kind)
