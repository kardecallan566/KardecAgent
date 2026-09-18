from pathlib import Path

from kardecagent.agent.recovery import RecoveryManager
from kardecagent.agent.state import TaskState, TaskStatus


def _event(state: TaskState, path: Path, content: str, subtask_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    import hashlib
    digest = hashlib.sha256(content.encode()).hexdigest()
    state.record(
        "integrity_change",
        "change",
        changes=[{
            "path": path.relative_to(Path(state.project_root)).as_posix(),
            "exists_before": False,
            "exists_after": True,
            "sha256_before": None,
            "sha256_after": digest,
            "before_data_b64": None,
            "tool": "write_file",
            "plan_step": 1,
            "subtask": True,
            "subtask_id": subtask_id,
        }],
        subtask_id=subtask_id,
    )


def test_recovery_rolls_back_only_failed_subtask(tmp_path: Path):
    state = TaskState("task", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    state.transition(TaskStatus.FAILED)
    _event(state, tmp_path / "first.txt", "keep", "subtask-1")
    _event(state, tmp_path / "failed.txt", "remove", "subtask-2")

    result = RecoveryManager().recover(tmp_path, state, subtask_id="subtask-2")

    assert result.recovered
    assert state.status is TaskStatus.RESUMING
    assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "failed.txt").exists()
    assert result.rolled_back_files == ("failed.txt",)


def test_recovery_preflight_conflict_changes_nothing(tmp_path: Path):
    state = TaskState("task", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    state.transition(TaskStatus.FAILED)
    _event(state, tmp_path / "failed.txt", "audit", "subtask-2")
    (tmp_path / "failed.txt").write_text("changed externally", encoding="utf-8")

    result = RecoveryManager().recover(tmp_path, state, subtask_id="subtask-2")

    assert not result.recovered
    assert result.conflict_paths == ("failed.txt",)
    assert (tmp_path / "failed.txt").read_text(encoding="utf-8") == "changed externally"
    assert any(e.event_type == "recovery_failed" for e in state.events)


def test_recovery_reports_last_consistent_plan_step(tmp_path: Path):
    state = TaskState("task", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    state.transition(TaskStatus.FAILED)
    state.record("plan_step_completed", "step 1", step=1)
    state.record("plan_step_completed", "step 2", step=2)
    _event(state, tmp_path / "failed.txt", "remove", "subtask-2")

    result = RecoveryManager().recover(tmp_path, state, subtask_id="subtask-2")

    assert result.recovered
    assert result.resume_step == 3


def test_recovery_handles_repeated_edits_to_same_file(tmp_path: Path):
    state = TaskState("task", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    state.transition(TaskStatus.FAILED)

    path = tmp_path / "same.txt"
    before = "one"
    middle = "two"
    after = "three"
    path.write_text(before, encoding="utf-8")

    import hashlib
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    state.record("integrity_change", "first edit", changes=[{
        "path": "same.txt", "exists_before": True, "exists_after": True,
        "sha256_before": digest(before), "sha256_after": digest(middle),
        "before_data_b64": __import__("base64").b64encode(before.encode()).decode(),
        "tool": "write_file", "plan_step": 1, "subtask_id": None,
    }])
    path.write_text(middle, encoding="utf-8")
    state.record("integrity_change", "second edit", changes=[{
        "path": "same.txt", "exists_before": True, "exists_after": True,
        "sha256_before": digest(middle), "sha256_after": digest(after),
        "before_data_b64": __import__("base64").b64encode(middle.encode()).decode(),
        "tool": "write_file", "plan_step": 1, "subtask_id": None,
    }])
    path.write_text(after, encoding="utf-8")

    result = RecoveryManager().recover(tmp_path, state)

    assert result.recovered
    assert path.read_text(encoding="utf-8") == before


def test_recovery_transaction_resumes_after_crash(tmp_path: Path):
    state = TaskState("crash-recovery", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    state.transition(TaskStatus.FAILED)

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    _event(state, first, "one", "subtask")
    _event(state, second, "two", "subtask")

    persisted = []
    manager = RecoveryManager()

    calls = {"count": 0}
    def persist(current: TaskState):
        calls["count"] += 1
        persisted.append(current.events[-1].event_type)
        if current.events[-1].event_type == "recovery_rollback_started" and calls["count"] == 5:
            raise KeyboardInterrupt("simulated process crash")

    try:
        manager.recover(tmp_path, state, subtask_id="subtask", persistence_callback=persist)
    except KeyboardInterrupt:
        pass

    # Simulate restart from the durable event stream. The transaction has
    # already rolled back the first unit but has no completion event.
    assert state.status is TaskStatus.RECOVERING
    assert not first.exists()
    assert second.exists()

    result = manager.resume_pending_recovery(tmp_path, state)
    assert result.recovered
    assert not second.exists()
    assert state.status is TaskStatus.RESUMING


def test_recovery_transaction_detects_external_change_after_crash(tmp_path: Path):
    state = TaskState("crash-conflict", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    state.transition(TaskStatus.FAILED)

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    _event(state, first, "one", "subtask")
    _event(state, second, "two", "subtask")

    manager = RecoveryManager()
    manager.recover(tmp_path, state, subtask_id="subtask")

    # Recreate an interrupted transaction from the audited event history.
    state.status = TaskStatus.RECOVERING
    for event in list(state.events):
        if event.event_type == "recovery_completed":
            state.events.remove(event)
    second.write_text("external", encoding="utf-8")

    result = manager.resume_pending_recovery(tmp_path, state)
    assert not result.recovered
    assert result.conflict_paths == ()
    assert "conflict" in (result.error or "")


def test_recovery_transaction_persists_phase_events(tmp_path: Path):
    state = TaskState("phases", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    state.transition(TaskStatus.FAILED)
    _event(state, tmp_path / "a.txt", "a", "subtask")

    result = RecoveryManager().recover(tmp_path, state)
    assert result.recovered
    kinds = [event.event_type for event in state.events]
    assert "recovery_started" in kinds
    assert "recovery_preflight_completed" in kinds
    assert "recovery_rollback_started" in kinds
    assert "recovery_file_rolled_back" in kinds
    assert "recovery_rollback_completed" in kinds
    assert "recovery_completed" in kinds
