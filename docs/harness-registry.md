# Harness Registry (v0)

The Harness Registry is the first building block for controlled, measurable
agent improvement. It stores a versioned snapshot of the behavioral
configuration without changing model weights or executing configuration
content.

## Location

By convention, a project may store its registry at:

```text
.kardecagent/harness.json
```

The API also accepts an explicit path, which keeps storage decisions outside
the core library and makes tests isolated.

## Schema v1

A registry contains:

- `system_prompt`: optional system-level instruction text.
- `guardrails`: ordered safety and execution constraints.
- `tool_guidance`: mapping of tool names to usage guidance.
- `skills`: named instruction bundles, each with an `enabled` flag.
- `version`: schema version; currently only `1` is supported.

Example:

```json
{
  "version": 1,
  "system_prompt": "Prefer small, verifiable changes.",
  "guardrails": [
    "Never expose secrets.",
    "Ask before destructive actions."
  ],
  "tool_guidance": {
    "shell": "Run focused tests before broad suites."
  },
  "skills": [
    {
      "name": "python-tests",
      "instructions": "Add regression tests for bug fixes.",
      "enabled": true
    }
  ]
}
```

## Python API

```python
from pathlib import Path
from kardecagent.harness import HarnessConfig, SkillConfig, load_registry, save_registry

path = Path(".kardecagent/harness.json")
config = load_registry(path)
updated = HarnessConfig(
    system_prompt="Prefer small, verifiable changes.",
    guardrails=("Never expose secrets.",),
    skills=(SkillConfig("python-tests", "Add regression tests."),),
)
save_registry(path, updated)
```

Loading a missing file returns an empty v1 configuration. Invalid JSON,
unknown fields, duplicate skill names, and unsupported schema versions fail
explicitly rather than being silently discarded. Writes use a temporary file
in the destination directory followed by an atomic replacement.

## Scope and safety

v0 is storage and validation only. It does not yet inject instructions into
the live agent, execute skills, rewrite its own configuration, or run
optimization experiments. This separation lets later evaluator and experiment
components compare immutable candidate snapshots against a baseline before
any candidate is adopted.
