from pathlib import Path

from kardecagent.agent.loop import AgentLoop
from kardecagent.agent.plan import ExecutionPlan
from kardecagent.agent.state import TaskState
from kardecagent.config import Settings
from kardecagent.llm.client import LLMResponse


class DeterministicLLM:
    """Small deterministic LLM double for end-to-end workflow tests."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def chat(self, messages: list[dict]) -> LLMResponse:
        self.calls += 1
        try:
            return LLMResponse(next(self.responses), {})
        except StopIteration as exc:
            raise AssertionError("deterministic LLM ran out of scripted responses") from exc


def _python_fixture(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        """[project]
name = "first-version-fixture"
version = "0.1.0"
requires-python = ">=3.11"
""",
        encoding="utf-8",
    )
    (root / "test_smoke.py").write_text(
        "def test_smoke():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )


def _plan(*steps: str) -> str:
    import json

    return json.dumps(
        {
            "summary": "Implement the requested fixture change",
            "steps": list(steps),
            "validation": ["Run the project tests"],
            "risks": [],
            "completion_criteria": ["The requested file exists", "The project tests pass"],
        }
    )


def _write_file(content: str, step: int = 1) -> str:
    import json

    return json.dumps(
        {
            "tool": "write_file",
            "arguments": {"path": "feature.py", "content": content},
            "plan_step": step,
        }
    )


def _complete(step: int = 1) -> str:
    import json

    return json.dumps(
        {
            "tool": "complete_step",
            "arguments": {
                "step": step,
                "evidence": "feature.py was written and is ready for validation",
            },
            "plan_step": step,
        }
    )


def _finish() -> str:
    import json

    return json.dumps(
        {
            "tool": "finish",
            "arguments": {
                "reason": "implementation completed",
                "criteria_evidence": [
                    "feature.py exists in the approved project scope",
                    "pytest completed successfully",
                ],
            },
        }
    )


def test_first_version_happy_path_changes_real_project_and_verifies(tmp_path: Path):
    _python_fixture(tmp_path)
    llm = DeterministicLLM(
        [
            _plan("Create feature.py"),
            _write_file("VALUE = 42\n"),
            _complete(),
            _finish(),
        ]
    )

    state = AgentLoop(llm, Settings(max_iterations=4)).run(
        tmp_path,
        "create feature.py with VALUE = 42",
        approval_callback=lambda plan: True,
    )

    assert state.status.value == "completed"
    assert (tmp_path / "feature.py").read_text(encoding="utf-8") == "VALUE = 42\n"
    assert llm.calls == 4
    assert any(event.event_type == "integrity_change" for event in state.events)
    assert any(event.event_type == "plan_step_started" for event in state.events)
    assert any(event.event_type == "plan_step_completed" for event in state.events)
    assert any(event.event_type == "verification" for event in state.events)
    assert any(event.event_type == "completed" for event in state.events)


def test_first_version_rejects_plan_before_any_real_change(tmp_path: Path):
    _python_fixture(tmp_path)
    llm = DeterministicLLM([_plan("Create feature.py")])

    state = AgentLoop(llm, Settings(max_iterations=2)).run(
        tmp_path,
        "create feature.py",
        approval_callback=lambda plan: False,
    )

    assert state.status.value == "failed"
    assert not (tmp_path / "feature.py").exists()
    assert not any(event.event_type == "execution_started" for event in state.events)
    assert not any(event.event_type == "integrity_change" for event in state.events)


def test_first_version_failure_rolls_back_and_retries_approved_plan(tmp_path: Path):
    _python_fixture(tmp_path)
    llm = DeterministicLLM(
        [
            _plan("Create feature.py"),
            # First execution changes the real workspace but never completes the step.
            _write_file("BROKEN = True\\n"),
            _finish(),
            # Recovery rolls the mutation back and retries the same approved step.
            _write_file("VALUE = 42\\n"),
            _complete(),
            _finish(),
        ]
    )

    state = AgentLoop(llm, Settings(max_iterations=2)).run(
        tmp_path,
        "create feature.py with VALUE = 42",
        approval_callback=lambda plan: True,
    )

    assert state.status.value == "completed"
    assert (tmp_path / "feature.py").read_text(encoding="utf-8") == "VALUE = 42\\n"
    assert any(event.event_type == "recovery_started" for event in state.events)
    assert any(event.event_type == "recovery_completed" for event in state.events)
    assert any(event.event_type == "resume_retry_started" for event in state.events)
    assert any(event.event_type == "resume_retry_finished" for event in state.events)
    assert any(
        event.event_type == "recovery_file_rolled_back"
        and event.data.get("path") == "feature.py"
        for event in state.events
    )
    assert llm.calls == 6


def test_first_version_recovery_preserves_completed_step_and_resumes_next_step(tmp_path: Path):
    _python_fixture(tmp_path)
    llm = DeterministicLLM(
        [
            _plan("Create feature.py", "Run validation"),
            _write_file("VALUE = 42\\n", step=1),
            _complete(step=1),
            # The first execution reaches max iterations after a completed step.
            '{"tool":"run_checks","arguments":{"kind":"test"},"plan_step":2}',
            _complete(step=2),
            _finish(),
        ]
    )

    state = AgentLoop(llm, Settings(max_iterations=2)).run(
        tmp_path,
        "create feature.py with VALUE = 42",
        approval_callback=lambda plan: True,
    )

    assert state.status.value == "completed"
    assert (tmp_path / "feature.py").read_text(encoding="utf-8") == "VALUE = 42\\n"
    assert any(event.event_type == "recovery_skipped" for event in state.events)
    assert any(event.event_type == "recovery_completed" for event in state.events)
    assert any(event.event_type == "resume_retry_started" for event in state.events)
    assert any(
        event.event_type == "plan_step_completed"
        and event.data.get("step") == 1
        for event in state.events
    )
    assert llm.calls == 6


def _tool(name: str, arguments: dict, step: int = 1) -> str:
    import json

    return json.dumps(
        {"tool": name, "arguments": arguments, "plan_step": step},
        ensure_ascii=False,
    )


def test_end_to_end_creates_multiple_files_and_verifies_each_artifact(tmp_path: Path):
    _python_fixture(tmp_path)
    llm = DeterministicLLM(
        [
            _plan("Create the service module", "Create the settings module"),
            _tool("write_file", {"path": "src/service.py", "content": "def total(values):\n    return sum(values)\n"}, 1),
            _complete(1),
            _tool("write_file", {"path": "src/settings.py", "content": "DEFAULT_LIMIT = 25\n"}, 2),
            _complete(2),
            _finish(),
        ]
    )

    state = AgentLoop(llm, Settings(max_iterations=6)).run(
        tmp_path,
        "create a service module and a settings module",
        approval_callback=lambda plan: True,
    )

    assert state.status.value == "completed"
    assert (tmp_path / "src/service.py").read_text(encoding="utf-8") == (
        "def total(values):\n    return sum(values)\n"
    )
    assert (tmp_path / "src/settings.py").read_text(encoding="utf-8") == "DEFAULT_LIMIT = 25\n"
    changed = {
        item["path"]
        for event in state.events
        if event.event_type == "integrity_change"
        for item in event.data.get("changes", [])
    }
    assert {"src/service.py", "src/settings.py"} <= changed
    assert sum(event.event_type == "plan_step_completed" for event in state.events) == 2
    assert any(event.event_type == "verification" for event in state.events)


def test_end_to_end_edits_existing_function_without_touching_unrelated_code(tmp_path: Path):
    _python_fixture(tmp_path)
    source = (
        "def total(values):\n"
        "    return len(values)\n"
        "\n"
        "def display_name(name):\n"
        "    return name.strip()\n"
    )
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir()
    target.write_text(source, encoding="utf-8")
    expected = source.replace(
        "def total(values):\n    return len(values)",
        "def total(values):\n    return sum(values)",
        1,
    )
    llm = DeterministicLLM(
        [
            _plan("Inspect and update the total function"),
            _tool("read_file", {"path": "src/service.py"}),
            _tool(
                "replace_in_file",
                {
                    "path": "src/service.py",
                    "find": "def total(values):\n    return len(values)",
                    "replace": "def total(values):\n    return sum(values)",
                },
            ),
            _complete(),
            _finish(),
        ]
    )

    state = AgentLoop(llm, Settings(max_iterations=5)).run(
        tmp_path,
        "change total to sum the values and preserve the other functions",
        approval_callback=lambda plan: True,
    )

    assert state.status.value == "completed"
    assert target.read_text(encoding="utf-8") == expected
    assert "def display_name(name):\n    return name.strip()" in target.read_text(encoding="utf-8")
    assert any(
        event.event_type == "integrity_change"
        and any(change["path"] == "src/service.py" for change in event.data.get("changes", []))
        for event in state.events
    )


def test_end_to_end_concurrent_user_edit_is_never_overwritten(tmp_path: Path):
    _python_fixture(tmp_path)
    target = tmp_path / "shared.py"
    target.write_text("VALUE = 'original'\n", encoding="utf-8")

    class ConcurrentDeterministicLLM(DeterministicLLM):
        def chat(self, messages: list[dict]) -> LLMResponse:
            if self.calls == 1:
                target.write_text("VALUE = 'user edit'\n", encoding="utf-8")
            return super().chat(messages)

    llm = ConcurrentDeterministicLLM(
        [
            _tool("read_file", {"path": "shared.py"}),
            _tool("write_file", {"path": "shared.py", "content": "VALUE = 'agent edit'\n"}),
        ]
    )
    plan = ExecutionPlan(
        "Update shared module",
        ["Read and update shared.py"],
        ["Confirm the file contents"],
        [],
        ["The requested update is present"],
    )

    state = AgentLoop(llm, Settings(max_iterations=2)).execute_approved_plan(
        tmp_path,
        "update shared.py",
        plan,
        TaskState("concurrent edit", str(tmp_path)),
        context={"subtask": {"id": "concurrent-user-edit-e2e"}},
    )

    assert target.read_text(encoding="utf-8") == "VALUE = 'user edit'\n"
    assert state.status.value == "max_iterations"
    assert any(
        event.event_type == "tool_error" and "changed concurrently" in event.message
        for event in state.events
    )
