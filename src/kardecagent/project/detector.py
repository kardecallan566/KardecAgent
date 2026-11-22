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
    languages: tuple[str, ...] = ()


def detect_project(root: Path) -> ProjectProfile:
    """Identify the primary stack and every source language present in a workspace."""
    names = {p.name.lower() for p in root.iterdir()}
    files = list(iter_project_files(root))
    extensions = {path.suffix.lower() for path in files}
    languages: list[str] = []

    extension_languages = (
        ({".py"}, "python"), ({".js", ".mjs", ".cjs", ".jsx"}, "javascript"),
        ({".ts", ".tsx", ".mts", ".cts"}, "typescript"), ({".rs"}, "rust"),
        ({".go"}, "go"), ({".java"}, "java"), ({".kt", ".kts"}, "kotlin"),
        ({".cs"}, "csharp"), ({".c", ".h"}, "c"), ({".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"}, "cpp"),
        ({".php"}, "php"), ({".rb"}, "ruby"), ({".swift"}, "swift"),
        ({".dart"}, "dart"), ({".scala"}, "scala"), ({".lua"}, "lua"),
        ({".sh", ".bash", ".zsh"}, "shell"), ({".html", ".htm"}, "html"),
        ({".css", ".scss", ".sass", ".less"}, "css"), ({".sql"}, "sql"),
        ({".vue"}, "vue"), ({".svelte"}, "svelte"), ({".md", ".mdx"}, "markdown"),
    )
    for suffixes, language_name in extension_languages:
        if extensions.intersection(suffixes) and language_name not in languages:
            languages.append(language_name)

    language = languages[0] if languages else "unknown"
    kind = "static-html" if language == "html" else language
    framework = None
    manager = None
    commands: dict[str, str] = {}

    if "package.json" in names:
        # A package manifest is sufficient evidence of a JavaScript workspace,
        # even when its source files have not been created yet.
        if "typescript" not in languages:
            if "javascript" not in languages:
                languages.insert(0, "javascript")
            language = "javascript"
        else:
            language = "typescript"
        try:
            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            package = {}
        deps = {**(package.get("dependencies") or {}), **(package.get("devDependencies") or {})}
        if "pnpm-lock.yaml" in names:
            manager, runner = "pnpm", "pnpm"
        elif "yarn.lock" in names:
            manager, runner = "yarn", "yarn"
        else:
            manager, runner = "npm", "npm"
        if "expo" in deps:
            framework, kind = "expo", "expo"
        elif "next" in deps:
            framework, kind = "next", "web"
        elif "vite" in deps:
            framework, kind = "vite", "web"
        elif "react" in deps:
            framework, kind = "react", "web"
        elif "vue" in deps:
            framework, kind = "vue", "web"
        elif "svelte" in deps:
            framework, kind = "svelte", "web"
        else:
            kind = "node"
        scripts = package.get("scripts") or {}
        for key in ("test", "lint", "typecheck", "build", "format"):
            if key in scripts:
                commands[key] = f"{runner} run {key}"

    if "pyproject.toml" in names or "requirements.txt" in names or "setup.py" in names:
        commands.setdefault("test", "python -m pytest")
        if "python" not in languages:
            languages.append("python")
        if kind == "unknown":
            kind, language, manager = "python", "python", "pip"
        if "ruff" in names or any((root / name).exists() for name in ("ruff.toml", ".ruff.toml")):
            commands["lint"] = "ruff check ."
    if "cargo.toml" in names:
        commands.setdefault("test", "cargo test")
        commands.setdefault("lint", "cargo clippy --all-targets")
        if "rust" not in languages:
            languages.append("rust")
        if kind == "unknown":
            kind, language = "rust", "rust"
    if "go.mod" in names:
        commands.setdefault("test", "go test ./...")
        commands.setdefault("lint", "go vet ./...")
        if "go" not in languages:
            languages.append("go")
        if kind == "unknown":
            kind, language = "go", "go"
    if any(name in names for name in ("pom.xml", "build.gradle", "build.gradle.kts")):
        if "java" not in languages and "kotlin" not in languages:
            languages.append("java")
        if kind == "unknown":
            kind, language = "jvm", languages[-1]
        if "pom.xml" in names:
            commands.setdefault("test", "mvn test")
        elif "gradlew" in names:
            commands.setdefault("test", "./gradlew test")
        else:
            commands.setdefault("test", "gradle test")
    if any(name.endswith(".csproj") or name.endswith(".sln") for name in names):
        if "csharp" not in languages:
            languages.append("csharp")
        if kind == "unknown":
            kind, language = "dotnet", "csharp"
        commands.setdefault("test", "dotnet test")
    if "composer.json" in names:
        if "php" not in languages:
            languages.append("php")
        if kind == "unknown":
            kind, language = "php", "php"
    if "gemfile" in names:
        if "ruby" not in languages:
            languages.append("ruby")
        if kind == "unknown":
            kind, language = "ruby", "ruby"
    if "pubspec.yaml" in names:
        if "dart" not in languages:
            languages.append("dart")
        if kind == "unknown":
            kind, language = "dart", "dart"
    if "package.swift" in names:
        if "swift" not in languages:
            languages.append("swift")
        if kind == "unknown":
            kind, language = "swift", "swift"
    if "cmakelists.txt" in names or "makefile" in names:
        if extensions.intersection({".c", ".h"}) and "c" not in languages:
            languages.append("c")
        if extensions.intersection({".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"}) and "cpp" not in languages:
            languages.append("cpp")
        if kind == "unknown":
            kind = "native"

    if language == "unknown" and languages:
        language = languages[0]
    if kind == "unknown" and language != "unknown":
        kind = "static-html" if language == "html" else language

    return ProjectProfile(kind, language, framework, manager, commands, tuple(languages))


def discover_command(root: Path, kind: str) -> str | None:
    return detect_project(root).commands.get(kind)
