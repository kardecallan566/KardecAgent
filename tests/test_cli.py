from pathlib import Path

from kardecagent.cli import _doctor, _models, _tasks, build_parser
from kardecagent.config import Settings


def test_cli_exposes_first_version_commands():
    parser = build_parser()
    args_run = parser.parse_args(["run", "--project", ".", "--task", "x", "--model", "qwen2.5-coder:7b", "--provider", "ollama"])
    assert args_run.command == "run"
    assert args_run.model == "qwen2.5-coder:7b"
    assert args_run.provider == "ollama"
    assert parser.parse_args(["resume", "--project", ".", "--task", "x"]).command == "resume"
    assert parser.parse_args(["doctor", "--project", "."]).command == "doctor"
    assert parser.parse_args(["tasks", "--project", "."]).command == "tasks"\n    assert parser.parse_args(["models"]).command == "models"\n    assert parser.parse_args(["models", "--installed-only"]).installed_only\n    assert parser.parse_args(["models", "--inspect", "qwen3.5:4b"]).inspect == "qwen3.5:4b"


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
\n\ndef test_models_command_lists_installed_and_catalog(monkeypatch, capsys):\n    from kardecagent import cli\n\n    class FakeOllama:\n        def __init__(self, *args, **kwargs):\n            pass\n\n        def list_models(self):\n            return [{"name": "qwen3.5:4b", "size": 3 * 1024**3}]\n\n    monkeypatch.setattr(cli, "OllamaClient", FakeOllama)\n    assert _models(Settings(), installed_only=False) == 0\n    output = capsys.readouterr().out\n    assert "qwen3.5:4b" in output\n    assert "[installed]" in output\n    assert "qwen3.8-flash-next:125b-mlx" in output\n    assert "NOT a lightweight GPU model" in output\n