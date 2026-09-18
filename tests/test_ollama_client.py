from kardecagent.cli import _build_llm
from kardecagent.config import Settings
from kardecagent.llm import OllamaClient


def test_settings_default_to_ollama():
    settings = Settings()
    assert settings.llm_provider == "ollama"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.llm_model == "qwen2.5-coder:3b"


def test_build_llm_uses_ollama():
    client = _build_llm(Settings())
    assert isinstance(client, OllamaClient)
    assert client.base_url == "http://127.0.0.1:11434"
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
