from pathlib import Path

from kardecagent.agent.loop import AgentLoop
from kardecagent.agent.plan import ExecutionPlan
from kardecagent.agent.state import TaskState
from kardecagent.config import Settings
from kardecagent.llm.client import LLMResponse


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)

    def chat(self, messages: list[dict]) -> LLMResponse:
        return LLMResponse(next(self.responses), {})


def make_state(tmp_path: Path) -> TaskState:
    return TaskState("subtask", str(tmp_path))


def test_execute_approved_plan_does_not_request_approval(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html lang='pt-BR'><head>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Site</title></head><body>OK</body></html>",
        encoding="utf-8",
    )
    llm = FakeLLM([
        '{"tool":"complete_step","arguments":{"step":1,"evidence":"ready"},"plan_step":1}',
        '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":["verified"]}}',
    ])
    loop = AgentLoop(llm, Settings(max_iterations=2))
    plan = ExecutionPlan("Approved", ["Validate"], ["validator"], [], ["verified"])
    state = make_state(tmp_path)

    result = loop.execute_approved_plan(tmp_path, "parent", plan, state)

    assert result.status.value == "completed"
    assert not any(e.event_type == "plan_approval" for e in result.events)


def test_subtask_scope_blocks_write_outside_scope(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    llm = FakeLLM([
        '{"tool":"write_file","arguments":{"path":"outside.txt","content":"blocked"},"plan_step":1}',
    ])
    loop = AgentLoop(llm, Settings(max_iterations=1))
    plan = ExecutionPlan("Scoped", ["Implement"], ["checks"], [], ["done"])
    state = make_state(tmp_path)

    result = loop.execute_approved_plan(
        tmp_path, "parent", plan, state, allowed_scope=("src",), allow_plan_changes=False
    )

    assert result.status.value == "max_iterations"
    assert not (tmp_path / "outside.txt").exists()
    assert any(e.event_type == "tool_error" and "scope violation" in e.message for e in result.events)
