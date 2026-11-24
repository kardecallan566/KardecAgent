from __future__ import annotations

from typing import Any

import pytest

from kardecagent.llm.client import LLMResponse
from kardecagent.llm.router import AdaptiveLLMClient, ModelRouter


class FakeClient:
    def __init__(self, model: str, content: str = "ok", error: Exception | None = None):
        self.model = model
        self.content = content
        self.error = error
        self.calls: list[list[dict[str, Any]]] = []

    def chat(self, messages, *, temperature=0.2):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return LLMResponse(self.content, {"model": self.model, "temperature": temperature})


def test_short_conversational_question_uses_fast_model():
    router = ModelRouter("qwen-coder:9b", "qwen-fast:1b")
    decision = router.decide([{"role": "user", "content": "quanto é 5+3?"}])

    assert decision.tier == "fast"
    assert decision.model == "qwen-fast:1b"


@pytest.mark.parametrize(
    "task",
    [
        "Implemente autenticação com testes.",
        "Corrija o bug no parser.",
        "Create a new project and add a React editor.",
        "Refactor this module and update the tests.",
        "Planeje a arquitetura do projeto.",
    ],
)
def test_software_engineering_requests_use_primary_model(task):
    router = ModelRouter("qwen-coder:9b", "qwen-fast:1b")
    decision = router.decide([{"role": "user", "content": task}])

    assert decision.tier == "primary"
    assert decision.model == "qwen-coder:9b"


def test_json_task_context_routes_using_the_actual_task_not_context_size():
    router = ModelRouter("qwen-coder:9b", "qwen-fast:1b")
    decision = router.decide([
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": '{"task":"quanto é 5+3?","project_files":["a.py","b.py"]}'},
    ])

    assert decision.tier == "fast"


def test_planning_phase_and_tool_protocol_always_use_primary():
    router = ModelRouter("qwen-coder:9b", "qwen-fast:1b")
    planning = router.decide([
        {"role": "system", "content": "You are in the planning phase."},
        {"role": "user", "content": "What is 5+3?"},
    ])
    tool = router.decide([
        {"role": "user", "content": "What is 5+3?"},
        {"role": "assistant", "content": '{"tool":"write_file"}'},
    ])

    assert planning.tier == "primary"
    assert tool.tier == "primary"


def test_code_and_long_requests_use_primary():
    router = ModelRouter("qwen-coder:9b", "qwen-fast:1b")
    code = router.decide([{"role": "user", "content": "Review this: def add(a, b): return a + b"}])
    long = router.decide([{"role": "user", "content": "hello " * 150}])

    assert code.tier == "primary"
    assert long.tier == "primary"


def test_router_records_selected_tier_and_model_in_response_metadata():
    primary = FakeClient("qwen-coder:9b")
    fast = FakeClient("qwen-fast:1b")
    client = AdaptiveLLMClient(primary, fast)

    response = client.chat([{"role": "user", "content": "quanto é 5+3?"}], temperature=0.0)

    assert len(fast.calls) == 1
    assert not primary.calls
    assert response.raw["_kardecagent_routing"] == {
        "tier": "fast",
        "model": "qwen-fast:1b",
        "reason": "short conversational request",
        "fallback": False,
    }


def test_fast_model_failure_falls_back_to_primary_without_losing_request():
    primary = FakeClient("qwen-coder:9b", content="primary response")
    fast = FakeClient("qwen-fast:1b", error=RuntimeError("model not loaded"))
    client = AdaptiveLLMClient(primary, fast)
    messages = [{"role": "user", "content": "quanto é 5+3?"}]

    response = client.chat(messages)

    assert response.content == "primary response"
    assert fast.calls == [messages]
    assert primary.calls == [messages]
    route = response.raw["_kardecagent_routing"]
    assert route["tier"] == "primary"
    assert route["model"] == "qwen-coder:9b"
    assert route["fallback"] is True


def test_router_uses_primary_when_fast_model_is_not_configured():
    router = ModelRouter("qwen-coder:9b", "")
    decision = router.decide([{"role": "user", "content": "hello"}])

    assert decision.tier == "primary"
    assert "not configured" in decision.reason


def test_primary_model_failure_is_not_hidden_by_another_fallback():
    primary = FakeClient("qwen-coder:9b", error=RuntimeError("primary failed"))
    fast = FakeClient("qwen-fast:1b")
    client = AdaptiveLLMClient(primary, fast)

    with pytest.raises(RuntimeError, match="primary failed"):
        client.chat([{"role": "user", "content": "implement a feature"}])


def test_fast_model_is_not_retried_for_every_request_after_failure():
    primary = FakeClient("qwen-coder:9b", content="primary response")
    fast = FakeClient("qwen-fast:1b", error=RuntimeError("model not loaded"))
    client = AdaptiveLLMClient(primary, fast)
    messages = [{"role": "user", "content": "quanto é 5+3?"}]

    first = client.chat(messages)
    second = client.chat(messages)

    assert len(fast.calls) == 1
    assert len(primary.calls) == 2
    assert first.raw["_kardecagent_routing"]["fallback"] is True
    assert second.raw["_kardecagent_routing"]["fallback"] is True
    assert "disabled after an earlier failure" in second.raw["_kardecagent_routing"]["reason"]
