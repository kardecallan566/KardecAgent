from kardecagent.project.test_results import parse_test_result


def test_pytest_success():
    result = parse_test_result("test", "12 passed in 1.2s", "", 0, False)
    assert result.passed
    assert result.summary == "12 tests passed."


def test_pytest_failure_extracts_errors():
    result = parse_test_result(
        "test",
        "2 failed, 8 passed",
        "TypeError: bad value",
        1,
        False,
    )
    assert not result.passed
    assert result.summary == "2 tests failed."
    assert any("TypeError" in item for item in result.errors)


def test_typecheck_failure_is_structured():
    result = parse_test_result("typecheck", "", "Found 3 errors.", 2, False)
    assert not result.passed
    assert result.summary == "Type checking failed with 3 errors."


def test_timeout_is_not_success():
    result = parse_test_result("build", "", "timeout", -1, True)
    assert not result.passed
    assert result.timed_out
    assert result.summary == "Command timed out."


def test_warnings_are_separated():
    result = parse_test_result("lint", "warning: unused import", "", 0, False)
    assert result.passed
    assert "warning: unused import" in result.warnings
