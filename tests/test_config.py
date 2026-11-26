from pathlib import Path
import pytest
from kardecagent.config import resolve_project_root
def test_resolve_project_root(tmp_path:Path):assert resolve_project_root(tmp_path)==tmp_path.resolve()
def test_missing(tmp_path:Path):
    with pytest.raises(ValueError):resolve_project_root(tmp_path/"missing")



def test_max_retries_can_be_configured_with_current_environment_prefix(monkeypatch):
    from kardecagent.config import Settings

    monkeypatch.setenv("KARDECAGENT_MAX_RETRIES", "3")
    monkeypatch.delenv("KARDEC_MAX_RETRIES", raising=False)

    assert Settings.from_env().max_retries == 3


def test_max_retries_supports_legacy_environment_prefix(monkeypatch):
    from kardecagent.config import Settings

    monkeypatch.delenv("KARDECAGENT_MAX_RETRIES", raising=False)
    monkeypatch.setenv("KARDEC_MAX_RETRIES", "2")

    assert Settings.from_env().max_retries == 2


def test_max_retries_environment_value_cannot_be_negative(monkeypatch):
    from kardecagent.config import Settings

    monkeypatch.setenv("KARDECAGENT_MAX_RETRIES", "-4")

    assert Settings.from_env().max_retries == 0


def test_fast_model_and_routing_can_be_enabled_from_environment(monkeypatch):
    from kardecagent.config import Settings

    monkeypatch.setenv("KARDECAGENT_FAST_MODEL", "qwen3.5:0.8b")
    monkeypatch.setenv("KARDECAGENT_MODEL_ROUTING_ENABLED", "true")
    settings = Settings.from_env()

    assert settings.llm_fast_model == "qwen3.5:0.8b"
    assert settings.llm_routing_enabled is True


def test_routing_supports_legacy_environment_prefix(monkeypatch):
    from kardecagent.config import Settings

    monkeypatch.delenv("KARDECAGENT_FAST_MODEL", raising=False)
    monkeypatch.delenv("KARDECAGENT_MODEL_ROUTING_ENABLED", raising=False)
    monkeypatch.setenv("KARDEC_FAST_MODEL", "granite4:3b")
    monkeypatch.setenv("KARDEC_MODEL_ROUTING_ENABLED", "yes")
    settings = Settings.from_env()

    assert settings.llm_fast_model == "granite4:3b"
    assert settings.llm_routing_enabled is True



def test_parallelism_is_configurable_without_a_manual_model_list(monkeypatch):
    from kardecagent.config import Settings

    monkeypatch.setenv("KARDECAGENT_ESCALATION_MODELS", "ignored:1b,ignored:2b")
    monkeypatch.setenv("KARDECAGENT_MAX_PARALLEL_SUBTASKS", "3")

    settings = Settings.from_env()

    assert settings.max_parallel_subtasks == 3
    assert not hasattr(settings, "llm_escalation_models")


def test_parallelism_is_bounded(monkeypatch):
    from kardecagent.config import Settings

    monkeypatch.setenv("KARDECAGENT_MAX_PARALLEL_SUBTASKS", "99")

    assert Settings.from_env().max_parallel_subtasks == 4
