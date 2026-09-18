from pathlib import Path

from kardecagent.cli import _doctor, _tasks, build_parser
from kardecagent.config import Settings


def test_cli_exposes_first_version_commands():
    parser = build_parser()
    assert parser.parse_args(["run", "--project", ".", "--task", "x"]).command == "run"
    assert parser.parse_args(["resume", "--project", ".", "--task", "x"]).command == "resume"
    assert parser.parse_args(["doctor", "--project", "."]).command == "doctor"
    assert parser.parse_args(["tasks", "--project", "."]).command == "tasks"


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
