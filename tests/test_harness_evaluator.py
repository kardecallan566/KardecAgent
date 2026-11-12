import pytest

from kardecagent.harness.evaluator import evaluate_verification


def test_all_checks_pass():
    result = evaluate_verification({"checks": [{"name": "tests", "passed": True}, {"name": "lint", "passed": True}]})
    assert result.passed is True
    assert result.score == 1.0
    assert result.checks_passed == 2


def test_partial_failure_has_fractional_score():
    result = evaluate_verification({"checks": [{"name": "tests", "passed": True}, {"name": "lint", "passed": False}]})
    assert result.passed is False
    assert result.score == 0.5
    assert result.failures == ("lint",)


def test_empty_or_malformed_checks_are_not_success():
    assert evaluate_verification({}).passed is False
    result = evaluate_verification({"checks": [{"name": "bad"}, None]})
    assert result.passed is False
    assert result.checks_total == 0
    assert result.metadata["invalid_checks_ignored"] == 2


def test_errors_fail_even_when_checks_pass():
    result = evaluate_verification({"checks": [{"passed": True}], "errors": ["execution timeout"]})
    assert result.passed is False
    assert result.score == 1.0
    assert result.errors == ("execution timeout",)


def test_to_dict_is_json_friendly():
    result = evaluate_verification({"checks": [{"name": "unit", "passed": True}]})
    assert result.to_dict()["failures"] == []
