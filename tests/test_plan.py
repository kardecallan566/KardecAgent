import pytest

from kardecagent.agent.plan import ExecutionPlan, PlanError, PlanTracker, parse_plan


def test_parse_plan():
    plan = parse_plan(
        '{"summary":"Create a site","steps":["Create HTML","Create CSS"],'
        '"validation":["Validate HTML"],"risks":["No browser automation yet"]}'
    )
    assert plan.summary == "Create a site"
    assert plan.steps == ["Create HTML", "Create CSS"]
    assert plan.completion_criteria == ["result is correct"]


def test_invalid_plan():
    with pytest.raises(PlanError, match="non-empty list"):
        parse_plan('{"summary":"x","steps":[],"validation":[],"completion_criteria":["x"]}')


def test_plan_tracker_enforces_sequential_steps():
    tracker = PlanTracker(
        ExecutionPlan("x", ["one", "two"], ["validate"])
    )
    assert tracker.current_step == 1
    tracker.start(1)
    tracker.complete(1)
    assert tracker.current_step == 2
    assert tracker.status() == ["completed", "in_progress"]
    with pytest.raises(PlanError, match="active plan step"):
        tracker.start(1)
    tracker.start(2)
    tracker.complete(2)
    assert tracker.completed
    assert tracker.as_dict()["current_step"] is None


def test_completion_criteria_are_required():
    with pytest.raises(PlanError, match="completion_criteria"):
        parse_plan('{"summary":"x","steps":["one"],"validation":[]}')


def test_plan_tracker_can_resume_from_step():
    plan = ExecutionPlan("x", ["one", "two", "three"], ["validate"])
    tracker = PlanTracker.resume_from(plan, 3)
    assert tracker.current_step == 3
    assert tracker.completed_steps == [1, 2]
    assert tracker.started_steps == [1, 2]
    assert tracker.status() == ["completed", "completed", "in_progress"]


def test_plan_tracker_rejects_invalid_resume_step():
    plan = ExecutionPlan("x", ["one"], ["validate"])
    with pytest.raises(PlanError, match="resume step"):
        PlanTracker.resume_from(plan, 3)
