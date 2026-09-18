from pathlib import Path
import pytest
from kardecagent.tools.terminal import is_command_allowed, is_command_blocked, run_command

def test_blocked():
    assert is_command_blocked("format C:")
    assert is_command_blocked("git status & del /s file.txt")

def test_allowlist():
    assert is_command_allowed("python -c \"print('ok')\"")
    assert is_command_allowed("pnpm test")
    assert not is_command_allowed("curl https://example.com")

def test_safe(tmp_path: Path):
    assert "ok" in run_command(tmp_path, 'python -c "print(\'ok\')"').stdout

def test_run_blocked(tmp_path: Path):
    with pytest.raises(PermissionError):
        run_command(tmp_path, "format C:")

def test_run_unapproved(tmp_path: Path):
    with pytest.raises(PermissionError):
        run_command(tmp_path, "curl https://example.com")
