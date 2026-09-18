from pathlib import Path

import pytest

from kardecagent.agent.orchestrator import Orchestrator, OrchestrationError, SubtaskExecutionResult
from kardecagent.config import Settings
from kardecagent.agent.plan import ExecutionPlan
from kardecagent.tasks import SubtaskStatus


class FakeLLM:
    def __init__(self, response: str):
        self.response = response

    def chat(self, messages):
        from kardecagent.llm.client import LLMResponse
        return LLMResponse(self.response, {})


def make_plan(steps=4):
    return ExecutionPlan(
        "Large task",
        [f"step {i}" for i in range(1, steps + 1)],
        ["tests"],
        [],
        ["all done"],
    )


def test_decompose_stays_inside_approved_plan(tmp_path: Path):
    response = '{"subtasks":['         '{"id":"1","title":"Core","objective":"Core","scope":["src"],"dependencies":[],"completion_criteria":["core"],"plan_steps":[1,2]},'         '{"id":"2","title":"Tests","objective":"Tests","scope":["tests"],"dependencies":["1"],"completion_criteria":["tests"],"plan_steps":[3,4]}'         ']}'
    orchestrator = Orchestrator(FakeLLM(response), Settings())
    board = orchestrator.decompose(make_plan(), tmp_path)
    assert list(board.subtasks) == ["1", "2"]
    assert board.get("2").dependencies == ("1",)


def test_decompose_rejects_unapproved_plan_step(tmp_path: Path):
    response = '{"subtasks":['         '{"id":"1","title":"Core","objective":"Core","dependencies":[],"completion_criteria":["core"],"plan_steps":[99]},'         '{"id":"2","title":"Tests","objective":"Tests","dependencies":[],"completion_criteria":["tests"],"plan_steps":[1]}'         ']}'
    orchestrator = Orchestrator(FakeLLM(response), Settings())
    with pytest.raises(OrchestrationError, match="approved plan_steps"):
        orchestrator.decompose(make_plan(), tmp_path)


def test_sequential_execution_obeys_dependencies(tmp_path: Path):
    response = '{"subtasks":['         '{"id":"1","title":"Core","objective":"Core","scope":["src"],"dependencies":[],"completion_criteria":["core"],"plan_steps":[1,2]},'         '{"id":"2","title":"Tests","objective":"Tests","scope":["tests"],"dependencies":["1"],"completion_criteria":["tests"],"plan_steps":[3,4]}'         ']}'
    orchestrator = Orchestrator(FakeLLM(response), Settings())
    board = orchestrator.decompose(make_plan(), tmp_path)
    order = []

    def execute(subtask):
        order.append(subtask.id)
        return SubtaskExecutionResult(subtask.id, "completed", "ok", ["verified"])

    result = orchestrator.execute_sequentially(board, execute)
    assert order == ["1", "2"]
    assert result.completed
    assert all(t.status is SubtaskStatus.COMPLETED for t in result.subtasks.values())


def test_failed_subtask_blocks_dependents(tmp_path: Path):
    response = '{"subtasks":['         '{"id":"1","title":"Core","objective":"Core","dependencies":[],"completion_criteria":["core"],"plan_steps":[1,2]},'         '{"id":"2","title":"Tests","objective":"Tests","dependencies":["1"],"completion_criteria":["tests"],"plan_steps":[3,4]}'         ']}'
    orchestrator = Orchestrator(FakeLLM(response), Settings())
    board = orchestrator.decompose(make_plan(), tmp_path)

    def execute(subtask):
        return SubtaskExecutionResult(subtask.id, "failed", "broken", [])

    result = orchestrator.execute_sequentially(board, execute)
    assert result.get("1").status is SubtaskStatus.FAILED
    assert result.get("2").status is SubtaskStatus.BLOCKED


def test_sequential_resume_prioritizes_active_subtask_and_step(tmp_path: Path):
    orchestrator = Orchestrator(FakeLLM("{}"), Settings())
    from kardecagent.tasks import Subtask, TaskBoard
    board = TaskBoard()
    board.add(Subtask("1", "First", "First", scope=("src",), plan_steps=(1,)))
    board.add(Subtask("2", "Second", "Second", scope=("tests",), plan_steps=(2,)))
    calls = []

    def execute(subtask, resume_step=1):
        calls.append((subtask.id, resume_step))
        return SubtaskExecutionResult(subtask.id, "completed", "ok", ["verified"], resume_step)

    result = orchestrator.execute_sequentially(
        board,
        execute,
        resume_subtask_id="2",
        resume_step=2,
    )
    assert result.completed
    assert calls[0] == ("2", 2)
