"""Load and atomically persist versioned harness configuration snapshots."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .schema import HarnessConfig, HarnessSchemaError


DEFAULT_REGISTRY_PATH = Path(".kardecagent") / "harness.json"


class HarnessRegistryError(ValueError):
    """Raised when a harness registry cannot be read or written safely."""


def load_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> HarnessConfig:
    """Load a registry, returning an empty v1 config when it does not exist."""
    registry_path = Path(path)
    try:
        raw = registry_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return HarnessConfig()
    except OSError as exc:
        raise HarnessRegistryError(f"Could not read harness registry: {exc}") from exc

    try:
        payload = json.loads(raw)
        return HarnessConfig.from_dict(payload)
    except (json.JSONDecodeError, HarnessSchemaError) as exc:
        raise HarnessRegistryError(f"Invalid harness registry at {registry_path}: {exc}") from exc


def save_registry(
    path: str | Path,
    config: HarnessConfig,
) -> Path:
    """Persist a complete snapshot using a same-directory atomic replacement."""
    if not isinstance(config, HarnessConfig):
        raise HarnessRegistryError("config must be a HarnessConfig instance.")

    registry_path = Path(path)
    parent = registry_path.parent
    temporary_path: Path | None = None
    try:
        parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{registry_path.name}.",
            suffix=".tmp",
            dir=parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(config.to_dict(), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, registry_path)
        temporary_path = None
        return registry_path
    except OSError as exc:
        raise HarnessRegistryError(f"Could not write harness registry: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def compose_system_prompt(base_prompt: str, config: HarnessConfig, *, phase: str = "") -> str:
    """Compose a model-facing prompt from a validated snapshot.

    Registry content is treated as instruction text, never as executable code.
    """
    sections = [base_prompt.strip()]
    if config.system_prompt.strip():
        sections.append("Project harness instructions:\\n" + config.system_prompt.strip())
    if config.guardrails:
        sections.append("Project guardrails (must be followed):\\n- " +
                       "\\n- ".join(config.guardrails))
    enabled_skills = [skill for skill in config.skills if skill.enabled]
    if enabled_skills:
        sections.append("Enabled skills (instruction bundles):\\n" + "\\n".join(
            f"[{skill.name}]\\n{skill.instructions}" for skill in enabled_skills
        ))
    if config.tool_guidance:
        sections.append("Tool guidance:\\n" + "\\n".join(
            f"- {name}: {guidance}" for name, guidance in sorted(config.tool_guidance.items())
        ))
    if phase.strip():
        sections.append(phase.strip())
    return "\\n\\n".join(sections)
