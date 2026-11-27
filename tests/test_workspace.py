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
    try:
        (tmp_path / "link.txt").symlink_to(tmp_path / "outside.txt")
    except OSError:
        pytest.skip("Symlink creation not permitted in this Windows environment")
    snapshot = WorkspaceSnapshot.for_git_repo(tmp_path)

    with pytest.raises(RuntimeError, match="symlink"):
        snapshot.restore({"link.txt"})



def test_recorded_artifacts_are_verified_against_current_disk_bytes(tmp_path: Path):
    import hashlib

    from kardecagent.tools.workspace import verify_recorded_changes

    target = tmp_path / "artifact.txt"
    target.write_bytes(b"verified")
    change = {
        "path": "artifact.txt",
        "exists_after": True,
        "sha256_after": hashlib.sha256(b"verified").hexdigest(),
    }
    assert verify_recorded_changes(tmp_path, [change])["verified"] is True

    target.write_bytes(b"changed externally")
    report = verify_recorded_changes(tmp_path, [change])
    assert report["verified"] is False
    assert report["files"][0]["status"] == "mismatch"


def test_recorded_artifact_verification_checks_expected_deletion(tmp_path: Path):
    from kardecagent.tools.workspace import verify_recorded_changes

    report = verify_recorded_changes(tmp_path, [{
        "path": "removed.txt",
        "exists_after": False,
        "sha256_after": None,
    }])
    assert report["verified"] is True


def test_multi_file_rollback_preflights_all_conflicts_before_mutating(tmp_path: Path):
    import base64
    import hashlib

    from kardecagent.tools.workspace import rollback_file_changes

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"after-first")
    second.write_bytes(b"external-change")
    changes = [
        {
            "path": "first.txt",
            "exists_before": True,
            "before_data_b64": base64.b64encode(b"before-first").decode("ascii"),
            "sha256_after": hashlib.sha256(b"after-first").hexdigest(),
        },
        {
            "path": "second.txt",
            "exists_before": True,
            "before_data_b64": base64.b64encode(b"before-second").decode("ascii"),
            "sha256_after": hashlib.sha256(b"expected-second").hexdigest(),
        },
    ]

    with pytest.raises(RuntimeError, match="rollback conflict"):
        rollback_file_changes(tmp_path, changes)

    assert first.read_bytes() == b"after-first"
    assert second.read_bytes() == b"external-change"
