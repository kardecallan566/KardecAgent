from pathlib import Path

from kardecagent.agent.loop import AgentLoop
from kardecagent.agent.plan import ExecutionPlan, PlanTracker
from kardecagent.agent.recovery import RecoveryResult
from kardecagent.agent.state import TaskState, TaskStatus
from kardecagent.agent.tools_schema import ToolCall
from kardecagent.config import Settings
from kardecagent.llm.client import LLMResponse


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)

    def chat(self, messages: list[dict]) -> LLMResponse:
        try:
            return LLMResponse(next(self.responses), {})
        except StopIteration:
            return LLMResponse('{"tool":"run_checks","arguments":{"kind":"test"},"plan_step":1}', {})


def _plan(steps=("Finish validation",)) -> str:
    return '{"summary":"Do task","steps":' + str(list(steps)).replace("'", '"') + ',"validation":["Run checks"],"risks":[],"completion_criteria":["result is correct"]}'


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
    assert state.status.value == "failed"
    assert sum(event.event_type == "verification_failed" for event in state.events) == 2
    assert any(event.event_type == "model_request_failed" for event in state.events)


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
        '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":["verified"]}}',
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
        '{"tool":"request_plan_change","arguments":{"plan":{"summary":"Expanded","steps":["Create file","Add test"],"validation":["Run tests"],"risks":["Extra scope"],"completion_criteria":["result works"]}}}',
    ]
    approvals = []
    llm = FakeLLM(responses)

    def approve(plan):
        approvals.append(plan.summary)
        return True

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
        '{"tool":"request_plan_change","arguments":{"plan":{"summary":"Expanded","steps":["Original step","New step"],"validation":["Run tests"],"risks":[],"completion_criteria":["result works"]}}}',
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
        '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":["validator passed","tests passed"]}}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=4)).run(
        tmp_path, "create site", approval_callback=lambda plan: True
    )
    assert state.status.value == "completed"
    assert any(event.event_type == "completion_criteria_failed" for event in state.events)



