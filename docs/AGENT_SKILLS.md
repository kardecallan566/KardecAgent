# Agent Skills

Project-local skills live under `.kardecagent/skills/<skill-id>/`:

- `manifest.json`: strict metadata and dependencies.
- `SKILL.md`: Markdown instructions loaded only when explicitly requested.

Example manifest:

```json
{
  "id": "python-testing",
  "name": "Python testing",
  "description": "Guidance for focused Python regression tests.",
  "version": "1.0.0",
  "tags": ["python", "testing"],
  "dependencies": []
}
```

IDs are lowercase slugs. Dependencies must exist and form an acyclic graph.
Packages and instruction files are size-limited; symlinks, unknown manifest
fields, invalid UTF-8, and path escapes are rejected.

Every skill requires explicit user approval before activation, regardless of
where it came from. Trust and activation cannot be self-declared by a package
manifest. The caller must obtain approval before passing the explicit approval
flag to the loader; the approval must cover the selected skill and its dependency
chain.

Skills are instruction/reference bundles only. They do not contain executable
hooks, grant tool permissions, install dependencies, or authorize model changes.
Loaded text is always marked as untrusted reference material and cannot override
the agent's system, developer, or user instructions. Discovery reads metadata;
instruction bodies are read only when a skill is explicitly loaded.
