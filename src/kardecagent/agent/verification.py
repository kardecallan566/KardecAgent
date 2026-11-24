from __future__ import annotations

from typing import Any, Iterable


def evaluate_completion_evidence(
    criteria: Iterable[str],
    supplied: Any,
) -> dict[str, Any]:
    """Validate one explicit evidence item for every approved completion criterion.

    Legacy string lists remain supported and are matched positionally. New callers
    should send objects with explicit criterion/evidence fields.
    """
    expected = [str(item).strip() for item in criteria if str(item).strip()]
    if not isinstance(supplied, list) or not supplied:
        return {
            "valid": False,
            "items": [],
            "missing": expected,
            "unexpected": [],
            "reason": "completion evidence must be a non-empty list",
        }

    items: list[dict[str, str]] = []
    if all(isinstance(item, str) for item in supplied):
        if len(supplied) != len(expected):
            return {
                "valid": False,
                "items": [],
                "missing": expected[len(supplied):] if len(supplied) < len(expected) else [],
                "unexpected": [],
                "reason": "provide exactly one evidence item for each completion criterion",
            }
        items = [
            {"criterion": criterion, "evidence": evidence.strip()}
            for criterion, evidence in zip(expected, supplied)
        ]
    elif all(isinstance(item, dict) for item in supplied):
        seen: set[str] = set()
        unexpected: list[str] = []
        for item in supplied:
            criterion = item.get("criterion")
            evidence = item.get("evidence")
            if not isinstance(criterion, str) or not criterion.strip():
                return {
                    "valid": False, "items": items, "missing": expected,
                    "unexpected": unexpected,
                    "reason": "each evidence object requires a textual criterion",
                }
            if not isinstance(evidence, str) or not evidence.strip():
                return {
                    "valid": False, "items": items, "missing": expected,
                    "unexpected": unexpected,
                    "reason": f"criterion '{criterion.strip()}' has empty evidence",
                }
            normalized = criterion.strip().casefold()
            if normalized in seen:
                return {
                    "valid": False, "items": items, "missing": expected,
                    "unexpected": unexpected,
                    "reason": f"criterion '{criterion.strip()}' is duplicated",
                }
            seen.add(normalized)
            match = next((value for value in expected if value.casefold() == normalized), None)
            if match is None:
                unexpected.append(criterion.strip())
            else:
                items.append({"criterion": match, "evidence": evidence.strip()})
        missing = [value for value in expected if value.casefold() not in seen]
        if missing or unexpected:
            return {
                "valid": False, "items": items, "missing": missing,
                "unexpected": unexpected,
                "reason": "evidence must match every approved criterion exactly once",
            }
        items.sort(key=lambda value: expected.index(value["criterion"]))
    else:
        return {
            "valid": False,
            "items": [],
            "missing": expected,
            "unexpected": [],
            "reason": "use either a list of evidence strings or criterion/evidence objects",
        }

    empty = [item["criterion"] for item in items if not item["evidence"].strip()]
    if empty:
        return {
            "valid": False, "items": items, "missing": empty,
            "unexpected": [], "reason": "completion evidence cannot be empty",
        }
    return {
        "valid": True,
        "items": items,
        "missing": [],
        "unexpected": [],
        "reason": "all approved completion criteria have evidence",
    }


def summarize_check_results(checks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summarize existing executor checks without running checks a second time."""
    normalized = []
    for check in checks:
        item = dict(check)
        if not item.get("available", False):
            item["status"] = "skipped"
        elif item.get("passed") is True:
            item["status"] = "passed"
        else:
            item["status"] = "failed"
        normalized.append(item)

    available = [item for item in normalized if item["status"] != "skipped"]
    failed = [item for item in normalized if item["status"] == "failed"]
    passed = [item for item in normalized if item["status"] == "passed"]
    return {
        "verified": bool(available) and not failed,
        "total": len(normalized),
        "available": len(available),
        "passed": len(passed),
        "failed": len(failed),
        "skipped": len(normalized) - len(available),
        "checks": normalized,
    }
