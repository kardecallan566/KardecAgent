import json

from kardecagent.agent.skills import SkillCatalog
from kardecagent.agent.skill_orchestrator import SkillOrchestrator


def add_skill(root, skill_id, *, name, tags, description=""):
    folder = root / ".kardecagent" / "skills" / skill_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps({
        "id": skill_id, "name": name, "description": description,
        "tags": tags, "dependencies": [],
    }), encoding="utf-8")
    (folder / "SKILL.md").write_text("Use a focused workflow.", encoding="utf-8")


def test_select_prefers_relevant_skills_and_respects_limit(tmp_path):
    add_skill(tmp_path, "python-tests", name="Python testing", tags=["python", "testing"])
    add_skill(tmp_path, "react-ui", name="React UI", tags=["react", "frontend"])
    add_skill(tmp_path, "rust-build", name="Rust build", tags=["rust", "build"])
    selected = SkillOrchestrator(SkillCatalog(tmp_path), max_skills=2).select(
        "write python tests for the project"
    )
    assert selected.candidate_ids == ("python-tests",)
    assert selected.approval_status == "approval_required"


def test_prepare_never_loads_without_approval(tmp_path):
    add_skill(tmp_path, "python-tests", name="Python testing", tags=["python", "testing"])
    result = SkillOrchestrator(SkillCatalog(tmp_path)).prepare("python testing")
    assert result.loaded_ids == ()
    assert result.approval_status == "approval_required"


def test_prepare_calls_approval_with_dependency_closure(tmp_path):
    add_skill(tmp_path, "base", name="Base workflow", tags=["python"])
    add_skill(tmp_path, "python-tests", name="Python testing", tags=["testing"])
    manifest = tmp_path / ".kardecagent" / "skills" / "python-tests" / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["dependencies"] = ["base"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    approved = []
    result = SkillOrchestrator(SkillCatalog(tmp_path)).prepare(
        "python testing", approval_callback=lambda ids: approved.append(ids) or True
    )
    assert approved == [("base", "python-tests")]
    assert result.loaded_ids == ("base", "python-tests")
    assert len(result.prompt_sections) == 2


def test_rejected_approval_does_not_load_skill(tmp_path):
    add_skill(tmp_path, "python-tests", name="Python testing", tags=["python", "testing"])
    result = SkillOrchestrator(SkillCatalog(tmp_path)).prepare(
        "python testing", approval_callback=lambda _: False
    )
    assert result.approval_status == "rejected"
    assert result.loaded_ids == ()


def test_outcome_is_auditable(tmp_path):
    add_skill(tmp_path, "python-tests", name="Python testing", tags=["python", "testing"])
    result = SkillOrchestrator(SkillCatalog(tmp_path)).prepare(
        "python testing", approval_callback=lambda _: True
    )
    assert SkillOrchestrator.outcome(result, success=True) == {
        "skill_ids": ["python-tests"], "success": True,
        "approval_status": "approved", "evaluation": "verified_success",
    }
