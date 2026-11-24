import pytest

from kardecagent.agent.tools_schema import ToolCallError, parse_tool_call
from kardecagent.agent.verification import (
    evaluate_completion_evidence,
    summarize_check_results,
)


def test_legacy_evidence_strings_are_matched_one_to_one_in_plan_order():
    result = evaluate_completion_evidence(
        ["Tests pass", "Editor opens"],
        ["pytest -q returned zero", "Opened the editor and loaded the file"],
    )

    assert result["valid"]
    assert result["items"] == [
        {"criterion": "Tests pass", "evidence": "pytest -q returned zero"},
        {"criterion": "Editor opens", "evidence": "Opened the editor and loaded the file"},
    ]


def test_explicit_evidence_can_arrive_in_any_order_and_is_normalized_to_plan_order():
    result = evaluate_completion_evidence(
        ["Tests pass", "Editor opens"],
        [
            {"criterion": "Editor opens", "evidence": "Loaded sample.py in the editor"},
            {"criterion": "Tests pass", "evidence": "pytest -q: 18 passed"},
        ],
    )

    assert result["valid"]
    assert [item["criterion"] for item in result["items"]] == [
        "Tests pass", "Editor opens"
    ]


def test_evidence_must_cover_every_criterion():
    result = evaluate_completion_evidence(
        ["Tests pass", "Editor opens"],
        [{"criterion": "Tests pass", "evidence": "pytest passed"}],
    )

    assert not result["valid"]
    assert result["missing"] == ["Editor opens"]


def test_evidence_rejects_duplicate_and_unapproved_criteria():
    duplicate = evaluate_completion_evidence(
        ["Tests pass", "Editor opens"],
        [
            {"criterion": "Tests pass", "evidence": "pytest passed"},
            {"criterion": "tests PASS", "evidence": "ran pytest again"},
        ],
    )
    unexpected = evaluate_completion_evidence(
        ["Tests pass"],
        [{"criterion": "Build passes", "evidence": "build passed"}],
    )

    assert not duplicate["valid"]
    assert "duplicated" in duplicate["reason"]
    assert not unexpected["valid"]
    assert unexpected["unexpected"] == ["Build passes"]


def test_legacy_evidence_does_not_accept_extra_or_missing_items():
    too_few = evaluate_completion_evidence(["A", "B"], ["done"])
    too_many = evaluate_completion_evidence(["A"], ["done", "also done"])

    assert not too_few["valid"]
    assert not too_many["valid"]


def test_check_summary_keeps_skipped_checks_distinct_from_failures():
    summary = summarize_check_results([
        {"kind": "test", "available": True, "passed": True},
        {"kind": "lint", "available": False},
    ])

    assert summary["verified"]
    assert summary["total"] == 2
    assert summary["available"] == 1
    assert summary["passed"] == 1
    assert summary["failed"] == 0
    assert summary["skipped"] == 1
    assert summary["checks"][1]["status"] == "skipped"


def test_check_summary_fails_closed_when_no_check_is_available_or_any_fails():
    unavailable = summarize_check_results([
        {"kind": "test", "available": False},
        {"kind": "lint", "available": False},
    ])
    failed = summarize_check_results([
        {"kind": "test", "available": True, "passed": True},
        {"kind": "lint", "available": True, "passed": False},
    ])

    assert not unavailable["verified"]
    assert not failed["verified"]
    assert failed["failed"] == 1


def test_finish_tool_accepts_explicit_criterion_evidence_objects():
    action = parse_tool_call(
        '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":'
        '[{"criterion":"Tests pass","evidence":"pytest: 10 passed"}]}}'
    )

    assert action.arguments["criteria_evidence"][0]["criterion"] == "Tests pass"


def test_finish_tool_rejects_malformed_criterion_evidence_objects():
    with pytest.raises(ToolCallError):
        parse_tool_call(
            '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":'
            '[{"criterion":"Tests pass","evidence":""}]}}'
        )
