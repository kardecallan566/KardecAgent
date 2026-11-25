from pathlib import Path

import pytest

from kardecagent.tasks import Subtask, SubtaskManager, SubtaskStatus, TaskBoardError


def test_decomposition_validates_dependencies(tmp_path: Path):
    manager = SubtaskManager(tmp_path)
    board = manager.parse_decomposition({
        "subtasks": [
            {"id": "1", "title": "Core", "objective": "Implement core", "scope": ["src/core.py"]},
            {"id": "2", "title": "Tests", "objective": "Add tests", "scope": ["tests"], "dependencies": ["1"]},
        ]
    })
    assert board.ready()[0].id == "1"
    board.mark_running("1")
    board.complete("1", result="ok", evidence=["implementation exists"])
    assert board.ready()[0].id == "2"


def test_decomposition_rejects_unknown_dependency(tmp_path: Path):
    manager = SubtaskManager(tmp_path)
    with pytest.raises(ValueError, match="unknown dependency"):
        manager.parse_decomposition({
            "subtasks": [
                {"id": "1", "title": "Core", "objective": "Implement core", "dependencies": ["missing"]},
                {"id": "2", "title": "Tests", "objective": "Add tests"},
            ]
        })


def test_decomposition_rejects_cycles(tmp_path: Path):
    manager = SubtaskManager(tmp_path)
    with pytest.raises(ValueError, match="cycle"):
        manager.parse_decomposition({
            "subtasks": [
                {"id": "1", "title": "A", "objective": "A", "dependencies": ["2"]},
                {"id": "2", "title": "B", "objective": "B", "dependencies": ["1"]},
            ]
        })


def test_scope_cannot_escape_project(tmp_path: Path):
    manager = SubtaskManager(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        manager.parse_decomposition({
            "subtasks": [
                {"id": "1", "title": "A", "objective": "A", "scope": ["../secret"]},
                {"id": "2", "title": "B", "objective": "B"},
            ]
        })


def test_limits_and_blocking(tmp_path: Path):
    manager = SubtaskManager(tmp_path, max_subtasks=2)
    with pytest.raises(ValueError, match="between"):
        manager.parse_decomposition({
            "subtasks": [
                {"id": "1", "title": "A", "objective": "A"},
                {"id": "2", "title": "B", "objective": "B"},
                {"id": "3", "title": "C", "objective": "C"},
            ]
        })
    board = manager.parse_decomposition({
        "subtasks": [
            {"id": "1", "title": "A", "objective": "A"},
            {"id": "2", "title": "B", "objective": "B", "dependencies": ["1"]},
        ]
    })
    board.mark_running("1")
    board.fail("1", result="failed")
    manager.mark_blocked(board)
    assert board.get("2").status is SubtaskStatus.BLOCKED


def test_task_board_rejects_invalid_state_transition(tmp_path: Path):
    manager = SubtaskManager(tmp_path)
    board = manager.parse_decomposition({
        "subtasks": [
            {"id": "1", "title": "A", "objective": "A"},
            {"id": "2", "title": "B", "objective": "B"},
        ]
    })
    with pytest.raises(TaskBoardError):
        board.complete("1", result="bad", evidence=[])


def test_decomposition_validates_and_preserves_specialist_role(tmp_path: Path):
    manager = SubtaskManager(tmp_path)
    board = manager.parse_decomposition({
        "subtasks": [
            {"id": "1", "title": "Design", "objective": "Design architecture", "role": "architect"},
            {"id": "2", "title": "Tests", "objective": "Validate behavior", "role": "tester"},
        ]
    })
    assert board.get("1").role == "architect"
    assert board.get("2").as_dict()["role"] == "tester"


def test_decomposition_rejects_unknown_specialist_role(tmp_path: Path):
    manager = SubtaskManager(tmp_path)
    with pytest.raises(ValueError, match="role must be"):
        manager.parse_decomposition({
            "subtasks": [
                {"id": "1", "title": "A", "objective": "A", "role": "magic"},
                {"id": "2", "title": "B", "objective": "B"},
            ]
        })


def test_short_plans_are_not_decomposed_for_single_file_edits(tmp_path):
    from kardecagent.tasks.manager import SubtaskManager

    manager = SubtaskManager(tmp_path)
    assert manager.should_decompose(["Inspect file", "Append requested line", "Verify file"], 500) is False
    assert manager.should_decompose(["A", "B", "C", "D"], 500) is False
    assert manager.should_decompose(["A", "B", "C", "D", "E"], 79) is False
    assert manager.should_decompose(["A", "B", "C", "D", "E"], 80) is True


def test_short_plans_do_not_trigger_subtask_decomposition(tmp_path):
    from kardecagent.tasks.manager import SubtaskManager

    manager = SubtaskManager(tmp_path)
    assert manager.should_decompose(["Inspect target", "Append line", "Verify"], 500) is False
    assert manager.should_decompose(["A", "B", "C", "D"], 500) is False
    assert manager.should_decompose(["A", "B", "C", "D", "E"], 79) is False
    assert manager.should_decompose(["A", "B", "C", "D", "E"], 80) is True
