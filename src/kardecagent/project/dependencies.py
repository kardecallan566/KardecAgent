from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import re

@dataclass(frozen=True)
class DependencyAudit:
    ecosystem: str
    available: bool
    passed: bool
    command: str | None
    summary: str
    raw_output: str = ""

def _has(root: Path, *names: str) -> str | None:
    for name in names:
        if (root / name).exists():
            return name
    return None

def discover_audit_command(root: Path) -> tuple[str, str] | None:
    package = _has(root, "package.json")
    if package:
        if (root / "pnpm-lock.yaml").exists():
            return "npm", "pnpm audit --json"
        if (root / "yarn.lock").exists():
            return "npm", "yarn npm audit --json"
        return "npm", "npm audit --json"
    if _has(root, "requirements.txt", "pyproject.toml", "Pipfile", "poetry.lock"):
        return "python", "python -m pip_audit -f json"
    return None

def parse_npm_audit(output: str) -> tuple[bool, str]:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return False, "Dependency audit returned non-JSON output; audit result is not trusted."
    metadata = data.get("metadata", {})
    vulnerabilities = metadata.get("vulnerabilities", {})
    total = sum(int(v or 0) for v in vulnerabilities.values() if isinstance(v, (int, float)))
    if total == 0:
        return True, "No known npm vulnerabilities were reported."
    return False, f"npm audit reported {total} known vulnerability entries."

def parse_pip_audit(output: str) -> tuple[bool, str]:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return False, "pip-audit returned non-JSON output; audit result is not trusted."
    if not isinstance(data, list):
        return False, "pip-audit returned an unexpected JSON shape."
    vulnerable = [x for x in data if isinstance(x, dict) and x.get("vulns")]
    if not vulnerable:
        return True, "No known Python package vulnerabilities were reported."
    return False, f"pip-audit reported vulnerabilities in {len(vulnerable)} package(s)."

def summarize_audit(ecosystem: str, stdout: str, stderr: str, returncode: int, timed_out: bool) -> DependencyAudit:
    output = stdout or stderr
    if timed_out:
        return DependencyAudit(ecosystem, True, False, None, "Dependency audit timed out.", output)
    if ecosystem == "npm":
        passed, summary = parse_npm_audit(output)
    else:
        passed, summary = parse_pip_audit(output)
    if returncode != 0 and passed:
        passed = False
        summary = "Dependency audit exited with an error; result is not trusted."
    return DependencyAudit(ecosystem, True, passed, None, summary, output)
