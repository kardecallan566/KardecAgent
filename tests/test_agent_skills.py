import json

import pytest

from kardecagent.agent.skills import SkillCatalog, SkillError


def add_skill(root, skill_id, *, dependencies=None, trust="local", enabled=None, body="# Skill\nUse focused tests.\n"):
    folder = root / ".kardecagent" / "skills" / skill_id
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": skill_id, "name": skill_id.replace("-", " ").title(),
        "description": "Test skill", "version": "1.0.0",
        "tags": ["testing"], "dependencies": dependencies or [], "trust": trust,
    }
    if enabled is not None:
        manifest["enabled"] = enabled
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "SKILL.md").write_text(body, encoding="utf-8")
    return folder


def test_discover_lists_metadata_without_loading_instruction_bodies(tmp_path):
    add_skill(tmp_path, "python-tests")
    result = SkillCatalog(tmp_path).list_available()
    assert result[0]["id"] == "python-tests"
    assert result[0]["enabled"] is True


def test_load_is_explicit_and_renders_untrusted_boundary(tmp_path):
    add_skill(tmp_path, "python-tests")
    catalog = SkillCatalog(tmp_path)
    assert catalog.load_prompt_sections([]) == []
    section = catalog.load_prompt_sections(["python-tests"])[0]
    assert "untrusted reference material" in section
    assert "<skill-reference>" in section


def test_external_skill_is_disabled_by_default(tmp_path):
    add_skill(tmp_path, "external-guide", trust="external")
    catalog = SkillCatalog(tmp_path)
    assert catalog.list_available()[0]["enabled"] is False
    with pytest.raises(SkillError, match="disabled"):
        catalog.load("external-guide")


def test_external_skill_cannot_self_enable(tmp_path):
    add_skill(tmp_path, "external-guide", trust="external", enabled=True)
    with pytest.raises(SkillError, match="reviewed and approved"):
        SkillCatalog(tmp_path).discover()


def test_dependency_load_order_is_dependency_first(tmp_path):
    add_skill(tmp_path, "base")
    add_skill(tmp_path, "advanced", dependencies=["base"])
    loaded = SkillCatalog(tmp_path).load("advanced")
    assert [skill.manifest.id for skill in loaded] == ["base", "advanced"]


def test_missing_dependency_is_rejected(tmp_path):
    add_skill(tmp_path, "advanced", dependencies=["missing"])
    with pytest.raises(SkillError, match="missing dependencies"):
        SkillCatalog(tmp_path).discover()


def test_dependency_cycle_is_rejected(tmp_path):
    add_skill(tmp_path, "first", dependencies=["second"])
    add_skill(tmp_path, "second", dependencies=["first"])
    with pytest.raises(SkillError, match="cyclic"):
        SkillCatalog(tmp_path).discover()


def test_invalid_skill_id_and_directory_mismatch_are_rejected(tmp_path):
    folder = add_skill(tmp_path, "Valid")
    with pytest.raises(SkillError, match="lowercase slug"):
        SkillCatalog(tmp_path).discover()


def test_unknown_manifest_fields_are_rejected(tmp_path):
    folder = add_skill(tmp_path, "python-tests")
    data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    data["run"] = "dangerous command"
    (folder / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SkillError, match="unknown skill manifest"):
        SkillCatalog(tmp_path).discover()


def test_oversized_instructions_are_rejected(tmp_path):
    add_skill(tmp_path, "large", body="x" * 70_000)
    with pytest.raises(SkillError, match="exceeds"):
        SkillCatalog(tmp_path).discover()


def test_symlink_instruction_file_is_rejected(tmp_path):
    folder = add_skill(tmp_path, "linked")
    target = tmp_path / "outside.md"
    target.write_text("outside", encoding="utf-8")
    (folder / "SKILL.md").unlink()
    try:
        (folder / "SKILL.md").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(SkillError, match="symbolic link"):
        SkillCatalog(tmp_path).discover()


def test_missing_skill_is_rejected(tmp_path):
    with pytest.raises(SkillError, match="unknown skill"):
        SkillCatalog(tmp_path).load("not-installed")


def test_empty_instructions_are_rejected_when_loaded(tmp_path):
    add_skill(tmp_path, "empty", body="  ")
    with pytest.raises(SkillError, match="empty"):
        SkillCatalog(tmp_path).load("empty")


def test_catalog_limit_is_enforced(tmp_path):
    catalog = SkillCatalog(tmp_path)
    catalog.root.mkdir(parents=True)
    for index in range(catalog.MAX_SKILLS + 1):
        (catalog.root / f"entry-{index}").mkdir()
    with pytest.raises(SkillError, match="limit"):
        catalog.discover()
