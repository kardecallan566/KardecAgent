import pytest

from kardecagent.studio_protocol import _approved_skill_ids, _skill_command


def payload(command, **values):
    return {"type": command, "payload": values}


def test_skill_catalog_list_and_save(tmp_path):
    result = _skill_command(tmp_path, payload(
        "skills.save", id="python-tests", name="Python Tests",
        description="Test guidance", tags=["python", "pytest"],
        dependencies=[], instructions="Run focused tests.",
    ))
    assert result["saved_id"] == "python-tests"
    assert result["skills"][0]["name"] == "Python Tests"
    assert result["approved_ids"] == []


def test_skill_body_is_loaded_only_when_requested(tmp_path):
    _skill_command(tmp_path, payload(
        "skills.save", id="python-tests", name="Python Tests",
        tags=["python"], dependencies=[], instructions="Detailed private workflow.",
    ))
    result = _skill_command(tmp_path, payload("skills.body", id="python-tests"))
    assert result["instructions"] == "Detailed private workflow."


def test_skill_activation_persists_explicit_approval_and_revocation(tmp_path):
    _skill_command(tmp_path, payload(
        "skills.save", id="base", name="Base", tags=["base"],
        dependencies=[], instructions="Base guidance.",
    ))
    _skill_command(tmp_path, payload(
        "skills.save", id="python-tests", name="Python Tests", tags=["python"],
        dependencies=["base"], instructions="Test guidance.",
    ))
    result = _skill_command(tmp_path, payload("skills.activation.set", id="python-tests", enabled=True))
    assert result["approved_ids"] == ["base", "python-tests"]
    assert _approved_skill_ids(tmp_path) == {"base", "python-tests"}
    result = _skill_command(tmp_path, payload("skills.activation.set", id="python-tests", enabled=False))
    assert result["approved_ids"] == []
    assert _approved_skill_ids(tmp_path) == set()


def test_skill_save_rejects_missing_dependencies_without_partial_package(tmp_path):
    with pytest.raises(ValueError, match="Dependências não encontradas"):
        _skill_command(tmp_path, payload(
            "skills.save", id="broken", name="Broken", tags=[],
            dependencies=["missing"], instructions="No.",
        ))
    assert not (tmp_path / ".kardecagent" / "skills" / "broken").exists()


def test_skill_delete_refuses_to_remove_dependency(tmp_path):
    _skill_command(tmp_path, payload(
        "skills.save", id="base", name="Base", tags=[], dependencies=[], instructions="Base.",
    ))
    _skill_command(tmp_path, payload(
        "skills.save", id="child", name="Child", tags=[], dependencies=["base"], instructions="Child.",
    ))
    with pytest.raises(ValueError, match="outras Skills dependem"):
        _skill_command(tmp_path, payload("skills.delete", id="base"))
    result = _skill_command(tmp_path, payload("skills.delete", id="child"))
    assert [item["id"] for item in result["skills"]] == ["base"]


def test_skill_activation_rejects_unknown_skill(tmp_path):
    with pytest.raises(ValueError, match="unknown skill"):
        _skill_command(tmp_path, payload("skills.activation.set", id="unknown", enabled=True))

def test_skill_save_rejects_cyclic_dependency_before_writing(tmp_path):
    _skill_command(tmp_path, payload(
        "skills.save", id="first", name="First", tags=[], dependencies=[],
        instructions="First guidance.",
    ))
    _skill_command(tmp_path, payload(
        "skills.save", id="second", name="Second", tags=[], dependencies=["first"],
        instructions="Second guidance.",
    ))
    with pytest.raises(ValueError, match="cyclic"):
        _skill_command(tmp_path, payload(
            "skills.save", id="first", name="First", tags=[], dependencies=["second"],
            instructions="Changed guidance.",
        ))
    assert _skill_command(tmp_path, payload("skills.body", id="first"))["instructions"] == "First guidance."


def test_skill_delete_removes_approval_record(tmp_path):
    _skill_command(tmp_path, payload(
        "skills.save", id="temporary", name="Temporary", tags=[], dependencies=[],
        instructions="Temporary guidance.",
    ))
    _skill_command(tmp_path, payload("skills.activation.set", id="temporary", enabled=True))
    result = _skill_command(tmp_path, payload("skills.delete", id="temporary"))
    assert result["approved_ids"] == []
    assert result["skills"] == []

def test_skill_catalog_refuses_symlinked_metadata_root(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "skills").mkdir()
    try:
        (tmp_path / ".kardecagent").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlinks are unavailable on this platform.")
    with pytest.raises(ValueError, match="link simbólico"):
        _skill_command(tmp_path, payload("skills.list"))
