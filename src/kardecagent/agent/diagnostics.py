from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable


class FailureCategory(str, Enum):
    MODEL_PROTOCOL = "model_protocol"
    TOOL_EXECUTION = "tool_execution"
    VERIFICATION = "verification"
    RECOVERY_CONFLICT = "recovery_conflict"
    ITERATION_LIMIT = "iteration_limit"
    PLAN = "plan"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FailureDiagnostic:
    """Actionable, serializable explanation for an unsuccessful agent execution."""

    category: FailureCategory
    message: str
    retryable: bool = False
    source_event: str | None = None
    error: str | None = None
    tool: str | None = None
    iteration: int | None = None
    plan_step: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["category"] = self.category.value
        return payload


_TRANSIENT_MARKERS = (
    "timed out",
    "timeout",
    "temporarily unavailable",
    "temporary failure",
    "connection reset",
    "connection refused",
    "connection error",
    "rate limit",
    "too many requests",
    "http 429",
    "http 502",
    "http 503",
    "http 504",
)


def _is_transient(error: str | None) -> bool:
    normalized = (error or "").casefold()
    return any(marker in normalized for marker in _TRANSIENT_MARKERS)


def diagnose_failure(
    events: Iterable[Any],
    status: str,
    *,
    fallback_message: str | None = None,
) -> FailureDiagnostic:
    """Select the most useful failure event without hiding its original error.

    The retryable flag is advisory only; this function never retries or changes
    task state. Callers remain responsible for approval and side-effect safety.
    """
    event_list = list(events)
    retry_markers = [
        index for index, event in enumerate(event_list)
        if getattr(event, "event_type", None) in {
            "subtask_resume_retry_started", "resume_retry_started"
        }
    ]
    if retry_markers:
        event_list = event_list[retry_markers[-1] + 1:]
    priorities = (
        # Prefer the underlying execution failure over a later rollback failure.
        ("model_protocol_stalled", FailureCategory.MODEL_PROTOCOL),
        ("tool_error", FailureCategory.TOOL_EXECUTION),
        ("model_error", FailureCategory.MODEL_PROTOCOL),
        ("verification_failed", FailureCategory.VERIFICATION),
        ("recovery_failed", FailureCategory.RECOVERY_CONFLICT),
        ("max_iterations", FailureCategory.ITERATION_LIMIT),
        ("plan_error", FailureCategory.PLAN),
    )
    selected = None
    category = FailureCategory.UNKNOWN
    for event_type, candidate_category in priorities:
        matches = [event for event in event_list if getattr(event, "event_type", None) == event_type]
        if matches:
            selected = matches[-1]
            category = candidate_category
            break

    if selected is None:
        message = fallback_message or f"Execution ended with status: {status}."
        return FailureDiagnostic(category, message, source_event=None)

    data = getattr(selected, "data", {}) or {}
    error = data.get("error")
    message = str(error or getattr(selected, "message", "") or fallback_message or
                  f"Execution ended with status: {status}.")
    details: dict[str, Any] = {}
    for key in ("repeated_count", "conflict_paths", "recovery_id", "limit", "resume_step"):
        if key in data:
            details[key] = data[key]
    related_recovery = next(
        (event for event in reversed(list(events))
         if getattr(event, "event_type", None) == "recovery_failed"),
        None,
    )
    if related_recovery is not None and related_recovery is not selected:
        recovery_data = getattr(related_recovery, "data", {}) or {}
        if recovery_data.get("conflict_paths"):
            details["recovery_conflict_paths"] = recovery_data["conflict_paths"]

    return FailureDiagnostic(
        category=category,
        message=message,
        retryable=_is_transient(str(error or message)),
        source_event=getattr(selected, "event_type", None),
        error=str(error) if error is not None else None,
        tool=str(data["tool"]) if data.get("tool") is not None else None,
        iteration=getattr(selected, "iteration", None),
        plan_step=data.get("plan_step") if isinstance(data.get("plan_step"), int) else None,
        details=details,
    )
