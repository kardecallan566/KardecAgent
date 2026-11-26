from pathlib import Path

from kardecagent.agent.loop import AgentLoop
from kardecagent.agent.plan import ExecutionPlan
from kardecagent.agent.state import TaskState, TaskStatus
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

    assert result.status.value == "failed"
    assert any(e.event_type == "retry_decision" and e.data.get("allowed") is False for e in result.events)
    assert not (tmp_path / "outside.txt").exists()
    assert any(e.event_type == "tool_error" and "scope violation" in e.message for e in result.events)


def test_dependency_install_commands_are_blocked_by_default(tmp_path: Path):
    import json
    from kardecagent.agent.tools_schema import parse_tool_call

    loop = AgentLoop(FakeLLM([]), Settings())
    for command in ("pip install requests", "npm install", "uv add httpx", "cargo add serde"):
        action = parse_tool_call(json.dumps({
            "tool": "run_command", "arguments": {"command": command}, "plan_step": 1
        }))
        try:
            loop.executor._execute_tool(tmp_path, action, None)
        except PermissionError as exc:
            assert "explicit user authorization" in str(exc)
        else:
            raise AssertionError(f"dependency installation was not blocked: {command}")


def test_non_install_commands_are_not_blocked(tmp_path: Path):
    assert not AgentLoop(FakeLLM([]), Settings()).executor._is_dependency_install_command("pytest -q")
    assert not AgentLoop(FakeLLM([]), Settings()).executor._is_dependency_install_command("npm test")


def test_run_command_detects_new_file_outside_scope(tmp_path: Path):
    import json
    from kardecagent.agent.tools_schema import parse_tool_call

    (tmp_path / "inside").mkdir()
    llm = FakeLLM([])
    loop = AgentLoop(llm, Settings())
    plan = ExecutionPlan("Scoped", ["Command"], ["checks"], [], ["done"])
    state = make_state(tmp_path)
    action = parse_tool_call(
        json.dumps({
            "tool": "run_command",
            "arguments": {"command": "python -c \"open('outside.txt', 'w').write('x')\""},
            "plan_step": 1,
        })
    )
    try:
        loop.executor._execute_tool(tmp_path, action, ("inside",))
    except ValueError as exc:
        assert "scope violation" in str(exc)
    else:
        raise AssertionError("expected scope violation")
    assert not (tmp_path / "outside.txt").exists()


