from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .diagnostics import FailureCategory, FailureDiagnostic


@dataclass(frozen=True)
class RetryDecision:
    allowed: bool
    reason: str
    attempt: int
    iteration_limit: int


class RetryPolicy:
    """Bounded retry gate for approved-plan recovery.

    A retry is considered only after RecoveryManager has confirmed that the
    workspace is consistent. This policy never performs recovery or executes
    tools; it only decides whether the caller may resume the approved plan.
    """

    _RETRYABLE_CATEGORIES = frozenset({
        FailureCategory.MODEL_PROTOCOL,
        FailureCategory.TOOL_EXECUTION,
        FailureCategory.VERIFICATION,
        FailureCategory.ITERATION_LIMIT,
    })

    @staticmethod
    def _count_retries(
        events: Iterable[object],
        event_type: str,
        subtask_id: str | None,
    ) -> int:
        count = 0
        for event in events:
            if getattr(event, "event_type", None) != event_type:
                continue
            data = getattr(event, "data", {}) or {}
            if subtask_id is not None and data.get("subtask_id") != subtask_id:
                continue
            count += 1
        return count

    @staticmethod
    def _expanded_limit(base_limit: int) -> int:
        base = max(1, int(base_limit))
        increment = max(1, min(4, base // 4))
        return min(base * 2, base + increment)

    def decide(
        self,
        *,
        events: Iterable[object],
        retry_event: str,
        status: str,
        diagnostic: FailureDiagnostic,
        recovery,
        base_iteration_limit: int,
        max_retries: int = 1,
        subtask_id: str | None = None,
    ) -> RetryDecision:
        """Return a conservative decision and the bounded next iteration limit."""
        previous_retries = self._count_retries(events, retry_event, subtask_id)
        attempt = previous_retries + 1
        base_limit = max(1, int(base_iteration_limit))

        def deny(reason: str) -> RetryDecision:
            return RetryDecision(False, reason, attempt, base_limit)

        if status not in {"failed", "max_iterations"}:
            return deny(f"status '{status}' is not eligible for retry")
        if max_retries < 1 or previous_retries >= max_retries:
            return deny("retry budget exhausted")
        if not getattr(recovery, "recovered", False):
            return deny("workspace recovery did not complete safely")
        if getattr(recovery, "conflict_paths", ()):
            return deny("workspace conflicts prevent a safe retry")
        if getattr(recovery, "error", None):
            return deny("recovery reported an error")
        if diagnostic.category not in self._RETRYABLE_CATEGORIES:
            return deny(f"failure category '{diagnostic.category.value}' is not retryable")
        if diagnostic.tool == "run_command" and not diagnostic.retryable:
            return deny("non-transient command failures are not retried because commands may have external side effects")

        iteration_limit = (
            self._expanded_limit(base_limit)
            if diagnostic.category is FailureCategory.ITERATION_LIMIT
            else base_limit
        )
        return RetryDecision(
            True,
            "approved plan can resume from the recovered workspace",
            attempt,
            iteration_limit,
        )
