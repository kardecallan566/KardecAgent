from pathlib import Path

import pytest

from kardecagent.agent.orchestrator import Orchestrator, OrchestrationError, SubtaskExecutionResult
from kardecagent.agent.recovery import RecoveryResult
from kardecagent.agent.state import TaskStatus
from kardecagent.config import Settings
from kardecagent.agent.plan import ExecutionPlan
from kardecagent.tasks import Subtask, SubtaskStatus, TaskBoard


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
def test_subtask_retry_normalizes_max_iterations_state(tmp_path: Path, monkeypatch):
    orchestrator = Orchestrator(FakeLLM("{}"), Settings(max_iterations=1))
    parent_plan = ExecutionPlan("Large task", ["Implement"], ["tests"], [], ["done"])
    board = TaskBoard()
    board.add(Subtask("1", "Implementation", "Implement", scope=("src",), plan_steps=(1,)))
    statuses = []
    limits = []

    def execute(*args, **kwargs):
        state = args[3]
        statuses.append(state.status)
        limits.append(kwargs.get("max_iterations"))
        if len(statuses) == 1:
            state.transition(TaskStatus.MAX_ITERATIONS, reason="Test limit reached.")
            state.record("max_iterations", "Maximum execution iterations reached.")
            return state
        assert state.status is TaskStatus.RESUMING
        state.transition(TaskStatus.RUNNING, reason="Retry started.")
        state.transition(TaskStatus.VERIFYING, reason="Retry verification started.")
        state.transition(TaskStatus.VERIFIED, reason="Retry verification passed.")
        state.transition(TaskStatus.COMPLETED, reason="Retry completed.")
        return state

    monkeypatch.setattr(orchestrator.agent_loop, "execute_approved_plan", execute)
    monkeypatch.setattr(
        orchestrator.recovery,
        "recover",
        lambda *args, **kwargs: RecoveryResult(True, resume_step=1),
    )

    result = orchestrator.execute_approved_plan(
        tmp_path,
        "parent",
        parent_plan,
        board,
    )

    assert statuses == [TaskStatus.RUNNING, TaskStatus.RESUMING]
    assert limits == [1, 2]
    assert result.completed




def test_subtask_does_not_retry_non_transient_command_failure(tmp_path: Path, monkeypatch):
    orchestrator = Orchestrator(FakeLLM("{}"), Settings(max_iterations=2))
    parent_plan = ExecutionPlan("Large task", ["Implement"], ["tests"], [], ["done"])
    board = TaskBoard()
    board.add(Subtask("1", "Implementation", "Implement", scope=("src",), plan_steps=(1,)))
    calls = []

    def execute(*args, **kwargs):
        state = args[3]
        calls.append(state.status)
        state.record(
            "tool_error",
            "run_command failed: command exited with status 1",
            tool="run_command",
            error="command exited with status 1",
        )
        state.transition(TaskStatus.FAILED, reason="Command failed.")
        return state

    monkeypatch.setattr(orchestrator.agent_loop, "execute_approved_plan", execute)
    monkeypatch.setattr(
        orchestrator.recovery,
        "recover",
        lambda *args, **kwargs: RecoveryResult(True, resume_step=1),
    )

    result = orchestrator.execute_approved_plan(tmp_path, "parent", parent_plan, board)

    assert calls == [TaskStatus.RUNNING]
    assert not result.completed
    assert result.get("1").status is SubtaskStatus.FAILED
    assert "external side effects" in result.get("1").result



def test_independent_subtasks_run_in_parallel_with_disjoint_scopes(tmp_path: Path):
    import threading
    import time

    orchestrator = Orchestrator(FakeLLM("{}"), Settings(max_parallel_subtasks=2))
    board = TaskBoard()
    board.add(Subtask("1", "Core", "Core", scope=("src/core",), plan_steps=(1,)))
    board.add(Subtask("2", "Tests", "Tests", scope=("tests",), plan_steps=(2,)))
    active = 0
    maximum = 0
    lock = threading.Lock()

    def execute(subtask):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return SubtaskExecutionResult(subtask.id, "completed", "ok", ["verified"])

    result = orchestrator.execute_sequentially(board, execute)

    assert result.completed
    assert maximum == 2


def test_overlapping_subtask_scopes_are_never_run_in_parallel(tmp_path: Path):
    import threading
    import time

    orchestrator = Orchestrator(FakeLLM("{}"), Settings(max_parallel_subtasks=4))
    board = TaskBoard()
    board.add(Subtask("1", "Parent scope", "Parent", scope=("src",), plan_steps=(1,)))
    board.add(Subtask("2", "Nested scope", "Nested", scope=("src/module",), plan_steps=(2,)))
    active = 0
    maximum = 0
    lock = threading.Lock()

    def execute(subtask):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return SubtaskExecutionResult(subtask.id, "completed", "ok", ["verified"])

    result = orchestrator.execute_sequentially(board, execute)

    assert result.completed
    assert maximum == 1



def test_parallel_resume_uses_each_subtasks_persisted_cursor(tmp_path: Path):
    orchestrator = Orchestrator(FakeLLM("{}"), Settings(max_parallel_subtasks=2))
    board = TaskBoard()
    first = Subtask("1", "Core", "Core", scope=("src/core",), plan_steps=(1, 2, 3))
    second = Subtask("2", "UI", "UI", scope=("src/ui",), plan_steps=(4, 5, 6))
    first.current_step = 2
    second.current_step = 3
    board.add(first)
    board.add(second)
    received_steps = {}

    def execute(subtask, resume_step=1):
        received_steps[subtask.id] = resume_step
        return SubtaskExecutionResult(subtask.id, "completed", "ok", ["evidence"])

    result = orchestrator.execute_sequentially(board, execute)

    assert result.completed
    assert received_steps == {"1": 2, "2": 3}



