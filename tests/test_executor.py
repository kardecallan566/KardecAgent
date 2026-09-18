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


def test_run_command_detects_new_file_outside_scope(tmp_path: Path):
    import json
    from kardecagent.agent.tools_schema import parse_tool_call

    (tmp_path / "inside").mkdir()
    llm = FakeLLM([])
    loop = AgentLoop(llm, Settings())
    plan = ExecutionPlan("Scoped", ["Command"], ["checks"], [], ["done"])
    state = make_state(tmp_path)
    action = parse_tool_call(
        '{"tool":"run_command","arguments":{"command":"python -c \\"open(\\\'outside.txt\\\', \\'w\\\').write(\\\'x\\\')\\"},"plan_step":1}'
    )
    try:
        loop.executor._execute_tool(tmp_path, action, ("inside",))
    except ValueError as exc:
        assert "scope violation" in str(exc)
    else:
        raise AssertionError("expected scope violation")
    assert (tmp_path / "outside.txt").read_text(encoding="utf-8") == "already dirty"
    assert (tmp_path / "outside.txt").exists()


def test_run_command_detects_change_to_preexisting_dirty_file(tmp_path: Path):
    import subprocess
    (tmp_path / "inside").mkdir()
    (tmp_path / "outside.txt").write_text("before", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-m", "fixture"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "outside.txt").write_text("already dirty", encoding="utf-8")
    loop = AgentLoop(FakeLLM([]), Settings())
    action = __import__("kardecagent.agent.tools_schema", fromlist=["parse_tool_call"]).parse_tool_call(
        '{"tool":"run_command","arguments":{"command":"python -c \\"open(\\\'outside.txt\\\', \\'w\\\').write(\\\'changed\\\')\\"},"plan_step":1}'
    )
    try:
        loop.executor._execute_tool(tmp_path, action, ("inside",))
    except ValueError as exc:
        assert "scope violation" in str(exc)
    else:
        raise AssertionError("expected scope violation")


def test_mutation_records_integrity_fingerprints(tmp_path: Path):
    llm = FakeLLM([])
    loop = AgentLoop(llm, Settings())
    plan = ExecutionPlan("Audit", ["Implement"], ["checks"], [], ["done"])
    state = make_state(tmp_path)
    from kardecagent.agent.tools_schema import parse_tool_call
    action = parse_tool_call(
        '{"tool":"write_file","arguments":{"path":"inside.txt","content":"after"},"plan_step":1}'
    )

    loop.executor._execute_tool(tmp_path, action, ("inside.txt",), state)

    changes = [e for e in state.events if e.event_type == "integrity_change"]
    assert len(changes) == 1
    record = changes[0].data["changes"][0]
    assert record["path"] == "inside.txt"
    assert record["sha256_before"] is None
    assert len(record["sha256_after"]) == 64
    assert record["tool"] == "write_file"
    assert record["plan_step"] == 1
    assert record["subtask"] is True
    assert changes[0].timestamp
