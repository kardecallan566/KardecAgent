from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

class SecurityReviewError(ValueError):
    pass

@dataclass(frozen=True)
class SecurityFinding:
    severity: str
    category: str
    message: str
    recommendation: str
    path: str | None = None
    line: int | None = None

@dataclass(frozen=True)
class SecurityReview:
    status: str
    findings: list[SecurityFinding]
    summary: str

    @property
    def passed(self) -> bool:
        return self.status == 'pass' and not self.findings

    def as_dict(self) -> dict[str, Any]:
        return {'status': self.status, 'summary': self.summary, 'findings': [f.__dict__ for f in self.findings]}

def parse_security_review(content: str) -> SecurityReview:
    text = content.strip()
    if text.startswith('```'):
        raise SecurityReviewError('markdown code fences are not allowed')
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SecurityReviewError('invalid security review JSON: ' + exc.msg) from exc
    if not isinstance(payload, dict):
        raise SecurityReviewError('security review must be a JSON object')
    status, summary, raw = payload.get('status'), payload.get('summary'), payload.get('findings', [])
    if status not in {'pass', 'findings'}:
        raise SecurityReviewError('status must be pass or findings')
    if not isinstance(summary, str) or not summary.strip():
        raise SecurityReviewError('security review requires a summary')
    if not isinstance(raw, list):
        raise SecurityReviewError('findings must be a list')
    findings = []
    for item in raw:
        if not isinstance(item, dict):
            raise SecurityReviewError('each finding must be an object')
        required = {'severity', 'category', 'message', 'recommendation'}
        if not required.issubset(item):
            raise SecurityReviewError('finding is missing required fields')
        if item['severity'] not in {'low', 'medium', 'high', 'critical'}:
            raise SecurityReviewError('finding severity is invalid')
        if not all(isinstance(item[k], str) and item[k].strip() for k in required):
            raise SecurityReviewError('finding text fields must be non-empty strings')
        path, line = item.get('path'), item.get('line')
        if path is not None and not isinstance(path, str):
            raise SecurityReviewError('finding path must be a string')
        if line is not None and (isinstance(line, bool) or not isinstance(line, int) or line < 1):
            raise SecurityReviewError('finding line must be a positive integer')
        findings.append(SecurityFinding(item['severity'], item['category'], item['message'], item['recommendation'], path, line))
    if status == 'pass' and findings:
        raise SecurityReviewError('a passing review cannot contain findings')
    if status == 'findings' and not findings:
        raise SecurityReviewError('a findings review must contain findings')
    return SecurityReview(status, findings, summary)

def security_review_instructions() -> str:
    return ('Review the implemented changes independently. Focus on authentication, authorization, secrets, credentials, sessions, tokens, input validation, cryptography, access control, sensitive logging, dependency usage, and privilege boundaries. Do not claim tests prove security. Return ONLY JSON with status pass/findings, summary, and structured findings. Use pass only when no security findings were identified.')