def test_approved_plan_retry_is_bounded_and_increases_iteration_budget(tmp_path: Path, monkeypatch):
    loop = AgentLoop(FakeLLM([]), Settings(max_iterations=1))
    plan = ExecutionPlan("Approved", ["Implement"], ["tests"], [], ["done"])
    state = TaskState("task", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    limits = []

    def execute(*args, **kwargs):
        current = args[3]
        limits.append(kwargs.get("max_iterations"))
        if len(limits) == 1:
            current.transition(TaskStatus.MAX_ITERATIONS, reason="Limit reached.")
            current.record("max_iterations", "Maximum execution iterations reached.")
        else:
            assert current.status is TaskStatus.RESUMING
            current.transition(TaskStatus.RUNNING, reason="Retry started.")
            current.transition(TaskStatus.VERIFYING, reason="Verification started.")
            current.transition(TaskStatus.VERIFIED, reason="Verification passed.")
            current.transition(TaskStatus.COMPLETED, reason="Completed.")
        return current

    def recover(root, current, **kwargs):
        current.transition(TaskStatus.RESUMING, reason="Recovery completed.")
        return RecoveryResult(True, resume_step=1)

    monkeypatch.setattr(loop.executor, "execute", execute)
    monkeypatch.setattr(loop.recovery, "recover", recover)

    result = loop.execute_approved_plan(tmp_path, "task", plan, state)

    assert result.status is TaskStatus.COMPLETED
    assert limits == [None, 2]
    assert any(
        event.event_type == "retry_decision" and event.data.get("allowed") is True
        for event in result.events
    )


def test_approved_plan_does_not_retry_non_transient_run_command_failure(tmp_path: Path, monkeypatch):
    loop = AgentLoop(FakeLLM([]), Settings(max_iterations=2))
    plan = ExecutionPlan("Approved", ["Implement"], ["tests"], [], ["done"])
    state = TaskState("task", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    calls = []

    def execute(*args, **kwargs):
        current = args[3]
        calls.append(current.status)
        current.record(
            "tool_error",
            "run_command failed: command exited with status 1",
            tool="run_command",
            error="command exited with status 1",
        )
        current.transition(TaskStatus.FAILED, reason="Command failed.")
        return current

    def recover(root, current, **kwargs):
        current.transition(TaskStatus.RESUMING, reason="Workspace unchanged.")
        return RecoveryResult(True, resume_step=1)

    monkeypatch.setattr(loop.executor, "execute", execute)
    monkeypatch.setattr(loop.recovery, "recover", recover)

    result = loop.execute_approved_plan(tmp_path, "task", plan, state)

    assert calls == [TaskStatus.RUNNING]
    decision = next(event for event in result.events if event.event_type == "retry_decision")
    assert decision.data["allowed"] is False
    assert "external side effects" in decision.data["reason"]



def test_execution_context_compaction_preserves_contract_progress_and_recent_turns(tmp_path: Path):
    loop = AgentLoop(FakeLLM([]), Settings(max_iterations=40))
    plan = ExecutionPlan("Approved", ["Inspect", "Implement"], ["tests"], [], ["done"])
    tracker = PlanTracker(plan)
    state = TaskState("task", str(tmp_path))
    state.record("tool_result", "Read source file.", tool="read_file", result="important source evidence")
    messages = [
        {"role": "system", "content": "system contract"},
        {"role": "user", "content": "initial approved task context"},
    ] + [
        {"role": "assistant" if index % 2 == 0 else "user", "content": f"turn-{index}"}
        for index in range(40)
    ]

    bounded = loop.executor._bound_messages(messages, state, tracker)

    assert len(bounded) == 21
    assert bounded[0] == messages[0]
    assert bounded[1] == messages[1]
    assert "important source evidence" in bounded[2]["content"]
    assert "approved_plan_progress" in bounded[2]["content"]
    assert bounded[3:] == messages[-18:]


def test_execution_context_is_unchanged_below_compaction_threshold(tmp_path: Path):
    loop = AgentLoop(FakeLLM([]), Settings(max_iterations=10))
    plan = ExecutionPlan("Approved", ["Implement"], ["tests"], [], ["done"])
    messages = [
        {"role": "system", "content": "system contract"},
        {"role": "user", "content": "initial task"},
        {"role": "assistant", "content": "recent turn"},
    ]

    assert loop.executor._bound_messages(messages, TaskState("task", str(tmp_path)), PlanTracker(plan)) is messages


def test_identical_append_is_blocked_until_workspace_mutation(tmp_path: Path):
    loop = AgentLoop(FakeLLM([]), Settings(max_iterations=10))
    state = TaskState("append-guard", str(tmp_path))
    action = ToolCall("append_file", {"path": "notes.txt", "content": "same line"})
    signature = loop.executor._action_signature(action)
    state.record("tool_result", "append succeeded", tool="append_file", action_signature=signature)

    assert loop.executor._is_repeated_side_effect(state, action)

    state.record(
        "tool_result",
        "file rewritten",
        tool="write_file",
        action_signature="different",
    )
    assert not loop.executor._is_repeated_side_effect(state, action)


def test_identical_command_is_blocked_but_changed_command_is_allowed(tmp_path: Path):
    loop = AgentLoop(FakeLLM([]), Settings(max_iterations=10))
    state = TaskState("command-guard", str(tmp_path))
    action = ToolCall("run_command", {"command": "pytest -q"})
    signature = loop.executor._action_signature(action)
    state.record("tool_result", "tests completed", tool="run_command", action_signature=signature)

    assert loop.executor._is_repeated_side_effect(state, action)
    assert not loop.executor._is_repeated_side_effect(
        state, ToolCall("run_command", {"command": "pytest -q tests/unit"})
    )


def test_read_only_calls_are_not_subject_to_side_effect_guard(tmp_path: Path):
    loop = AgentLoop(FakeLLM([]), Settings(max_iterations=10))
    state = TaskState("read-guard", str(tmp_path))
    action = ToolCall("read_file", {"path": "README.md"})
    state.record("tool_result", "read completed", tool="read_file")

    assert not loop.executor._is_repeated_side_effect(state, action)
