from pathlib import Path
import pytest
from types import SimpleNamespace

from kardecagent.cli import _build_llm, _doctor, _models, _ollama_health, _tasks, build_parser
from kardecagent.config import Settings


def test_cli_exposes_first_version_commands():
    parser = build_parser()
    args_run = parser.parse_args(["run", "--project", ".", "--task", "x", "--model", "qwen2.5-coder:7b", "--provider", "ollama"])
    assert args_run.command == "run"
    assert args_run.model == "qwen2.5-coder:7b"
    assert args_run.provider == "ollama"
    assert parser.parse_args(["resume", "--project", ".", "--task", "x"]).command == "resume"
    assert parser.parse_args(["doctor", "--project", "."]).command == "doctor"
    assert parser.parse_args(["tasks", "--project", "."]).command == "tasks"
    assert parser.parse_args(["ollama-health"]).command == "ollama-health"
    assert parser.parse_args(["models"]).command == "models"
    assert parser.parse_args(["models", "--installed-only"]).installed_only
    assert parser.parse_args(["models", "--inspect", "qwen3.5:4b"]).inspect == "qwen3.5:4b"


def test_tasks_command_handles_empty_store(tmp_path: Path, capsys):
    assert _tasks(tmp_path) == 0
    assert "No persisted tasks." in capsys.readouterr().out


def test_doctor_reports_local_llm_failure_without_modifying_project(tmp_path: Path, capsys):
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    settings = Settings(llm_base_url="http://127.0.0.1:1/v1", llm_timeout_seconds=0.1)
    assert _doctor(settings, tmp_path) == 1
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert before == after
    assert "[FAIL] Local LLM:" in capsys.readouterr().out


def test_models_command_lists_installed_and_catalog(monkeypatch, capsys):
    from kardecagent import cli

    class FakeOllama:
        def __init__(self, *args, **kwargs):
            pass

        def list_models(self):
            return [{"name": "qwen3.5:4b", "size": 3 * 1024**3}]

    monkeypatch.setattr(cli, "OllamaClient", FakeOllama)
    assert _models(Settings(), installed_only=False) == 0
    output = capsys.readouterr().out
    assert "qwen3.5:4b" in output
    assert "[installed]" in output
    assert "qwen3.8-flash-next:125b-mlx" in output
    assert "NOT a lightweight GPU model" in output


def test_ollama_health_emits_machine_readable_json(monkeypatch, capsys):
    from kardecagent import cli

    class FakeOllama:
        def __init__(self, *args, **kwargs):
            pass

        def server_version(self):
            return "0.13.0"

        def list_models(self):
            return [{"name": "qwen2.5-coder:3b"}]

        def health_check(self):
            return SimpleNamespace(content="KARDECAGENT_OK")

    monkeypatch.setattr(cli, "OllamaClient", FakeOllama)

    assert _ollama_health(Settings()) == 0
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "version": "0.13.0",
        "models": ["qwen2.5-coder:3b"],
        "model": "qwen2.5-coder:3b",
        "response": "KARDECAGENT_OK",
    }


def test_ollama_health_returns_json_error_when_model_is_missing(monkeypatch, capsys):
    from kardecagent import cli

    class FakeOllama:
        def __init__(self, *args, **kwargs):
            pass

        def server_version(self):
            return "0.13.0"

        def list_models(self):
            return [{"name": "other-model"}]

    monkeypatch.setattr(cli, "OllamaClient", FakeOllama)

    assert _ollama_health(Settings()) == 1
    import json
    payload = json.loads(capsys.readouterr().out)
    assert "not installed" in payload["error"]


def test_build_llm_wraps_primary_and_fast_clients_when_routing_enabled(monkeypatch):
    from kardecagent import cli

    class FakeClient:
        def __init__(self, base_url, model, timeout):
            self.model = model

    class FakeRouter:
        def __init__(self, primary, fast):
            self.primary = primary
            self.fast = fast

    monkeypatch.setattr(cli, "OllamaClient", FakeClient)
    monkeypatch.setattr(cli, "AdaptiveLLMClient", FakeRouter)
    settings = Settings(
        llm_model="qwen3.5:9b",
        llm_fast_model="qwen3.5:0.8b",
        llm_routing_enabled=True,
    )

    client = _build_llm(settings)

    assert isinstance(client, FakeRouter)
    assert client.primary.model == "qwen3.5:9b"
    assert client.fast.model == "qwen3.5:0.8b"


def test_build_llm_keeps_single_model_when_routing_is_disabled(monkeypatch):
    from kardecagent import cli

    class FakeClient:
        def __init__(self, base_url, model, timeout):
            self.model = model

    monkeypatch.setattr(cli, "OllamaClient", FakeClient)
    settings = Settings(llm_model="qwen3.5:9b", llm_fast_model="qwen3.5:0.8b")

    client = _build_llm(settings)

    assert isinstance(client, FakeClient)
    assert client.model == "qwen3.5:9b"


def test_build_llm_rejects_enabled_routing_without_fast_model():
    from kardecagent import cli

    settings = Settings(llm_routing_enabled=True, llm_fast_model="")

    with pytest.raises(ValueError, match="FAST_MODEL"):
        _build_llm(settings)
