from __future__ import annotations

from dataclasses import dataclass

SECURITY_REQUIREMENTS = [
    "Do not store passwords or secrets in plaintext.",
    "Do not hardcode credentials, API keys, tokens, private keys, or secrets.",
    "Use established, maintained cryptographic/authentication libraries instead of custom cryptography.",
    "Protect authentication and authorization boundaries and validate untrusted input.",
    "Avoid leaking credentials, tokens, session identifiers, or sensitive user data in logs and error messages.",
    "Review session/token lifecycle, expiration, revocation, and storage when applicable.",
    "Check brute-force/rate-limit protections when applicable.",
    "Run relevant tests, static analysis, and dependency/security checks when available.",
]

@dataclass(frozen=True)
class SecurityAssessment:
    level: str
    matched_signals: tuple[str, ...] = ()

    @property
    def sensitive(self) -> bool:
        return self.level in {"sensitive", "high_risk"}

    @property
    def high_risk(self) -> bool:
        return self.level == "high_risk"

def assess_security(task: str) -> SecurityAssessment:
    text = task.lower()
    high = (
        "private key", "signing key", "root credential", "admin credential",
        "payment", "financial", "production secret", "production credential",
        "oauth provider", "identity provider",
    )
    sensitive = (
        "auth", "authentication", "authorization", "password", "credential",
        "secret", "token", "session", "jwt", "oauth", "permission",
        "access control", "api key", "encryption", "cryptograph", "security",
    )
    high_hits = tuple(x for x in high if x in text)
    if high_hits:
        return SecurityAssessment("high_risk", high_hits)
    hits = tuple(x for x in sensitive if x in text)
    if hits:
        return SecurityAssessment("sensitive", hits)
    return SecurityAssessment("normal")

def security_requirements_for(level: str) -> list[str]:
    return list(SECURITY_REQUIREMENTS) if level in {"sensitive", "high_risk"} else []
