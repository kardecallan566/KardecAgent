from pathlib import Path

from kardecagent.tools.git import (
    create_checkpoint,
    git_current_branch,
    git_diff,
    git_has_uncommitted_changes,
    git_is_repo,
    git_log,
    git_status,
)


def _init_repo(path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "KardecAgent Test"],
        cwd=path,
        check=True,
    )
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def test_git_status_non_repo(tmp_path: Path):
    result = git_status(tmp_path)
    assert result.returncode != 0
    assert not git_is_repo(tmp_path)


def test_git_tools_are_callable(tmp_path: Path):
    assert git_diff(tmp_path).command.startswith("git diff")
    assert git_log(tmp_path).command.startswith("git log")


def test_checkpoint_and_dirty_detection(tmp_path: Path):
    _init_repo(tmp_path)

    assert git_is_repo(tmp_path)
    assert git_current_branch(tmp_path).returncode == 0
    assert not git_has_uncommitted_changes(tmp_path)

    checkpoint = create_checkpoint(tmp_path, "add feature")
    assert checkpoint.returncode == 0
    assert checkpoint.stderr == ""

    branches = subprocess_run(["git", "branch", "--list", "agent/checkpoint-*"], tmp_path)
    assert "agent/checkpoint-add-feature-" in branches

    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")
    assert git_has_uncommitted_changes(tmp_path)


def subprocess_run(command: list[str], cwd: Path) -> str:
    import subprocess

    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
