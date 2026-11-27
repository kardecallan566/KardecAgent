from kardecagent.agent.plan import ExecutionPlan
from kardecagent.agent.persistence import TaskStore
from kardecagent.agent.skill_learning import SkillLearningEngine
from kardecagent.agent.state import TaskState, TaskStatus


def add_skill(root, skill_id="python-tests"):
    folder = root / ".kardecagent" / "skills" / skill_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(
        '{"id":"python-tests","name":"Python tests","description":"Testing","tags":["python"],"dependencies":[]}',
        encoding="utf-8",
    )
    (folder / "SKILL.md").write_text("# Python tests\nRun focused tests.", encoding="utf-8")


def save_task(root, name, *, skill_ids=None, success=None, unmatched=None):
    state = TaskState(task=name, project_root=str(root.resolve()))
    state.status = TaskStatus.COMPLETED
    if skill_ids is not None:
        state.record(
            "skill_outcome", "Skill use evaluated.",
            skill_ids=skill_ids, success=success,
            approval_status="approved",
            evaluation="verified_success" if success else "not_verified",
        )
    if unmatched is not None:
        state.record(
            "skills_unmatched", "No relevant skill was found.",
            task_fingerprint=unmatched,
            task_terms=["database", "migration", "schema"],
        )
    plan = ExecutionPlan(
        summary="Test task", steps=["Inspect"], validation=["Verify"],
    )
    TaskStore(root).save(state, plan=plan, approved=True)


def test_skill_metrics_use_only_persisted_outcome_events(tmp_path):
    add_skill(tmp_path)
    save_task(tmp_path, "Task one", skill_ids=["python-tests"], success=True)
    save_task(tmp_path, "Task two", skill_ids=["python-tests"], success=False)
    report = SkillLearningEngine(tmp_path).analyze()
    assert report["tasks_analyzed"] == 2
    assert report["metrics"] == [{
        "skill_id": "python-tests",
        "attempts": 2,
        "verified_successes": 1,
        "not_verified": 1,
        "verified_success_rate": 50.0,
        "last_used_at": report["metrics"][0]["last_used_at"],
    }]
    assert report["auto_changes"] is False


def test_repeated_unverified_skill_usage_creates_review_draft_not_file_changes(tmp_path):
    add_skill(tmp_path)
    original = (tmp_path / ".kardecagent" / "skills" / "python-tests" / "SKILL.md").read_text(encoding="utf-8")
    save_task(tmp_path, "Task one", skill_ids=["python-tests"], success=False)
    save_task(tmp_path, "Task two", skill_ids=["python-tests"], success=False)
    save_task(tmp_path, "Task three", skill_ids=["python-tests"], success=True)
    report = SkillLearningEngine(tmp_path).analyze()
    proposal = next(item for item in report["proposals"] if item["kind"] == "improve_skill")
    assert proposal["attempts"] == 3
    assert proposal["not_verified"] == 2
    assert "Revisão sugerida" in proposal["draft_instructions"]
    assert (tmp_path / ".kardecagent" / "skills" / "python-tests" / "SKILL.md").read_text(encoding="utf-8") == original


def test_repeated_unmatched_tasks_create_new_skill_draft(tmp_path):
    save_task(tmp_path, "Task one", unmatched="same-pattern")
    save_task(tmp_path, "Task two", unmatched="same-pattern")
    save_task(tmp_path, "Task three", unmatched="same-pattern")
    report = SkillLearningEngine(tmp_path).analyze()
    proposal = next(item for item in report["proposals"] if item["kind"] == "create_skill")
    assert proposal["draft_manifest"]["id"] == "proposed-database-migration-schema"
    assert "## Fluxo recomendado" in proposal["draft_instructions"]
    assert not (tmp_path / ".kardecagent" / "skills" / proposal["skill_id"]).exists()


def test_learning_engine_requires_minimum_sample_count(tmp_path):
    add_skill(tmp_path)
    save_task(tmp_path, "Task one", skill_ids=["python-tests"], success=False)
    save_task(tmp_path, "Task two", skill_ids=["python-tests"], success=False)
    report = SkillLearningEngine(tmp_path).analyze()
    assert report["proposals"] == []


def test_learning_engine_caps_analysis_window(tmp_path):
    engine = SkillLearningEngine(tmp_path, task_limit=100)
    assert engine.task_limit == 100
