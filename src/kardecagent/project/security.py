from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

IGNORED_PARTS = {".git", "node_modules", ".venv", "venv", "dist", "build", ".expo", "__pycache__"}

PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("generic_secret_assignment", re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password)\s*[:=]\s*["'][^"']{8,}["']")),
    ("generic_secret_assignment", re.compile(r'(?i)\b(?:api[_-]?key|secret|token|password)\s*[:=]\s*["'][^"']{8,}["']')),
)

@dataclass(frozen=True)
class SecurityFinding:
    kind: str
    path: str
    line: int
    message: str

@dataclass(frozen=True)
class SecurityScanResult:
    passed: bool
    findings: list[SecurityFinding]

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "findings": [f.__dict__ for f in self.findings],
        }

def scan_project(root: Path, max_findings: int = 100) -> SecurityScanResult:
    findings: list[SecurityFinding] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".zip", ".gz"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(root).as_posix()
        for line_no, line in enumerate(text.splitlines(), 1):
            for kind, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append(SecurityFinding(kind, rel, line_no, "Potential hardcoded secret or credential."))
                    if len(findings) >= max_findings:
                        return SecurityScanResult(False, findings)
    return SecurityScanResult(not findings, findings)
