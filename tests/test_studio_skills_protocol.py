import pytest

from kardecagent.studio_protocol import _skill_command


@pytest.fixture
def approved():
    return {}


def payload(command, **values):
    return {"type": command, "payload": values}


def test_skill_catalog_list_and_save(tmp_path, approved):
    result = _skill_command(tmp_path, payload(
        "skills.save", id="python-tests", name="Python Tests",
        description="Test guidance", tags=["python", "pytest"],
        dependencies=[], instructions="Run focused tests.",
    ), approved)
    assert result["saved_id"] == "python-tests"
    assert result["skills"][0]["name"] == "Python Tests"
    assert result["approved_ids"] == []


def test_skill_body_is_loaded_only_when_requested(tmp_path, approved):
    _skill_command(tmp_path, payload(
        "skills.save", id="python-tests", name="Python Tests",
        tags=["python"], dependencies=[], instructions="Detailed private workflow.",
    ), approved)
    result = _skill_command(tmp_path, payload("skills.body", id="python-tests"), approved)
    assert result["instructions"] == "Detailed private workflow."


def test_skill_activation_is_explicit_and_session_scoped(tmp_path, approved):
    _skill_command(tmp_path, payload(
        "skills.save", id="base", name="Base", tags=["base"],
        dependencies=[], instructions="Base guidance.",
    ), approved)
    _skill_command(tmp_path, payload(
        "skills.save", id="python-tests", name="Python Tests", tags=["python"],
        dependencies=["base"], instructions="Test guidance.",
    ), approved)
    result = _skill_command(tmp_path, payload("skills.activation.set", id="python-tests", enabled=True), approved)
    assert result["approved_ids"] == ["base", "python-tests"]
    assert set(approved) == {"base", "python-tests"}
    result = _skill_command(tmp_path, payload("skills.activation.set", id="python-tests", enabled=False), approved)
    assert result["approved_ids"] == []
    assert approved == {}


def test_skill_save_rejects_missing_dependencies_without_partial_package(tmp_path, approved):
    with pytest.raises(ValueError, match="Dependências não encontradas"):
        _skill_command(tmp_path, payload(
            "skills.save", id="broken", name="Broken", tags=[],
            dependencies=["missing"], instructions="No.",
        ), approved)
    assert not (tmp_path / ".kardecagent" / "skills" / "broken").exists()


def test_skill_delete_refuses_to_remove_dependency(tmp_path, approved):
    _skill_command(tmp_path, payload(
        "skills.save", id="base", name="Base", tags=[], dependencies=[], instructions="Base.",
    ), approved)
    _skill_command(tmp_path, payload(
        "skills.save", id="child", name="Child", tags=[], dependencies=["base"], instructions="Child.",
    ), approved)
    with pytest.raises(ValueError, match="outras Skills dependem"):
        _skill_command(tmp_path, payload("skills.delete", id="base"), approved)
    result = _skill_command(tmp_path, payload("skills.delete", id="child"), approved)
    assert [item["id"] for item in result["skills"]] == ["base"]


def test_skill_activation_rejects_unknown_skill(tmp_path, approved):
    with pytest.raises(ValueError, match="unknown skill"):
        _skill_command(tmp_path, payload("skills.activation.set", id="unknown", enabled=True), approved)


def test_skill_save_rejects_cyclic_dependency_before_writing(tmp_path, approved):
    _skill_command(tmp_path, payload(
        "skills.save", id="first", name="First", tags=[], dependencies=[],
        instructions="First guidance.",
    ), approved)
    _skill_command(tmp_path, payload(
        "skills.save", id="second", name="Second", tags=[], dependencies=["first"],
        instructions="Second guidance.",
    ), approved)
    with pytest.raises(ValueError, match="cyclic"):
        _skill_command(tmp_path, payload(
            "skills.save", id="first", name="First", tags=[], dependencies=["second"],
            instructions="Changed guidance.",
        ), approved)
    assert _skill_command(tmp_path, payload("skills.body", id="first"), approved)["instructions"] == "First guidance."


