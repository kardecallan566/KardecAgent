import base64
import json

import httpx

from kardecagent.agent.persistence import TaskStore
from kardecagent.agent.plan import ExecutionPlan
from kardecagent.agent.skill_learning import SkillLearningEngine
from kardecagent.agent.skill_marketplace import preview_github_skill
from kardecagent.agent.skill_orchestrator import SkillOrchestrator
from kardecagent.agent.skills import SkillCatalog
from kardecagent.agent.state import TaskState, TaskStatus
from kardecagent.studio_protocol import _skill_approval_valid, _skill_command


def command(root, approvals, kind, **payload):
    return _skill_command(root, {"type": kind, "payload": payload}, approvals)


def save_outcome(root, task_name, preparation, success):
    state = TaskState(task=task_name, project_root=str(root.resolve()))
    state.status = TaskStatus.COMPLETED
    state.record(
        "skill_outcome", "Measured skill outcome.",
        **SkillOrchestrator.outcome(preparation, success=success),
    )
    TaskStore(root).save(
        state,
        plan=ExecutionPlan(
            summary="E2E task",
            steps=["Inspect", "Implement"],
            validation=["Run checks"],
            completion_criteria=["Result verified"],
        ),
        approved=True,
    )


def test_end_to_end_skill_activation_outcomes_and_learning(tmp_path):
    approvals = {}
    command(
        tmp_path, approvals, "skills.save",
        id="python-testing", name="Python testing",
        description="Focused Python tests", tags=["python", "testing"],
        dependencies=[], instructions="# Python testing\nRun focused tests and verify.",
    )
    command(tmp_path, approvals, "skills.activation.set", id="python-testing", enabled=True)

    catalog = SkillCatalog(tmp_path)
    orchestrator = SkillOrchestrator(catalog)
    callback = lambda ids: _skill_approval_valid(tmp_path, approvals, list(ids))
    preparation = orchestrator.prepare("write python tests", approval_callback=callback)
    assert preparation.loaded_ids == ("python-testing",)

    save_outcome(tmp_path, "task one", preparation, False)
    save_outcome(tmp_path, "task two", preparation, False)
    save_outcome(tmp_path, "task three", preparation, True)
    report = SkillLearningEngine(tmp_path).analyze()
    metric = report["metrics"][0]
    assert (metric["attempts"], metric["verified_successes"], metric["not_verified"]) == (3, 1, 2)
    proposal = next(item for item in report["proposals"] if item["kind"] == "improve_skill")
    assert proposal["skill_id"] == "python-testing"

    command(
        tmp_path, approvals, "skills.save",
        id="python-testing", name="Python testing",
        description="Focused Python tests", tags=["python", "testing"],
        dependencies=[], instructions="# Python testing\nChanged instructions require review.",
    )
    assert not _skill_approval_valid(tmp_path, approvals, ["python-testing"])
    denied = orchestrator.prepare("write python tests", approval_callback=callback)
    assert denied.loaded_ids == ()
    assert denied.approval_status == "rejected"


def test_end_to_end_github_preview_install_then_separate_activation(tmp_path, monkeypatch):
    manifest = {
        "id": "python-testing", "name": "Python testing",
        "description": "Testing guidance", "tags": ["python", "testing"],
        "dependencies": [],
    }
    files = {
        "manifest.json": json.dumps(manifest).encode("utf-8"),
        "SKILL.md": b"# Python testing\nReview and verify tests.",
    }

    def github_get(url, **kwargs):
        filename = "manifest.json" if "manifest.json?" in url else "SKILL.md"
        return httpx.Response(
            200,
            json={
                "type": "file", "encoding": "base64",
                "content": base64.b64encode(files[filename]).decode("ascii"),
            },
            request=httpx.Request("GET", "https://api.github.com/test"),
        )

    monkeypatch.setattr("kardecagent.agent.skill_marketplace.httpx.get", github_get)
    source = "https://github.com/acme/skills/tree/main/python-testing"
    preview = preview_github_skill(tmp_path, source)
    approvals = {}
    installed = command(
        tmp_path, approvals, "skills.import.install",
        source_url=source, fingerprint=preview.fingerprint,
        approved=True, replace_existing=False,
    )
    assert installed["installed_id"] == "python-testing"
    assert approvals == {}

    command(tmp_path, approvals, "skills.activation.set", id="python-testing", enabled=True)
    prepared = SkillOrchestrator(SkillCatalog(tmp_path)).prepare(
        "write python tests",
        approval_callback=lambda ids: _skill_approval_valid(tmp_path, approvals, list(ids)),
    )
    assert prepared.loaded_ids == ("python-testing",)


def test_skill_selection_is_deterministic_and_bounded_for_large_catalog(tmp_path):
    skills_root = tmp_path / ".kardecagent" / "skills"
    for index in range(60):
        skill_id = f"python-testing-{index:02d}"
        folder = skills_root / skill_id
        folder.mkdir(parents=True)
        manifest = {
            "id": skill_id,
            "name": f"Python testing workflow {index}",
            "description": "Python testing and database migration",
            "tags": ["python", "testing", "database"],
            "dependencies": [],
        }
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "SKILL.md").write_text("Use a focused workflow.", encoding="utf-8")

    catalog = SkillCatalog(tmp_path)
    first = SkillOrchestrator(catalog, max_skills=3).select("python testing database")
    second = SkillOrchestrator(catalog, max_skills=3).select("python testing database")
    assert len(catalog.discover()) == 60
    assert first.candidate_ids == second.candidate_ids
    assert len(first.candidate_ids) <= 3


def test_learning_report_does_not_write_skill_files(tmp_path):
    folder = tmp_path / ".kardecagent" / "skills" / "python-testing"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        '{"id":"python-testing","name":"Python testing","tags":["python"],"dependencies":[]}',
        encoding="utf-8",
    )
    skill_file = folder / "SKILL.md"
    skill_file.write_text("# Python testing\nExisting content.", encoding="utf-8")
    before = skill_file.read_bytes()
    report = SkillLearningEngine(tmp_path).analyze()
    assert report["auto_changes"] is False
    assert skill_file.read_bytes() == before
