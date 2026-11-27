# Agent Skills

Project-local skills live under `.kardecagent/skills/<skill-id>/`:

- `manifest.json`: strict metadata, dependencies and trust state.
- `SKILL.md`: Markdown instructions loaded only when explicitly requested.

Example manifest:

```json
{
  "id": "python-testing",
  "name": "Python testing",
  "description": "Guidance for focused Python regression tests.",
  "version": "1.0.0",
  "tags": ["python", "testing"],
  "dependencies": [],
  "trust": "local",
  "enabled": true
}
```

IDs are lowercase slugs. Dependencies must exist and form an acyclic graph.
Packages and instruction files are size-limited; symlinks, unknown manifest
fields, invalid UTF-8, and path escapes are rejected. External packages must
remain disabled until explicitly reviewed and approved.

Skills are instruction/reference bundles only. They do not contain executable
hooks, grant tool permissions, install dependencies, or authorize model changes.
Loaded text is always marked as untrusted reference material and cannot override
the agent's system, developer, or user instructions. Discovery reads metadata;
instruction bodies are read only when a skill is explicitly loaded.
