from types import SimpleNamespace

from kardecagent.agent.diagnostics import FailureCategory, FailureDiagnostic
from kardecagent.agent.retry_policy import RetryPolicy


def event(kind, **data):
    return SimpleNamespace(event_type=kind, data=data)


def recovery(*, recovered=True, conflicts=(), error=None, rolled_back=()):
    return SimpleNamespace(
        recovered=recovered,
        conflict_paths=conflicts,
        error=error,
        rolled_back_events=rolled_back,
    )


def diagnostic(category, *, retryable=False, tool=None):
    return FailureDiagnostic(
        category=category,
        message="failure",
        retryable=retryable,
        tool=tool,
    )


def test_allows_retry_after_safe_recovery():
    decision = RetryPolicy().decide(
        events=[],
        retry_event="resume_retry_started",
        status="failed",
        diagnostic=diagnostic(FailureCategory.MODEL_PROTOCOL),
        recovery=recovery(rolled_back=(1,)),
        base_iteration_limit=20,
    )

    assert decision.allowed
    assert decision.attempt == 1
    assert decision.iteration_limit == 20


def test_denies_retry_when_workspace_recovery_has_conflicts():
    decision = RetryPolicy().decide(
        events=[],
        retry_event="resume_retry_started",
        status="failed",
        diagnostic=diagnostic(FailureCategory.TOOL_EXECUTION),
        recovery=recovery(recovered=False, conflicts=("src/app.py",)),
        base_iteration_limit=20,
    )

    assert not decision.allowed
    assert "recovery" in decision.reason


def test_denies_retry_after_retry_budget_is_exhausted():
    decision = RetryPolicy().decide(
        events=[event("resume_retry_started")],
        retry_event="resume_retry_started",
        status="max_iterations",
        diagnostic=diagnostic(FailureCategory.ITERATION_LIMIT),
        recovery=recovery(),
        base_iteration_limit=20,
        max_retries=1,
    )

    assert not decision.allowed
    assert decision.reason == "retry budget exhausted"


def test_iteration_limit_retry_gets_a_small_bounded_budget_increase():
    decision = RetryPolicy().decide(
        events=[],
        retry_event="resume_retry_started",
        status="max_iterations",
        diagnostic=diagnostic(FailureCategory.ITERATION_LIMIT),
        recovery=recovery(),
        base_iteration_limit=20,
    )

    assert decision.allowed
    assert decision.iteration_limit == 24
    assert decision.iteration_limit <= 40


def test_iteration_limit_increase_is_bounded_for_small_budgets():
    decision = RetryPolicy().decide(
        events=[],
        retry_event="subtask_retry_started",
        subtask_id="task-1",
        status="max_iterations",
        diagnostic=diagnostic(FailureCategory.ITERATION_LIMIT),
        recovery=recovery(),
        base_iteration_limit=1,
    )

    assert decision.allowed
    assert decision.iteration_limit == 2


def test_non_transient_run_command_failure_is_not_retried():
    decision = RetryPolicy().decide(
        events=[],
        retry_event="resume_retry_started",
        status="failed",
        diagnostic=diagnostic(
            FailureCategory.TOOL_EXECUTION,
            retryable=False,
            tool="run_command",
        ),
        recovery=recovery(),
        base_iteration_limit=20,
    )

    assert not decision.allowed
    assert "external side effects" in decision.reason


def test_transient_run_command_failure_can_retry_after_recovery():
    decision = RetryPolicy().decide(
        events=[],
        retry_event="resume_retry_started",
        status="failed",
        diagnostic=diagnostic(
            FailureCategory.TOOL_EXECUTION,
            retryable=True,
            tool="run_command",
        ),
        recovery=recovery(),
        base_iteration_limit=20,
    )

    assert decision.allowed


def test_unknown_and_plan_failures_are_not_retried():
    for category in (FailureCategory.UNKNOWN, FailureCategory.PLAN, FailureCategory.RECOVERY_CONFLICT):
        decision = RetryPolicy().decide(
            events=[],
            retry_event="resume_retry_started",
            status="failed",
            diagnostic=diagnostic(category),
            recovery=recovery(),
            base_iteration_limit=20,
        )
        assert not decision.allowed


def test_subtask_retry_count_is_scoped_to_its_subtask():
    decision = RetryPolicy().decide(
        events=[
            event("subtask_retry_started", subtask_id="task-2"),
        ],
        retry_event="subtask_retry_started",
        subtask_id="task-1",
        status="max_iterations",
        diagnostic=diagnostic(FailureCategory.ITERATION_LIMIT),
        recovery=recovery(),
        base_iteration_limit=5,
        max_retries=1,
    )

    assert decision.allowed
    assert decision.attempt == 1



def test_denies_retry_when_a_command_already_returned_a_result():
    decision = RetryPolicy().decide(
        events=[],
        execution_events=[
            event("tool_result", tool="run_command", result='{"returncode":0}'),
        ],
        retry_event="resume_retry_started",
        status="max_iterations",
        diagnostic=diagnostic(FailureCategory.ITERATION_LIMIT),
        recovery=recovery(),
        base_iteration_limit=10,
    )

    assert not decision.allowed
    assert "external side effects" in decision.reason
