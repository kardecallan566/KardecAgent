from kardecagent.agent.state import TaskState, TaskStatus
def test_event():
    state = TaskState('test', '.')
    state.status = TaskStatus.RUNNING
    state.record('test', 'hello', value=1)
    assert state.events[0].data['value'] == 1