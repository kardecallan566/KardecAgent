from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class TestResult:
    kind: str
    available: bool
    passed: bool
    returncode: int | None = None
    timed_out: bool = False
    summary: str = ""
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    output: str = ""


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _tail_errors(text: str, limit: int = 8) -> tuple[str, ...]:
    lines = _lines(text)
    patterns = ("error", "failed", "failure", "traceback", "exception", "typeerror")
    matches = [line for line in lines if any(p in line.lower() for p in patterns)]
    return tuple(matches[-limit:])


def _warnings(text: str, limit: int = 6) -> tuple[str, ...]:
    lines = _lines(text)
    return tuple(line for line in lines if "warning" in line.lower())[-limit:]


def parse_test_result(kind: str, stdout: str, stderr: str, returncode: int, timed_out: bool) -> TestResult:
    combined = (stdout or "") + "\n" + (stderr or "")
    if timed_out:
        return TestResult(kind, True, False, returncode, True, "Command timed out.", _tail_errors(combined), _warnings(combined), combined[-6000:])
    if returncode == 0:
        return TestResult(kind, True, True, returncode, False, _success_summary(kind, combined), (), _warnings(combined), combined[-6000:])
    errors = _tail_errors(combined)
    return TestResult(kind, True, False, returncode, False, _failure_summary(kind, combined), errors, _warnings(combined), combined[-6000:])


def _success_summary(kind: str, text: str) -> str:
    lower = text.lower()
    if kind == "test":
        match = re.search(r"(\d+)\s+(?:passed|passing)", lower)
        return f"{match.group(1)} tests passed." if match else "Tests completed successfully."
    if kind == "typecheck":
        return "Type checking completed successfully."
    if kind == "lint":
        return "Lint completed successfully."
    if kind == "build":
        return "Build completed successfully."
    return f"{kind} check completed successfully."


def _failure_summary(kind: str, text: str) -> str:
    lower = text.lower()
    if kind == "test":
        match = re.search(r"(\d+)\s+(?:failed|failing)", lower)
        if match:
            return f"{match.group(1)} tests failed."
    if kind == "typecheck":
        match = re.search(r"(\d+)\s+errors?", lower)
        if match:
            return f"Type checking failed with {match.group(1)} errors."
    return f"{kind} check failed (exit code != 0)."


def format_for_model(result: TestResult) -> dict:
    return {
        "kind": result.kind,
        "available": result.available,
        "passed": result.passed,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "summary": result.summary,
        "errors": list(result.errors),
        "warnings": list(result.warnings),
        "output_tail": result.output,
    }
