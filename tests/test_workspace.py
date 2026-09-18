from pathlib import Path
import subprocess

import pytest

from kardecagent.tools.workspace import WorkspaceSnapshot, git_has_rename_or_copy


def init_git(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-m", "fixture"],
        cwd=root, check=True, capture_output=True,
    )


def test_new_file_is_removed_on_restore(tmp_path: Path):
    (tmp_path / "tracked.txt").write_text("original", encoding="utf-8")
    init_git(tmp_path)
    snapshot = WorkspaceSnapshot.for_git_repo(tmp_path)

    (tmp_path / "outside.txt").write_text("created", encoding="utf-8")
    assert git_has_rename_or_copy(tmp_path) is False
    assert snapshot.restore({"outside.txt"}) == ["outside.txt"]
    assert not (tmp_path / "outside.txt").exists()


def test_dirty_file_is_restored_byte_for_byte(tmp_path: Path):
    (tmp_path / "outside.txt").write_bytes(b"before\x00bytes")
    init_git(tmp_path)
    (tmp_path / "outside.txt").write_bytes(b"user changes")
    snapshot = WorkspaceSnapshot.for_git_repo(tmp_path)

    (tmp_path / "outside.txt").write_bytes(b"agent changed it")
    assert snapshot.restore({"outside.txt"}) == ["outside.txt"]
    assert (tmp_path / "outside.txt").read_bytes() == b"user changes"


def test_deleted_file_is_restored(tmp_path: Path):
    (tmp_path / "outside.txt").write_bytes(b"keep me")
    init_git(tmp_path)
    snapshot = WorkspaceSnapshot.for_git_repo(tmp_path)

    (tmp_path / "outside.txt").unlink()
    assert snapshot.restore({"outside.txt"}) == ["outside.txt"]
    assert (tmp_path / "outside.txt").read_bytes() == b"keep me"


def test_inside_scope_changes_can_be_kept(tmp_path: Path):
    (tmp_path / "inside.txt").write_text("before", encoding="utf-8")
    init_git(tmp_path)
    snapshot = WorkspaceSnapshot.for_git_repo(tmp_path)

    (tmp_path / "inside.txt").write_text("after", encoding="utf-8")
    assert snapshot.files["inside.txt"].data == b"before"
    assert snapshot.restore(set()) == []


def test_project_snapshot_detects_non_git_changes(tmp_path: Path):
    (tmp_path / "inside.txt").write_text("before", encoding="utf-8")
    snapshot = WorkspaceSnapshot.for_project(tmp_path)
    (tmp_path / "new.txt").write_text("created", encoding="utf-8")
    (tmp_path / "inside.txt").write_text("after", encoding="utf-8")
    after = WorkspaceSnapshot.for_project(tmp_path)
    assert after.changed_paths(snapshot) == {"inside.txt", "new.txt"}


def test_symlink_is_not_auto_remediated(tmp_path: Path):
    (tmp_path / "outside.txt").write_text("safe", encoding="utf-8")
    init_git(tmp_path)
    (tmp_path / "link.txt").symlink_to(tmp_path / "outside.txt")
    snapshot = WorkspaceSnapshot.for_git_repo(tmp_path)

    with pytest.raises(RuntimeError, match="symlink"):
        snapshot.restore({"link.txt"})
