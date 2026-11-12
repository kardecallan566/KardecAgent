"""Deterministic evaluation helpers for agent run verification."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvaluationResult:
    passed: bool
    score: float
    checks_total: int
    checks_passed: int
    failures: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "checks_total": self.checks_total,
            "checks_passed": self.checks_passed,
            "failures": list(self.failures),
            "errors": list(self.errors),
            "metadata": self.metadata,
        }


def evaluate_verification(verification: dict[str, Any] | None) -> EvaluationResult:
    """Score structured verification data without an LLM self-judgment.

    Expected shape: {"checks": [{"name": str, "passed": bool, ...}], "errors": [...]}.
    Missing or malformed checks are not considered evidence of success.
    """
    payload = verification if isinstance(verification, dict) else {}
    raw_checks = payload.get("checks", [])
    checks = raw_checks if isinstance(raw_checks, list) else []
    valid_checks = [item for item in checks if isinstance(item, dict) and isinstance(item.get("passed"), bool)]
    passed = [item for item in valid_checks if item["passed"]]
    failures = tuple(str(item.get("name", f"check-{i + 1}")) for i, item in enumerate(valid_checks) if not item["passed"])
    raw_errors = payload.get("errors", [])
    errors = tuple(str(error) for error in raw_errors) if isinstance(raw_errors, list) else ()
    total = len(valid_checks)
    score = len(passed) / total if total else 0.0
    success = total > 0 and len(passed) == total and not errors
    return EvaluationResult(
        passed=success,
        score=round(score, 4),
        checks_total=total,
        checks_passed=len(passed),
        failures=failures,
        errors=errors,
        metadata={"invalid_checks_ignored": len(checks) - total},
    )
