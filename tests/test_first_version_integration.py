from pathlib import Path

from kardecagent.agent.loop import AgentLoop
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
            _write_file("BROKEN = True\n"),
            _finish(),
            # Retry starts from the last consistent step after recovery.
            _write_file("VALUE = 42\n"),
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
    assert (tmp_path / "feature.py").read_text(encoding="utf-8") == "VALUE = 42\n"
    assert any(event.event_type == "recovery_started" for event in state.events)
    assert any(event.event_type == "recovery_completed" for event in state.events)
    assert any(event.event_type == "resume_retry_started" for event in state.events)
    assert any(event.event_type == "resume_retry_finished" for event in state.events)
    assert llm.calls == 6
