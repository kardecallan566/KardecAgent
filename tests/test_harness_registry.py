import json

import pytest

from kardecagent.harness import (
    HarnessConfig,
    HarnessRegistryError,
    SkillConfig,
    load_registry,
    save_registry,
)
from kardecagent.harness.schema import HarnessSchemaError


def test_missing_registry_returns_empty_default(tmp_path):
    config = load_registry(tmp_path / ".kardecagent" / "harness.json")

    assert config == HarnessConfig()
    assert config.version == 1
    assert config.skills == ()


def test_registry_round_trip_preserves_all_harness_components(tmp_path):
    path = tmp_path / ".kardecagent" / "harness.json"
    config = HarnessConfig(
        system_prompt="Prefer small, verifiable changes.",
        guardrails=("Never expose secrets.", "Ask before destructive actions."),
        tool_guidance={"shell": "Run focused tests before broad suites."},
        skills=(SkillConfig("python-tests", "Add regression tests."),),
    )

    saved_path = save_registry(path, config)

    assert saved_path == path
    assert load_registry(path) == config
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_registry_rejects_unknown_fields(tmp_path):
    path = tmp_path / "harness.json"
    path.write_text(json.dumps({"version": 1, "surprise": True}), encoding="utf-8")

    with pytest.raises(HarnessRegistryError, match="Unknown harness field"):
        load_registry(path)


def test_registry_rejects_unsupported_schema_version(tmp_path):
    path = tmp_path / "harness.json"
    path.write_text(json.dumps({"version": 2}), encoding="utf-8")

    with pytest.raises(HarnessRegistryError, match="Unsupported harness schema"):
        load_registry(path)


def test_skill_names_must_be_unique_case_insensitively():
    with pytest.raises(HarnessSchemaError, match="unique"):
        HarnessConfig(skills=(
            SkillConfig("Review", "First instructions."),
            SkillConfig("review", "Second instructions."),
        ))


def test_save_requires_typed_config(tmp_path):
    with pytest.raises(HarnessRegistryError, match="HarnessConfig"):
        save_registry(tmp_path / "harness.json", {"version": 1})
