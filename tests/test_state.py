import pytest

from kardecagent.agent.state import TaskState, TaskStatus


def test_event():
    state = TaskState("test", ".")
    state.transition(TaskStatus.RUNNING, reason="start")
    state.record("test", "hello", value=1)
    assert state.events[0].data["value"] == 1


def test_valid_execution_recovery_transition_path():
    state = TaskState("test", ".")
    state.transition(TaskStatus.RUNNING)
    state.transition(TaskStatus.MAX_ITERATIONS)
    state.transition(TaskStatus.RECOVERING)
    state.transition(TaskStatus.RESUMING)
    state.transition(TaskStatus.RUNNING)
    state.transition(TaskStatus.VERIFYING)
    state.transition(TaskStatus.VERIFIED)
    state.transition(TaskStatus.COMPLETED)
    assert state.status is TaskStatus.COMPLETED


def test_invalid_transition_is_rejected():
    state = TaskState("test", ".")
    with pytest.raises(ValueError, match="invalid task status transition"):
        state.transition(TaskStatus.COMPLETED)


def test_completed_is_terminal():
    state = TaskState("test", ".")
    state.transition(TaskStatus.RUNNING)
    state.transition(TaskStatus.VERIFYING)
    state.transition(TaskStatus.VERIFIED)
    state.transition(TaskStatus.COMPLETED)
    assert not state.can_transition(TaskStatus.RUNNING)
    with pytest.raises(ValueError, match="invalid task status transition"):
        state.transition(TaskStatus.RUNNING)


def test_status_transition_is_audited():
    state = TaskState("test", ".")
    state.transition(TaskStatus.RUNNING, reason="started")
    event = state.events[-1]
    assert event.event_type == "status_changed"
    assert event.data["from_status"] == "pending"
    assert event.data["to_status"] == "running"



def test_events_have_monotonic_sequences():
    state = TaskState("test", ".")
    state.transition(TaskStatus.RUNNING)
    state.record("work", "done")
    state.transition(TaskStatus.VERIFYING)
    assert [event.sequence for event in state.events] == [1, 2, 3]
