"""Typed, versioned schema for agent harness configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SCHEMA_VERSION = 1


class HarnessSchemaError(ValueError):
    """Raised when a harness configuration does not match its schema."""


@dataclass(frozen=True, slots=True)
class SkillConfig:
    """A named, opt-in instruction bundle available to the agent."""

    name: str
    instructions: str
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise HarnessSchemaError("skill.name must be a non-empty string.")
        if not isinstance(self.instructions, str):
            raise HarnessSchemaError("skill.instructions must be a string.")
        if not isinstance(self.enabled, bool):
            raise HarnessSchemaError("skill.enabled must be a boolean.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "instructions": self.instructions,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SkillConfig":
        if not isinstance(value, dict):
            raise HarnessSchemaError("Each skill must be a JSON object.")
        allowed = {"name", "instructions", "enabled"}
        unknown = set(value) - allowed
        if unknown:
            raise HarnessSchemaError(f"Unknown skill field(s): {', '.join(sorted(unknown))}.")
        if "name" not in value or "instructions" not in value:
            raise HarnessSchemaError("Each skill requires name and instructions.")
        return cls(
            name=value["name"],
            instructions=value["instructions"],
            enabled=value.get("enabled", True),
        )


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    """A complete, serializable snapshot of the agent's behavioral harness."""

    system_prompt: str = ""
    guardrails: tuple[str, ...] = field(default_factory=tuple)
    tool_guidance: dict[str, str] = field(default_factory=dict)
    skills: tuple[SkillConfig, ...] = field(default_factory=tuple)
    version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != SCHEMA_VERSION:
            raise HarnessSchemaError(f"Unsupported harness schema version: {self.version!r}.")
        if not isinstance(self.system_prompt, str):
            raise HarnessSchemaError("system_prompt must be a string.")
        if not isinstance(self.guardrails, (tuple, list)) or not all(
            isinstance(item, str) for item in self.guardrails
        ):
            raise HarnessSchemaError("guardrails must be a list of strings.")
        if not isinstance(self.tool_guidance, dict) or not all(
            isinstance(key, str) and key.strip() and isinstance(value, str)
            for key, value in self.tool_guidance.items()
        ):
            raise HarnessSchemaError("tool_guidance must map non-empty tool names to strings.")
        if not isinstance(self.skills, (tuple, list)) or not all(
            isinstance(skill, SkillConfig) for skill in self.skills
        ):
            raise HarnessSchemaError("skills must contain SkillConfig entries.")
        names = [skill.name.casefold() for skill in self.skills]
        if len(names) != len(set(names)):
            raise HarnessSchemaError("Skill names must be unique (case-insensitive).")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "system_prompt": self.system_prompt,
            "guardrails": list(self.guardrails),
            "tool_guidance": dict(self.tool_guidance),
            "skills": [skill.to_dict() for skill in self.skills],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "HarnessConfig":
        if not isinstance(value, dict):
            raise HarnessSchemaError("Harness configuration must be a JSON object.")
        allowed = {"version", "system_prompt", "guardrails", "tool_guidance", "skills"}
        unknown = set(value) - allowed
        if unknown:
            raise HarnessSchemaError(f"Unknown harness field(s): {', '.join(sorted(unknown))}.")
        version = value.get("version", SCHEMA_VERSION)
        skills_value = value.get("skills", [])
        if not isinstance(skills_value, list):
            raise HarnessSchemaError("skills must be a JSON array.")
        guardrails = value.get("guardrails", [])
        if not isinstance(guardrails, list):
            raise HarnessSchemaError("guardrails must be a JSON array.")
        tool_guidance = value.get("tool_guidance", {})
        if not isinstance(tool_guidance, dict):
            raise HarnessSchemaError("tool_guidance must be a JSON object.")
        return cls(
            version=version,
            system_prompt=value.get("system_prompt", ""),
            guardrails=tuple(guardrails),
            tool_guidance=tool_guidance,
            skills=tuple(SkillConfig.from_dict(item) for item in skills_value),
        )
