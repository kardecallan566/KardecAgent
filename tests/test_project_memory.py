from pathlib import Path
from types import SimpleNamespace

import pytest

from kardecagent.agent.project_memory import ProjectMemoryError, ProjectMemoryStore


def test_project_memory_round_trip_and_approved_plan_promotion(tmp_path: Path):
    store = ProjectMemoryStore(tmp_path)
    plan = SimpleNamespace(
        steps=("Create page structure", "Implement responsive design"),
        completion_criteria=("All routes render", "Production build passes"),
    )
    brief = {
        "delivery_requirements": [
            "Use reusable components",
            "Support reduced motion",
        ]
    }

    path = store.record_approved_plan(
        "Create a multi-page animated website",
        brief,
        plan,
    )

    assert path.is_file()
    memory = store.load()
    assert memory["project"]["root"] == str(tmp_path.resolve())
    assert [item["text"] for item in memory["requirements"]] == [
        "Use reusable components",
        "Support reduced motion",
    ]
    assert len(memory["acceptance_criteria"]) == 2
    assert len(memory["milestones"]) == 2
    assert all(item["status"] == "approved" for item in memory["requirements"])


def test_project_memory_deduplicates_approved_requirements(tmp_path: Path):
    store = ProjectMemoryStore(tmp_path)
    plan = SimpleNamespace(steps=("Implement page",), completion_criteria=("Page renders",))
    brief = {"delivery_requirements": ["Use semantic HTML"]}

    store.record_approved_plan("Task A", brief, plan)
    store.record_approved_plan("Task B", brief, plan)

    memory = store.load()
    assert len(memory["requirements"]) == 1
    assert len(memory["acceptance_criteria"]) == 1
    assert len(memory["milestones"]) == 1


def test_project_memory_detects_tampering(tmp_path: Path):
    store = ProjectMemoryStore(tmp_path)
    store.save(store.empty())
    content = store.path.read_text(encoding="utf-8").replace('"requirements": []', '"requirements": ["tampered"]')
    store.path.write_text(content, encoding="utf-8")

    with pytest.raises(ProjectMemoryError, match="checksum"):
        store.load()


def test_project_memory_rejects_workspace_mismatch(tmp_path: Path):
    store = ProjectMemoryStore(tmp_path)
    store.save(store.empty())

    other = ProjectMemoryStore(tmp_path / "other")
    with pytest.raises(ProjectMemoryError, match="different workspace"):
        other.load() if other.path.exists() else _load_copied_memory(store, other)


def _load_copied_memory(source: ProjectMemoryStore, target: ProjectMemoryStore):
    target.directory.mkdir(parents=True)
    target.path.write_bytes(source.path.read_bytes())
    return target.load()


def test_project_memory_is_loaded_into_agent_context(tmp_path: Path):
    from kardecagent.agent.loop import AgentLoop
    from kardecagent.config import Settings

    store = ProjectMemoryStore(tmp_path)
    store.record_approved_plan(
        "Initial approved task",
        {"delivery_requirements": ["Preserve keyboard navigation"]},
        SimpleNamespace(steps=("Implement navigation",), completion_criteria=("Keyboard usable",)),
    )

    context = AgentLoop(object(), Settings())._build_context(
        tmp_path,
        "Improve the existing project",
    )

    assert context["project_memory"]["requirements"][0]["text"] == "Preserve keyboard navigation"
    assert context["project_memory"]["acceptance_criteria"][0]["status"] == "pending"
