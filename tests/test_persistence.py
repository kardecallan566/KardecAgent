from pathlib import Path

from kardecagent.agent.persistence import TaskStore, PersistenceError, TaskJournal
from kardecagent.agent.plan import ExecutionPlan
from kardecagent.agent.state import TaskState, TaskStatus
from kardecagent.tasks import Subtask, SubtaskStatus, TaskBoard


def make_plan():
    return ExecutionPlan(
        "Persisted task", ["Implement", "Verify"], ["tests"],
        ["interruption"], ["implemented", "verified"],
    )


def test_task_store_round_trip(tmp_path: Path):
    store = TaskStore(tmp_path)
    state = TaskState("build feature", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    state.iteration = 4
    state.record("plan_created", "approved")
    board = TaskBoard()
    board.add(Subtask(
        "1", "Implementation", "Implement feature",
        scope=("src",), plan_steps=(1,), status=SubtaskStatus.COMPLETED,
        result="done", evidence=["test passed"], iterations=1,
    ))
    path = store.save(state, plan=make_plan(), board=board, approved=True)

    loaded_state, loaded_plan, loaded_board = store.load("build feature")

    assert path.is_file()
    assert loaded_state.task == state.task
    assert loaded_state.iteration == 4
    assert loaded_plan.summary == "Persisted task"
    assert loaded_board is not None
    assert loaded_board.get("1").status is SubtaskStatus.COMPLETED
    assert loaded_board.get("1").evidence == ["test passed"]


def test_running_subtask_is_reset_to_pending_on_resume(tmp_path: Path):
    store = TaskStore(tmp_path)
    state = TaskState("interrupted", str(tmp_path))
    board = TaskBoard()
    board.add(Subtask(
        "1", "Running", "Work", scope=("src",), plan_steps=(1,),
        status=SubtaskStatus.RUNNING,
    ))
    store.save(state, plan=make_plan(), board=board, approved=True)

    _, _, loaded_board = store.load("interrupted")

    assert loaded_board is not None
    assert loaded_board.get("1").status is SubtaskStatus.PENDING


def test_unapproved_task_cannot_resume(tmp_path: Path):
    store = TaskStore(tmp_path)
    state = TaskState("blocked", str(tmp_path))
    store.save(state, plan=make_plan(), approved=False)

    try:
        store.load("blocked")
    except PersistenceError as exc:
        assert "approved" in str(exc)
    else:
        raise AssertionError("expected PersistenceError")


def test_persisted_task_has_integrity_metadata(tmp_path: Path):
    store = TaskStore(tmp_path)
    state = TaskState("integrity", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    path = store.save(state, plan=make_plan(), approved=True)
    import json
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert len(payload["integrity_sha256"]) == 64


def test_corrupt_current_task_recovers_from_backup(tmp_path: Path):
    store = TaskStore(tmp_path)
    state = TaskState("backup", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    path = store.save(state, plan=make_plan(), approved=True)
    store.save(state, plan=make_plan(), approved=True)
    backup = path.with_suffix(path.suffix + ".bak")
    assert backup.is_file()
    path.write_text("{corrupt", encoding="utf-8")
    loaded, _, _ = store.load("backup")
    assert loaded.task == "backup"
    assert loaded.status is TaskStatus.RUNNING


def test_execution_journal_is_append_only_without_duplicates(tmp_path: Path):
    store = TaskStore(tmp_path)
    state = TaskState("journal", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    store.save(state, plan=make_plan(), approved=True)
    journal_path = store.journal_path_for("journal")
    first_lines = journal_path.read_text(encoding="utf-8").splitlines()

    state.iteration = 1
    state.record("progress", "step completed")
    store.save(state, plan=make_plan(), approved=True)
    second_lines = journal_path.read_text(encoding="utf-8").splitlines()

    assert len(second_lines) == len(first_lines) + 1
    assert second_lines[:len(first_lines)] == first_lines


def test_execution_journal_recovers_truncated_tail(tmp_path: Path):
    store = TaskStore(tmp_path)
    state = TaskState("truncated", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    store.save(state, plan=make_plan(), approved=True)
    journal_path = store.journal_path_for("truncated")
    journal_path.open("ab").write(b'{"interrupted":')
    
    loaded, _, _ = store.load("truncated")
    assert loaded.status is TaskStatus.RUNNING
    assert len(loaded.events) == len(state.events)


def test_execution_journal_rejects_tampered_middle_record(tmp_path: Path):
    store = TaskStore(tmp_path)
    state = TaskState("tampered", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    state.record("one", "first")
    state.record("two", "second")
    store.save(state, plan=make_plan(), approved=True)
    journal_path = store.journal_path_for("tampered")
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace('"message": "second"', '"message": "tampered"')
    journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        store.load("tampered")
    except PersistenceError as exc:
        assert "journal" in str(exc)
    else:
        raise AssertionError("expected journal corruption error")


def test_snapshot_corruption_uses_backup_and_keeps_backup(tmp_path: Path):
    store = TaskStore(tmp_path)
    state = TaskState("backup2", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    path = store.save(state, plan=make_plan(), approved=True)
    state.record("progress", "second")
    store.save(state, plan=make_plan(), approved=True)
    backup = path.with_suffix(path.suffix + ".bak")
    assert backup.is_file()
    backup_bytes = backup.read_bytes()
    path.write_text("{broken", encoding="utf-8")

    loaded, _, _ = store.load("backup2")
    assert loaded.task == "backup2"
    assert backup.read_bytes() == backup_bytes


def test_load_replays_journal_after_snapshot_boundary(tmp_path: Path):
    store = TaskStore(tmp_path)
    state = TaskState("crash-window", str(tmp_path))
    state.transition(TaskStatus.RUNNING)
    store.save(state, plan=make_plan(), approved=True)

    state.iteration = 2
    state.record("progress", "durable journal event")
    TaskJournal(store.journal_path_for("crash-window")).append_events(state)

    loaded, _, _ = store.load("crash-window")
    assert loaded.iteration == 2
    assert loaded.events[-1].event_type == "progress"
    assert loaded.events[-1].sequence == 2
