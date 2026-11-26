from types import SimpleNamespace

from kardecagent.agent.diagnostics import FailureCategory, diagnose_failure


def event(kind, message, **data):
    return SimpleNamespace(
        event_type=kind,
        message=message,
        data=data,
        iteration=3,
    )


def test_diagnose_preserves_original_tool_error_and_marks_transient():
    result = diagnose_failure(
        [
            event("tool_error", "run_command failed", tool="run_command",
                  error="connection timed out", plan_step=2),
            event("recovery_failed", "Rollback blocked", conflict_paths=["src/a.py"]),
        ],
        "failed",
    )

    assert result.category is FailureCategory.TOOL_EXECUTION
    assert result.error == "connection timed out"
    assert result.tool == "run_command"
    assert result.plan_step == 2
    assert result.retryable is True


def test_diagnose_marks_workspace_conflict_as_non_retryable():
    result = diagnose_failure(
        [event("recovery_failed", "Recovery blocked", conflict_paths=["src/a.py"])],
        "failed",
    )

    assert result.category is FailureCategory.RECOVERY_CONFLICT
    assert result.retryable is False
    assert result.details["conflict_paths"] == ["src/a.py"]


def test_diagnose_verification_failure_is_not_automatically_retryable():
    result = diagnose_failure(
        [event("verification_failed", "Tests failed", error="2 tests failed")],
        "failed",
    )

    assert result.category is FailureCategory.VERIFICATION
    assert result.message == "2 tests failed"
    assert result.retryable is False


def test_diagnose_iteration_limit_has_a_specific_category():
    result = diagnose_failure(
        [event("max_iterations", "Maximum execution iterations reached.")],
        "max_iterations",
    )

    assert result.category is FailureCategory.ITERATION_LIMIT
    assert result.source_event == "max_iterations"
    assert result.retryable is False


def test_diagnose_has_safe_fallback_when_no_failure_event_exists():
    result = diagnose_failure([], "failed", fallback_message="Subtask scope is missing.")

    assert result.category is FailureCategory.UNKNOWN
    assert result.message == "Subtask scope is missing."
    assert result.as_dict()["category"] == "unknown"


def test_diagnose_uses_latest_event_of_same_category():
    result = diagnose_failure(
        [
            event("tool_error", "first error", error="first"),
            event("tool_error", "second error", error="second"),
        ],
        "failed",
    )

    assert result.error == "second"



def test_diagnose_uses_only_the_final_retry_attempt():
    result = diagnose_failure(
        [
            event("tool_error", "temporary failure", error="connection timed out"),
            event("subtask_resume_retry_started", "retry started"),
            event("max_iterations", "Maximum execution iterations reached."),
        ],
        "max_iterations",
    )

    assert result.category is FailureCategory.ITERATION_LIMIT
    assert result.source_event == "max_iterations"
    assert result.retryable is False


def test_diagnose_accepts_one_shot_iterables():
    events = iter([event("tool_error", "command failed", error="permission denied")])
    result = diagnose_failure(events, "failed")

    assert result.category is FailureCategory.TOOL_EXECUTION
    assert result.error == "permission denied"



def test_diagnose_model_provider_timeout_as_transient_provider_failure():
    result = diagnose_failure(
        [event(
            "model_request_failed",
            "Model request failed.",
            error="TimeoutError: model request timed out",
            model="fixture-model",
            duration_seconds=180.0,
        )],
        "failed",
    )

    assert result.category is FailureCategory.MODEL_PROVIDER
    assert result.retryable is True
    assert result.source_event == "model_request_failed"



def test_diagnose_repeated_plan_step_stall_as_non_retryable_plan_failure():
    result = diagnose_failure(
        [event(
            "plan_execution_stalled",
            "Execution stalled on the active approved plan step.",
            reason="model repeatedly skipped active step",
            active_step=1,
        )],
        "failed",
    )

    assert result.category is FailureCategory.PLAN
    assert result.retryable is False
    assert result.source_event == "plan_execution_stalled"
