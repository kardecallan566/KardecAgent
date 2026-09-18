from pathlib import Path

from kardecagent.agent.loop import AgentLoop
from kardecagent.config import Settings
from kardecagent.llm.client import LLMResponse


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)

    def chat(self, messages: list[dict]) -> LLMResponse:
        return LLMResponse(next(self.responses), {})


def _plan(steps=("Finish validation",)) -> str:
    return '{"summary":"Do task","steps":' + str(list(steps)).replace("'", '"') + ',"validation":["Run checks"],"risks":[]}'


def test_finish_requires_verification(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    (tmp_path / "test_example.py").write_text(
        "def test_example():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    llm = FakeLLM([
        _plan(),
        '{"tool":"complete_step","arguments":{"step":1,"evidence":"tests are present"},"plan_step":1}',
        '{"tool":"finish","arguments":{"reason":"implemented","criteria_evidence":["pytest passed"]}}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=3)).run(
        tmp_path, "verify task", approval_callback=lambda plan: True
    )
    assert state.status.value == "completed"
    assert any(event.event_type == "verification" for event in state.events)
    assert any(event.event_type == "plan_step_completed" for event in state.events)


def test_failed_verification_returns_to_model(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    (tmp_path / "test_example.py").write_text(
        "def test_example():\n    assert False\n",
        encoding="utf-8",
    )
    llm = FakeLLM([
        _plan(),
        '{"tool":"complete_step","arguments":{"step":1,"evidence":"attempted"},"plan_step":1}',
        '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":["verified"]}}',
        '{"tool":"finish","arguments":{"reason":"done again","criteria_evidence":["verified"]}}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=4)).run(
        tmp_path, "fix task", approval_callback=lambda plan: True
    )
    assert state.status.value == "max_iterations"
    assert any(event.event_type == "verification_failed" for event in state.events)


def test_static_html_finish_is_verified(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html lang='pt-BR'><head>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Site</title></head><body><main>OK</main></body></html>",
        encoding="utf-8",
    )
    llm = FakeLLM([
        _plan(),
        '{"tool":"complete_step","arguments":{"step":1,"evidence":"HTML created"},"plan_step":1}',
        '{"tool":"finish","arguments":{"reason":"site implemented","criteria_evidence":["HTML validator passed"]}}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=3)).run(
        tmp_path, "create site", approval_callback=lambda plan: True
    )
    assert state.status.value == "completed"


def test_plan_must_be_approved_before_execution(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    llm = FakeLLM([_plan()])
    state = AgentLoop(llm, Settings(max_iterations=1)).run(
        tmp_path, "do task", approval_callback=lambda plan: False
    )
    assert state.status.value == "failed"
    assert any(event.event_type == "plan_created" for event in state.events)
    assert any(
        event.event_type == "plan_approval" and event.data.get("approved") is False
        for event in state.events
    )
    assert not any(event.event_type == "execution_started" for event in state.events)
    assert not any(event.event_type == "checkpoint_created" for event in state.events)


def test_approved_plan_starts_execution_and_checkpoint_after_approval(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    llm = FakeLLM([
        _plan(),
        '{"tool":"finish","arguments":{"reason":"done"}}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=1)).run(
        tmp_path, "do task", approval_callback=lambda plan: True
    )
    assert any(event.event_type == "execution_started" for event in state.events)
    assert not any(event.event_type == "checkpoint_created" for event in state.events)


def test_plan_scope_violation_is_rejected(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    llm = FakeLLM([
        _plan(),
        '{"tool":"write_file","arguments":{"path":"x.txt","content":"x"},"plan_step":2}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=2)).run(
        tmp_path, "do task", approval_callback=lambda plan: True
    )
    assert any(event.event_type == "plan_scope_violation" for event in state.events)
    assert not (tmp_path / "x.txt").exists()


def test_plan_change_requires_second_approval(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    responses = [
        _plan(("Create file",)),
        '{"tool":"request_plan_change","arguments":{"plan":{"summary":"Expanded","steps":["Create file","Add test"],"validation":["Run tests"],"risks":["Extra scope"]}}}',
    ]
    approvals = []
    llm = FakeLLM(responses)

    def approve(plan):
        approvals.append(plan.summary)
        return len(approvals) == 1

    state = AgentLoop(llm, Settings(max_iterations=2)).run(
        tmp_path, "do task", approval_callback=approve
    )
    assert approvals == ["Do task", "Expanded"]
    assert any(event.event_type == "plan_change_proposed" for event in state.events)
    assert any(event.event_type == "plan_change_approval" for event in state.events)
    assert any(event.event_type == "plan_changed" for event in state.events)


def test_rejected_plan_change_keeps_original_plan(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    llm = FakeLLM([
        _plan(("Original step",)),
        '{"tool":"request_plan_change","arguments":{"plan":{"summary":"Expanded","steps":["Original step","New step"],"validation":["Run tests"],"risks":[]}}}',
    ])
    approvals = []

    def approve(plan):
        approvals.append(plan.summary)
        return len(approvals) == 1

    state = AgentLoop(llm, Settings(max_iterations=2)).run(
        tmp_path, "do task", approval_callback=approve
    )
    assert approvals == ["Do task", "Expanded"]
    assert any(
        event.event_type == "plan_change_approval" and event.data.get("approved") is False
        for event in state.events
    )


def test_finish_requires_evidence_for_every_completion_criterion(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html lang='pt-BR'><head><meta name='viewport' content='width=device-width, initial-scale=1'><title>Site</title></head><body>OK</body></html>",
        encoding="utf-8",
    )
    llm = FakeLLM([
        '{"summary":"Do task","steps":["Finish validation"],"validation":["Run checks"],"risks":[],"completion_criteria":["result is verified","tests pass"]}',
        '{"tool":"complete_step","arguments":{"step":1,"evidence":"implemented"},"plan_step":1}',
        '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":["only one"]}}',
        '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":["validator passed"]}}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=4)).run(
        tmp_path, "create site", approval_callback=lambda plan: True
    )
    assert state.status.value == "completed"
    assert any(event.event_type == "completion_criteria_failed" for event in state.events)
