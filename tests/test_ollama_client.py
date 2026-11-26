from kardecagent.cli import _build_llm
from kardecagent.config import Settings
from kardecagent.llm import OllamaClient


def test_settings_default_to_ollama():
    settings = Settings()
    assert settings.llm_provider == "ollama"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.llm_model == "qwen2.5-coder:3b"


def test_build_llm_uses_ollama(monkeypatch):
    from kardecagent.llm.router import EscalatingLLMClient

    monkeypatch.setattr(
        "kardecagent.llm.ollama.OllamaClient.list_models",
        lambda self: [{"name": "qwen2.5-coder:3b", "size": 2_000_000_000}],
    )
    client = _build_llm(Settings())
    assert isinstance(client, EscalatingLLMClient)
    assert client.clients[0].base_url == "http://127.0.0.1:11434"
    assert client.model == "qwen2.5-coder:3b"


def test_ollama_client_chat_uses_native_api(monkeypatch):
    calls = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "role": "assistant",
                    "content": "KARDECAGENT_OK",
                }
            }

    def fake_post(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return FakeResponse()

    monkeypatch.setattr("kardecagent.llm.ollama.httpx.post", fake_post)

    client = OllamaClient()
    response = client.chat(
        [{"role": "user", "content": "Health check."}],
        temperature=0.0,
    )

    assert response.content == "KARDECAGENT_OK"
    assert calls["url"] == "http://127.0.0.1:11434/api/chat"
    assert calls["kwargs"]["json"]["model"] == "qwen2.5-coder:3b"
    assert calls["kwargs"]["json"]["stream"] is False
    assert calls["kwargs"]["json"]["options"]["temperature"] == 0.0


def test_ollama_client_list_models(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "qwen3-coder:30b"}]}

    monkeypatch.setattr(
        "kardecagent.llm.ollama.httpx.get",
        lambda *args, **kwargs: FakeResponse(),
    )

    assert OllamaClient().list_models() == [{"name": "qwen3-coder:30b"}]


def test_ollama_client_show_model(monkeypatch):
    calls = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"details": {"family": "qwen", "parameter_size": "4B", "quantization_level": "Q4_K_M"}, "capabilities": ["tools"]}

    def fake_post(url, **kwargs):
        calls["url"] = url
        calls["json"] = kwargs["json"]
        return FakeResponse()

    monkeypatch.setattr("kardecagent.llm.ollama.httpx.post", fake_post)
    metadata = OllamaClient().show_model("qwen3.5:4b")
    assert calls["url"].endswith("/api/show")
    assert calls["json"]["model"] == "qwen3.5:4b"
    assert metadata["details"]["quantization_level"] == "Q4_K_M"
    assert "tools" in metadata["capabilities"]


def test_ollama_client_server_version(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"version": "0.12.0"}

    monkeypatch.setattr("kardecagent.llm.ollama.httpx.get", lambda *args, **kwargs: FakeResponse())
    assert OllamaClient().server_version() == "0.12.0"



def test_build_llm_discovers_installed_models_and_creates_escalation_pool(monkeypatch):
    from kardecagent.llm.router import EscalatingLLMClient

    monkeypatch.setattr(
        "kardecagent.llm.ollama.OllamaClient.list_models",
        lambda self: [
            {"name": "qwen-coder:3b", "size": 3_000_000_000},
            {"name": "qwen-coder:14b", "size": 9_000_000_000},
            {"name": "llama:8b", "size": 5_000_000_000},
        ],
    )
    settings = Settings(llm_model="qwen-coder:3b")

    client = _build_llm(settings)

    assert isinstance(client, EscalatingLLMClient)
    assert client.model == "qwen-coder:3b"
    assert [item.model for item in client.clients[1:]] == ["llama:8b", "qwen-coder:14b"]


def test_build_llm_automatically_selects_installed_model_when_configured_model_is_missing(monkeypatch):
    from kardecagent.llm.router import EscalatingLLMClient

    monkeypatch.setattr(
        "kardecagent.llm.ollama.OllamaClient.list_models",
        lambda self: [
            {"name": "general:2b", "size": 2_000_000_000},
            {"name": "qwen-coder:4b", "size": 3_000_000_000},
            {"name": "qwen-coder:14b", "size": 9_000_000_000},
        ],
    )

    client = _build_llm(Settings(llm_model="not-installed:latest"))

    assert isinstance(client, EscalatingLLMClient)
    assert client.model == "qwen-coder:4b"
    assert [item.model for item in client.clients[1:]] == ["qwen-coder:14b"]


def test_build_llm_uses_smallest_installed_model_as_fast_route_when_enabled(monkeypatch):
    from kardecagent.llm.router import AdaptiveLLMClient, EscalatingLLMClient

    monkeypatch.setattr(
        "kardecagent.llm.ollama.OllamaClient.list_models",
        lambda self: [
            {"name": "qwen-coder:3b", "size": 2_000_000_000},
            {"name": "qwen-coder:14b", "size": 9_000_000_000},
        ],
    )

    client = _build_llm(Settings(
        llm_model="qwen-coder:14b",
        llm_routing_enabled=True,
        llm_fast_model="",
    ))

    assert isinstance(client, EscalatingLLMClient)
    assert isinstance(client.clients[0], AdaptiveLLMClient)
    assert client.clients[0].primary.model == "qwen-coder:14b"
    assert client.clients[0].fast.model == "qwen-coder:3b"


def test_build_llm_fails_clearly_when_ollama_has_no_installed_models(monkeypatch):
    monkeypatch.setattr(
        "kardecagent.llm.ollama.OllamaClient.list_models",
        lambda self: [],
    )

    import pytest

    with pytest.raises(RuntimeError, match="No Ollama models are installed"):
        _build_llm(Settings())



def test_unenabled_fast_model_remains_available_for_escalation(monkeypatch):
    from kardecagent.llm.router import EscalatingLLMClient

    monkeypatch.setattr(
        "kardecagent.llm.ollama.OllamaClient.list_models",
        lambda self: [
            {"name": "qwen-coder:3b", "size": 2_000_000_000},
            {"name": "qwen-coder:14b", "size": 9_000_000_000},
        ],
    )

    client = _build_llm(Settings(
        llm_model="qwen-coder:3b",
        llm_fast_model="qwen-coder:14b",
        llm_routing_enabled=False,
    ))

    assert isinstance(client, EscalatingLLMClient)
    assert [item.model for item in client.clients[1:]] == ["qwen-coder:14b"]
