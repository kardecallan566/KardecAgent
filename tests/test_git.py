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


def test_branch_commit_and_merge_workflow(tmp_path: Path):
    from kardecagent.tools.git import git_checkout, git_create_branch, git_commit, git_merge

    _init_repo(tmp_path)
    base = git_current_branch(tmp_path).stdout.strip()
    created = git_create_branch(tmp_path, "feature/workflow", base)
    assert created.returncode == 0
    assert git_current_branch(tmp_path).stdout.strip() == "feature/workflow"

    (tmp_path / "README.md").write_text("feature\n", encoding="utf-8")
    committed = git_commit(tmp_path, "feat: update readme", ["README.md"])
    assert committed.returncode == 0
    assert "feat: update readme" in git_log(tmp_path).stdout

    assert git_checkout(tmp_path, base).returncode == 0
    (tmp_path / "base.txt").write_text("base update\n", encoding="utf-8")
    assert git_commit(tmp_path, "docs: base update", ["base.txt"]).returncode == 0
    assert git_checkout(tmp_path, "feature/workflow").returncode == 0
    merged = git_merge(tmp_path, base)
    assert merged.returncode == 0
    assert (tmp_path / "base.txt").read_text(encoding="utf-8") == "base update\n"


def test_git_commit_rejects_unrelated_staged_changes(tmp_path: Path):
    from kardecagent.tools.git import git_commit

    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("selected\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("unrelated\n", encoding="utf-8")
    import subprocess
    subprocess.run(["git", "add", "other.txt"], cwd=tmp_path, check=True, capture_output=True)

    result = git_commit(tmp_path, "feat: selected only", ["README.md"])
    assert result.returncode != 0
    assert "Unrelated staged changes" in result.stderr
    assert "other.txt" in subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=tmp_path,
        check=True, capture_output=True, text=True
    ).stdout


def test_git_branch_name_is_validated(tmp_path: Path):
    from kardecagent.tools.git import git_create_branch

    _init_repo(tmp_path)
    result = git_create_branch(tmp_path, "--delete-all")
    assert result.returncode != 0
