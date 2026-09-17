from pathlib import Path
from kardecagent.tools.git import git_diff, git_log, git_status

def test_git_status_non_repo(tmp_path: Path):
    result = git_status(tmp_path)
    assert result.returncode != 0

def test_git_tools_are_callable(tmp_path: Path):
    assert git_diff(tmp_path).command.startswith('git diff')
    assert git_log(tmp_path).command.startswith('git log')