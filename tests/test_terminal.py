from pathlib import Path

import pytest

from kardecagent.tools.terminal import (
    is_command_allowed,
    is_command_blocked,
    parse_command,
    run_command,
)


def test_blocked():
    assert is_command_blocked("format C:")
    assert is_command_blocked("git status & del /s file.txt")
    assert is_command_blocked("python -c \"print('ok')\" && whoami")
    assert is_command_blocked("python -c \"print('> is just text')\" > output.txt")
    assert is_command_blocked("powershell -Command Get-ChildItem")


def test_allowlist():
    assert is_command_allowed("python -c \"print('ok')\"")
    assert is_command_allowed("pnpm test")
    assert not is_command_allowed("curl https://example.com")


def test_parser():
    parsed = parse_command('python -c "print(\'ok\')"')
    assert parsed is not None
    assert parsed.executable == "python"
    assert parsed.argv[0] == "python"
    assert parsed.argv[2] == "print('ok')"
    assert not parse_command("python -c \"print('ok')\" && whoami")


def test_safe(tmp_path: Path):
    assert "ok" in run_command(tmp_path, 'python -c "print(\'ok\')"').stdout


def test_run_blocked(tmp_path: Path):
    with pytest.raises(PermissionError):
        run_command(tmp_path, "format C:")


def test_run_unapproved(tmp_path: Path):
    with pytest.raises(PermissionError):
        run_command(tmp_path, "curl https://example.com")


def test_run_does_not_use_shell(tmp_path: Path):
    marker = tmp_path / "marker.txt"
    with pytest.raises(PermissionError):
        run_command(
            tmp_path,
            f'python -c "open(\'{marker}\', \'w\').write(\'x\')" && echo bypass',
        )
    assert not marker.exists()