def test_run_command_detects_change_to_preexisting_dirty_file(tmp_path: Path):
    import json
    import subprocess
    from kardecagent.agent.tools_schema import parse_tool_call
    (tmp_path / "inside").mkdir()
    (tmp_path / "outside.txt").write_text("before", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-m", "fixture"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "outside.txt").write_text("already dirty", encoding="utf-8")
    loop = AgentLoop(FakeLLM([]), Settings())
    action = parse_tool_call(
        json.dumps({
            "tool": "run_command",
            "arguments": {"command": "python -c \"open('outside.txt', 'w').write('changed')\""},
            "plan_step": 1,
        })
    )
    try:
        loop.executor._execute_tool(tmp_path, action, ("inside",))
    except ValueError as exc:
        assert "scope violation" in str(exc)
    else:
        raise AssertionError("expected scope violation")
    assert (tmp_path / "outside.txt").read_text(encoding="utf-8") == "already dirty"


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
    assert record["subtask_id"] is None
    assert changes[0].timestamp


def test_audited_operation_can_be_rolled_back(tmp_path: Path):
    loop = AgentLoop(FakeLLM([]), Settings())
    plan = ExecutionPlan("Audit", ["Implement"], ["checks"], [], ["done"])
    state = make_state(tmp_path)
    from kardecagent.agent.tools_schema import parse_tool_call
    action = parse_tool_call(
        '{"tool":"write_file","arguments":{"path":"inside.txt","content":"after"},"plan_step":1}'
    )
    loop.executor._execute_tool(tmp_path, action, ("inside.txt",), state)
    loop.executor.rollback_operation(tmp_path, state, 0)
    assert not (tmp_path / "inside.txt").exists()
    assert any(e.event_type == "integrity_rollback" for e in state.events)


def test_audited_rollback_refuses_concurrent_modification(tmp_path: Path):
    loop = AgentLoop(FakeLLM([]), Settings())
    state = make_state(tmp_path)
    from kardecagent.agent.tools_schema import parse_tool_call
    action = parse_tool_call(
        '{"tool":"write_file","arguments":{"path":"inside.txt","content":"after"},"plan_step":1}'
    )
    loop.executor._execute_tool(tmp_path, action, ("inside.txt",), state)
    (tmp_path / "inside.txt").write_text("changed-after-audit", encoding="utf-8")
    try:
        loop.executor.rollback_operation(tmp_path, state, 0)
    except RuntimeError as exc:
        assert "rollback conflict" in str(exc)
    else:
        raise AssertionError("expected rollback conflict")


def test_finish_rejects_missing_criterion_evidence_before_accepting_complete_evidence(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html lang='pt-BR'><head>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Site</title></head><body>OK</body></html>",
        encoding="utf-8",
    )
    llm = FakeLLM([
        '{"tool":"complete_step","arguments":{"step":1,"evidence":"implemented"},"plan_step":1}',
        '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":'
        '[{"criterion":"Page validates","evidence":"validator passed"}]}}',
        '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":'
        '[{"criterion":"Page validates","evidence":"validator passed"},'
        '{"criterion":"Mobile viewport exists","evidence":"viewport meta tag is present"}]}}',
    ])
    loop = AgentLoop(llm, Settings(max_iterations=3))
    plan = ExecutionPlan(
        "Approved",
        ["Validate"],
        ["validator"],
        [],
        ["Page validates", "Mobile viewport exists"],
    )
    result = loop.execute_approved_plan(tmp_path, "parent", plan, make_state(tmp_path))

    assert result.status.value == "completed"
    rejected = [event for event in result.events if event.event_type == "completion_criteria_failed"]
    assert len(rejected) == 1
    assert rejected[0].data["missing"] == ["Mobile viewport exists"]
    completed = next(event for event in result.events if event.event_type == "completed")
    assert len(completed.data["criteria_evidence"]) == 2


def test_verify_includes_structured_check_summary_without_rerunning_checks(tmp_path: Path, monkeypatch):
    loop = AgentLoop(FakeLLM([]), Settings())
    results = iter([
        {"kind": "test", "available": True, "passed": True, "summary": "3 passed"},
        {"kind": "typecheck", "available": False},
        {"kind": "lint", "available": True, "passed": False, "summary": "lint errors"},
        {"kind": "build", "available": False},
        {"kind": "validate", "available": False},
    ])
    monkeypatch.setattr(loop.executor, "_run_check", lambda root, kind: next(results))

    report = loop.executor.verify(tmp_path)

    assert not report["verified"]
    assert report["summary"] == {
        "verified": False,
        "total": 5,
        "available": 2,
        "passed": 1,
        "failed": 1,
        "skipped": 3,
    }
    assert [check["status"] for check in report["checks"]] == [
        "passed", "skipped", "failed", "skipped", "skipped"
    ]


def test_agent_file_tools_create_directory_append_and_replace(tmp_path: Path):
    import json
    from kardecagent.agent.tools_schema import parse_tool_call
    loop = AgentLoop(FakeLLM([]), Settings())
    state = make_state(tmp_path)
    actions = [
        parse_tool_call(json.dumps({"tool":"create_directory","arguments":{"path":"src/new"},"plan_step":1})),
        parse_tool_call(json.dumps({"tool":"append_file","arguments":{"path":"src/new/loucura.py","content":"print(1,2)\n"},"plan_step":1})),
        parse_tool_call(json.dumps({"tool":"replace_in_file","arguments":{"path":"src/new/loucura.py","find":"print(1,2)","replace":"print(3,4)"},"plan_step":1})),
    ]
    for action in actions:
        loop.executor._execute_tool(tmp_path, action, None, state)
    assert (tmp_path / "src/new/loucura.py").read_text(encoding="utf-8") == "print(3,4)\n"


def test_agent_file_tools_respect_subtask_scope(tmp_path: Path):
    import json
    from kardecagent.agent.tools_schema import parse_tool_call
    loop = AgentLoop(FakeLLM([]), Settings())
    action = parse_tool_call(json.dumps({"tool":"create_directory","arguments":{"path":"outside"},"plan_step":1}))
    try:
        loop.executor._execute_tool(tmp_path, action, ("src",))
    except ValueError as exc:
        assert "scope violation" in str(exc)
    else:
        raise AssertionError("expected scope violation")
    assert not (tmp_path / "outside").exists()


def test_agent_appends_requested_line_to_existing_file(tmp_path: Path):
    import json
    from kardecagent.agent.tools_schema import parse_tool_call

    target = tmp_path / "loucura.py"
    target.write_text("print('inicio')\n", encoding="utf-8")
    loop = AgentLoop(FakeLLM([]), Settings())
    state = make_state(tmp_path)
    action = parse_tool_call(json.dumps({
        "tool": "append_file",
        "arguments": {"path": "loucura.py", "content": "print(1,2)\n"},
        "plan_step": 1,
    }))

    result = loop.executor._execute_tool(tmp_path, action, None, state)

    assert json.loads(result)["ok"] is True
    assert target.read_text(encoding="utf-8") == "print('inicio')\nprint(1,2)\n"


def test_git_shell_mutations_are_detected_and_redacted(tmp_path: Path):
    executor = AgentLoop(FakeLLM([]), Settings()).executor
    assert executor._contains_git_mutation_command("git push origin main")
    assert executor._contains_git_mutation_command("git -C repo pull --ff-only origin main")
    assert executor._contains_git_mutation_command("npm test && gh pr merge 12 --squash")
    assert not executor._contains_git_mutation_command("git status && git log -5")
    redacted = executor._redact_git_command("git push https://token123@github.com/org/repo")
    assert "token123" not in redacted
    assert "[REDACTED]" in redacted


def test_git_mutation_requires_separate_explicit_approval(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)

    class StaticLLM:
        def chat(self, messages):
            return LLMResponse('{"tool":"git_create_branch","arguments":{"branch":"feature/approval"},"plan_step":1}', {})

    loop = AgentLoop(StaticLLM(), Settings(max_iterations=2))
    plan = ExecutionPlan("Git change", ["Create a branch"], ["verify branch"], [], ["branch created"])
    result = loop.execute_approved_plan(tmp_path, "create branch", plan, make_state(tmp_path))

    assert result.status.value == "max_iterations"
    assert any(event.event_type == "git_approval_denied" for event in result.events)
    assert not subprocess.run(["git", "branch", "--list", "feature/approval"], cwd=tmp_path,
                              check=True, capture_output=True, text=True).stdout.strip()


def test_git_mutation_executes_only_after_high_risk_approval(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)

    class StaticLLM:
        def chat(self, messages):
            return LLMResponse('{"tool":"git_create_branch","arguments":{"branch":"feature/approved"},"plan_step":1}', {})

    loop = AgentLoop(StaticLLM(), Settings(max_iterations=2))
    plan = ExecutionPlan("Git change", ["Create a branch"], ["verify branch"], [], ["branch created"])
    approvals = []
    result = loop.execute_approved_plan(
        tmp_path, "create branch", plan, make_state(tmp_path),
        high_risk_approval_callback=lambda proposed: approvals.append(proposed) or True,
    )

    assert result.status.value == "failed"
    assert any(event.event_type == "plan_execution_stalled" for event in result.events)
    assert approvals and approvals[0].security_level == "high_risk"
    assert any(event.event_type == "git_approval_granted" for event in result.events)
    assert "feature/approved" in subprocess.run(
        ["git", "branch", "--list"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout


def test_shell_git_mutations_cannot_bypass_approval_or_destructive_guards():
    executor = AgentLoop(FakeLLM([]), Settings()).executor
    assert executor._contains_git_mutation_command("python -c \"import subprocess; subprocess.run(['git','push'])\"")
    assert executor._contains_git_mutation_command("git add src/app.py")
    assert executor._contains_git_mutation_command("gh pr create --title feature")
    assert executor._is_forbidden_git_command("git push --force-with-lease origin main")
    assert executor._is_forbidden_git_command("git reset --hard HEAD~1")
    assert executor._is_forbidden_git_command("git checkout -- .")
    assert not executor._is_forbidden_git_command("git pull --ff-only origin main")



def test_model_request_failure_is_journaled_and_task_is_terminal(tmp_path: Path):
    class FailingLLM:
        model = "fixture-model"

        def chat(self, messages):
            raise TimeoutError("model request timed out")

    loop = AgentLoop(FailingLLM(), Settings(max_iterations=2, llm_timeout_seconds=17))
    plan = ExecutionPlan("Approved", ["Inspect"], ["checks"], [], ["done"])
    state = make_state(tmp_path)
    state.transition(TaskStatus.RUNNING)
    persisted = []

    result = loop.executor.execute(
        tmp_path,
        "inspect",
        plan,
        state,
        persistence_callback=lambda current, current_plan: persisted.append(
            (current.status.value, current.events[-1].event_type)
        ),
    )

    assert result.status is TaskStatus.FAILED
    started = next(event for event in result.events if event.event_type == "model_request_started")
    failed = next(event for event in result.events if event.event_type == "model_request_failed")
    assert started.data["model"] == "fixture-model"
    assert started.data["timeout_seconds"] == 17
    assert failed.data["error"] == "TimeoutError: model request timed out"
    assert failed.data["duration_seconds"] >= 0
    assert ("running", "model_request_started") in persisted
    assert any(status == "failed" for status, _event in persisted)
    assert not any(event.event_type == "model_response" for event in result.events)



def test_executor_stops_repeated_future_step_actions_instead_of_burning_iterations(tmp_path: Path):
    llm = FakeLLM([
        '{"tool":"write_file","arguments":{"path":"teste.py","content":"for i in range(11):\\n    print(i)"},"plan_step":2}',
        '{"tool":"create_directory","arguments":{"path":"./"},"plan_step":1}',
        '{"tool":"write_file","arguments":{"path":"teste.py","content":"for i in range(11):\\n    print(i)"},"plan_step":2}',
    ])
    loop = AgentLoop(llm, Settings(max_iterations=20))
    plan = ExecutionPlan(
        "Create a counter",
        ["Inspect whether teste.py exists", "Create teste.py if missing", "Write a counter from 0 to 10"],
        ["Read back teste.py"],
        [],
        ["teste.py contains a counter from 0 to 10"],
    )

    result = loop.execute_approved_plan(tmp_path, "create counter", plan, make_state(tmp_path))

    assert result.status is TaskStatus.FAILED
    assert not (tmp_path / "teste.py").exists()
    assert sum(event.event_type == "iteration_started" for event in result.events) == 3
    stalled = next(event for event in result.events if event.event_type == "plan_execution_stalled")
    assert stalled.data["active_step"] == 1
    assert stalled.data["consecutive_no_progress"] == 3
    assert any(event.event_type == "plan_scope_violation" for event in result.events)
    assert any(event.event_type == "execution_no_progress" for event in result.events)


def test_create_directory_reports_existing_directory_as_noop(tmp_path: Path):
    loop = AgentLoop(FakeLLM([]), Settings())
    action = __import__("kardecagent.agent.tools_schema", fromlist=["parse_tool_call"]).parse_tool_call(
        '{"tool":"create_directory","arguments":{"path":"./"},"plan_step":1}'
    )

    result = loop.executor._execute_tool(tmp_path, action, None)

    import json
    payload = json.loads(result)
    assert payload["created"] is False
    assert payload["changed"] is False



def test_executor_advances_only_after_explicit_step_completion(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='counter-fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text(
        "def test_example():\n    assert True\n",
        encoding="utf-8",
    )
    llm = FakeLLM([
        '{"tool":"list_files","arguments":{"path":"."}}',
        '{"tool":"complete_step","arguments":{"step":1,"evidence":"Directory listing inspected; teste.py is absent."},"plan_step":1}',
        '{"tool":"write_file","arguments":{"path":"teste.py","content":""},"plan_step":2}',
        '{"tool":"complete_step","arguments":{"step":2,"evidence":"Created teste.py and verified it exists."},"plan_step":2}',
        '{"tool":"append_file","arguments":{"path":"teste.py","content":"for i in range(11):\\n    print(i)\\n"},"plan_step":3}',
        '{"tool":"complete_step","arguments":{"step":3,"evidence":"Read back the file and confirmed the loop prints values 0 through 10."},"plan_step":3}',
        '{"tool":"finish","arguments":{"reason":"Counter file created and verified.","criteria_evidence":[{"criterion":"teste.py contains a counter from 0 to 10","evidence":"Read-back confirms range(11) and print(i)."}]}}',
    ])
    loop = AgentLoop(llm, Settings(max_iterations=10))
    plan = ExecutionPlan(
        "Create a counter",
        ["Inspect whether teste.py exists", "Create teste.py if missing", "Write a counter from 0 to 10"],
        ["Read back teste.py"],
        [],
        ["teste.py contains a counter from 0 to 10"],
    )

    result = loop.execute_approved_plan(tmp_path, "create counter", plan, make_state(tmp_path))

    assert result.status is TaskStatus.COMPLETED
    assert (tmp_path / "teste.py").read_text(encoding="utf-8") == "for i in range(11):\n    print(i)\n"
    assert [event.data["step"] for event in result.events if event.event_type == "plan_step_completed"] == [1, 2, 3]