def test_adaptive_parallelism_limit_caps_independent_subtasks(tmp_path: Path):
    import threading
    import time

    orchestrator = Orchestrator(FakeLLM("{}"), Settings(max_parallel_subtasks=4))
    board = TaskBoard()
    board.add(Subtask("1", "Core", "Core", scope=("src/core",), plan_steps=(1,)))
    board.add(Subtask("2", "Tests", "Tests", scope=("tests",), plan_steps=(2,)))
    active = 0
    maximum = 0
    lock = threading.Lock()

    def execute(subtask):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return SubtaskExecutionResult(subtask.id, "completed", "ok", ["verified"])

    result = orchestrator.execute_sequentially(board, execute, parallelism_limit=1)
    assert result.completed
    assert maximum == 1


def test_adaptive_assessment_can_trigger_decomposition(tmp_path: Path):
    from kardecagent.agent.adaptive_intelligence import AdaptiveIntelligenceEngine

    orchestrator = Orchestrator(FakeLLM("{}"), Settings())
    plan = make_plan(steps=4)
    assessment = AdaptiveIntelligenceEngine().assess(
        "Implement secure backend architecture and database migration",
        plan_steps=plan.steps,
        project_file_count=0,
    )
    assert assessment.recommend_decomposition
    assert orchestrator.should_decompose(plan, tmp_path, assessment=assessment)


def test_subtask_skills_are_selected_and_loaded_only_after_explicit_approval(tmp_path: Path, monkeypatch):
    import json
    from kardecagent.agent.skills import SkillCatalog
    from kardecagent.agent.state import TaskState, TaskStatus

    package = tmp_path / ".kardecagent" / "skills" / "python-tests"
    package.mkdir(parents=True)
    (package / "manifest.json").write_text(json.dumps({
        "id": "python-tests",
        "name": "Python Testing",
        "description": "Create and validate Python tests with pytest",
        "tags": ["python", "pytest", "testing"],
        "dependencies": [],
    }), encoding="utf-8")
    (package / "SKILL.md").write_text("Prefer focused pytest tests.", encoding="utf-8")

    orchestrator = Orchestrator(FakeLLM("{}"), Settings())
    board = TaskBoard()
    board.add(Subtask(
        "tests", "Write Python tests", "Implement Python tests using pytest",
        scope=("tests",), plan_steps=(1,),
    ))
    approvals = []
    observed_contexts = []

    def approve(skill_ids):
        approvals.append(skill_ids)
        return True

    def execute(root, task, plan, state, **kwargs):
        observed_contexts.append(kwargs["context"]["skill_prompt_sections"])
        state.transition(TaskStatus.VERIFYING, reason="Fixture verification.")
        state.transition(TaskStatus.VERIFIED, reason="Fixture verified.")
        state.transition(TaskStatus.COMPLETED, reason="Fixture completed.")
        return state

    monkeypatch.setattr(orchestrator.agent_loop, "execute_approved_plan", execute)
    result = orchestrator.execute_approved_plan(
        tmp_path, "Implement Python tests", make_plan(steps=1), board,
        skill_catalog=SkillCatalog(tmp_path),
        skill_approval_callback=approve,
    )

    assert result.completed
    assert approvals == [("python-tests",)]
    assert len(observed_contexts) == 1
    assert "REFERENCE SKILL: Python Testing" in observed_contexts[0][0]


def test_subtask_skill_refusal_keeps_skill_instructions_unloaded(tmp_path: Path, monkeypatch):
    import json
    from kardecagent.agent.skills import SkillCatalog
    from kardecagent.agent.state import TaskStatus

    package = tmp_path / ".kardecagent" / "skills" / "python-tests"
    package.mkdir(parents=True)
    (package / "manifest.json").write_text(json.dumps({
        "id": "python-tests", "name": "Python Testing",
        "description": "Python pytest testing", "tags": ["python", "pytest"],
        "dependencies": [],
    }), encoding="utf-8")
    (package / "SKILL.md").write_text("Sensitive instructions should not load.", encoding="utf-8")

    orchestrator = Orchestrator(FakeLLM("{}"), Settings())
    board = TaskBoard()
    board.add(Subtask(
        "tests", "Write Python tests", "Implement Python tests using pytest",
        scope=("tests",), plan_steps=(1,),
    ))
    observed = []

    def execute(root, task, plan, state, **kwargs):
        observed.extend(kwargs["context"]["skill_prompt_sections"])
        state.transition(TaskStatus.VERIFYING, reason="Fixture verification.")
        state.transition(TaskStatus.VERIFIED, reason="Fixture verified.")
        state.transition(TaskStatus.COMPLETED, reason="Fixture completed.")
        return state

    monkeypatch.setattr(orchestrator.agent_loop, "execute_approved_plan", execute)
    result = orchestrator.execute_approved_plan(
        tmp_path, "Implement Python tests", make_plan(steps=1), board,
        skill_catalog=SkillCatalog(tmp_path),
        skill_approval_callback=lambda skill_ids: False,
    )

    assert result.completed
    assert observed == []