def test_skill_delete_revokes_session_approval(tmp_path, approved):
    _skill_command(tmp_path, payload(
        "skills.save", id="temporary", name="Temporary", tags=[], dependencies=[],
        instructions="Temporary guidance.",
    ), approved)
    _skill_command(tmp_path, payload("skills.activation.set", id="temporary", enabled=True), approved)
    result = _skill_command(tmp_path, payload("skills.delete", id="temporary"), approved)
    assert result["approved_ids"] == []
    assert result["skills"] == []


def test_skill_catalog_refuses_symlinked_metadata_root(tmp_path, approved):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "skills").mkdir()
    try:
        (tmp_path / ".kardecagent").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlinks are unavailable on this platform.")
    with pytest.raises(ValueError, match="link simbólico"):
        _skill_command(tmp_path, payload("skills.list"), approved)

def test_editing_skill_invalidates_its_previous_approval(tmp_path, approved):
    _skill_command(tmp_path, payload(
        "skills.save", id="python", name="Python", tags=["python"],
        dependencies=[], instructions="Original guidance.",
    ), approved)
    _skill_command(tmp_path, payload("skills.activation.set", id="python", enabled=True), approved)
    assert "python" in approved
    result = _skill_command(tmp_path, payload(
        "skills.save", id="python", name="Python", tags=["python"],
        dependencies=[], instructions="Changed guidance.",
    ), approved)
    assert "python" not in result["approved_ids"]
    assert "python" not in approved


def test_editing_dependency_invalidates_dependent_skill_consent(tmp_path, approved):
    _skill_command(tmp_path, payload(
        "skills.save", id="base", name="Base", tags=[],
        dependencies=[], instructions="Base v1.",
    ), approved)
    _skill_command(tmp_path, payload(
        "skills.save", id="child", name="Child", tags=[],
        dependencies=["base"], instructions="Child guidance.",
    ), approved)
    _skill_command(tmp_path, payload("skills.activation.set", id="child", enabled=True), approved)
    assert set(_skill_command(tmp_path, payload("skills.list"), approved)["approved_ids"]) == {"base", "child"}
    _skill_command(tmp_path, payload(
        "skills.save", id="base", name="Base", tags=[],
        dependencies=[], instructions="Base v2.",
    ), approved)
    assert _skill_command(tmp_path, payload("skills.list"), approved)["approved_ids"] == []

def test_skill_save_rejects_control_characters(tmp_path, approved):
    with pytest.raises(ValueError, match="caracteres de controle"):
        _skill_command(tmp_path, payload(
            "skills.save", id="unsafe", name="Unsafe", tags=[], dependencies=[],
            instructions="safe\x01unsafe",
        ), approved)

def test_marketplace_preview_command_returns_review_data_without_install(
    tmp_path, approved, monkeypatch
):
    from kardecagent import studio_protocol

    class Preview:
        def as_dict(self):
            return {"id": "external-guide", "name": "External guide", "fingerprint": "abc"}

    monkeypatch.setattr(
        studio_protocol, "preview_github_skill",
        lambda root, source_url: Preview(),
    )
    result = studio_protocol._skill_command(
        tmp_path,
        payload("skills.import.preview", source_url="https://github.com/acme/repo/tree/main/external-guide"),
        approved,
    )
    assert result["preview"]["id"] == "external-guide"
    assert not (tmp_path / ".kardecagent" / "skills" / "external-guide").exists()


def test_marketplace_install_command_invalidates_old_session_consent(
    tmp_path, approved, monkeypatch
):
    from kardecagent import studio_protocol

    _skill_command(tmp_path, payload(
        "skills.save", id="external-guide", name="External guide", tags=[],
        dependencies=[], instructions="Local version.",
    ), approved)
    _skill_command(tmp_path, payload("skills.activation.set", id="external-guide", enabled=True), approved)
    monkeypatch.setattr(
        studio_protocol, "install_github_skill",
        lambda *args, **kwargs: {"installed_id": "external-guide", "fingerprint": "new"},
    )
    result = studio_protocol._skill_command(
        tmp_path,
        payload(
            "skills.import.install", source_url="https://github.com/acme/repo/tree/main/external-guide",
            fingerprint="new", approved=True, replace_existing=True,
        ),
        approved,
    )
    assert result["installed_id"] == "external-guide"
    assert "external-guide" not in approved
