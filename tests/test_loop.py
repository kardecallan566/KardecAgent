from pathlib import Path

from kardecagent.agent.loop import AgentLoop
from kardecagent.config import Settings
from kardecagent.llm.client import LLMResponse


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)

    def chat(self, messages: list[dict]) -> LLMResponse:
        return LLMResponse(next(self.responses), {})


def _make_project(root: Path) -> None:
    (root / "test_example.py").write_text(
        "def test_example():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )


def test_finish_requires_verification(tmp_path: Path):
    _make_project(tmp_path)
    llm = FakeLLM(['{"tool":"finish","arguments":{"reason":"implemented"}}'])
    state = AgentLoop(llm, Settings(max_iterations=1)).run(tmp_path, "verify task")

    assert state.status.value == "completed"
    assert any(event.event_type == "verification" for event in state.events)


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
        '{"tool":"finish","arguments":{"reason":"done"}}',
        '{"tool":"finish","arguments":{"reason":"done again"}}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=2)).run(tmp_path, "fix task")

    assert state.status.value == "max_iterations"
    assert any(event.event_type == "verification_failed" for event in state.events)



def test_static_html_finish_is_verified(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html lang='pt-BR'><head>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Site</title></head><body><main>OK</main></body></html>",
        encoding="utf-8",
    )
    llm = FakeLLM(['{"tool":"finish","arguments":{"reason":"site implemented"}}'])
    state = AgentLoop(llm, Settings(max_iterations=1)).run(tmp_path, "create site")
    assert state.status.value == "completed"
    assert any(
        event.event_type == "project_detected"
        and event.data.get("kind") == "static-html"
        for event in state.events
    )
