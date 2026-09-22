import pytest

from kardecagent.agent.plan import ExecutionPlan, PlanError, PlanTracker, parse_plan


def test_parse_plan():
    plan = parse_plan(
        '{"summary":"Create a site","steps":["Create HTML","Create CSS"],'
        '"validation":["Validate HTML"],"risks":["No browser automation yet"],'
        '"completion_criteria":["result is correct"]}'
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


def test_parse_plan_recovers_from_missing_commas():
    malformed = """```json
{
  "summary": "Build game page",
  "steps": [
    "1. Define layout"
    "2. Choose CSS framework"
    "3. Create HTML"
  ],
  "validation": [
    "Visual check"
    "Browser test"
  ],
  "risks": [
    "CSS conflict"
  ],
  "completion_criteria": [
    "HTML exists"
    "Page looks good"
  ]
}
```"""
    plan = parse_plan(malformed)
    assert plan.summary == "Build game page"
    assert len(plan.steps) == 3
    assert len(plan.validation) == 2
    assert len(plan.completion_criteria) == 2


def test_parse_plan_handles_dict_elements():
    raw = """{
      "summary": "Create game page",
      "steps": [
        {"step": "Step 1", "description": "Set up files"},
        {"step": "Step 2", "description": "Add CSS styles"}
      ],
      "validation": [
        {"method": "Browser check", "description": "Open in Chrome"}
      ],
      "risks": [
        {"risk": "Compatibility", "mitigation": "Use standard CSS"}
      ],
      "completion_criteria": [
        {"criteria": "HTML page renders correctly", "method": "Visual inspection"}
      ]
    }"""
    plan = parse_plan(raw)
    assert plan.summary == "Create game page"
    assert len(plan.steps) == 2
    assert "Step 1: Set up files" in plan.steps[0]
    assert "Step 2: Add CSS styles" in plan.steps[1]
    assert "Browser check: Open in Chrome" in plan.validation[0]
    assert "Compatibility: Use standard CSS" in plan.risks[0]
    assert "HTML page renders correctly: Visual inspection" in plan.completion_criteria[0]

