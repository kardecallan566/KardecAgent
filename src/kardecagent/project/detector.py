from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ProjectProfile:
    language: str
    framework: str | None
    package_manager: str | None
    commands: dict[str, str]

def detect_project(root: Path) -> ProjectProfile:
    names = {p.name for p in root.iterdir()}
    language = 'unknown'; framework = None; manager = None
    commands: dict[str, str] = {}
    if 'package.json' in names:
        language = 'javascript'
        if 'pnpm-lock.yaml' in names: manager = 'pnpm'; runner = 'pnpm'
        elif 'yarn.lock' in names: manager = 'yarn'; runner = 'yarn'
        elif 'package-lock.json' in names: manager = 'npm'; runner = 'npm'
        else: manager = 'npm'; runner = 'npm'
        try:
            import json
            package = json.loads((root / 'package.json').read_text(encoding='utf-8'))
            deps = {**package.get('dependencies', {}), **package.get('devDependencies', {})}
            if 'expo' in deps: framework = 'expo'
            elif 'react' in deps: framework = 'react'
            scripts = package.get('scripts', {})
            for key in ('test', 'lint', 'typecheck', 'build'):
                if key in scripts: commands[key] = f'{runner} run {key}'
        except (OSError, ValueError):
            pass
    elif 'pyproject.toml' in names or 'requirements.txt' in names:
        language = 'python'; manager = 'pip'
        if 'pyproject.toml' in names: framework = 'python'
        commands = {'test': 'python -m pytest'}
        if (root / 'ruff.toml').exists() or (root / 'ruff').exists(): commands['lint'] = 'ruff check .'
    elif any(p.suffix == '.ts' for p in root.rglob('*.ts')):
        language = 'typescript'
    elif any(p.suffix == '.py' for p in root.rglob('*.py')):
        language = 'python'
    return ProjectProfile(language, framework, manager, commands)

def discover_command(root: Path, kind: str) -> str | None:
    return detect_project(root).commands.get(kind)