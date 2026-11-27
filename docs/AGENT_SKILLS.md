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

## Studio manager and approval lifecycle

The Studio's Skills view sends bounded JSONL commands to the already workspace-bound Core:
`skills.list`, `skills.body`, `skills.save`, `skills.delete`, and
`skills.activation.set`. Metadata is listed without reading the instruction body;
the body is fetched only when the user opens a skill for editing.

Creating or editing validates the strict manifest, dependency closure, graph
acyclicity, package paths, symlinks, and the 64 KiB instruction limit. Files are
replaced atomically. A skill with dependents cannot be deleted.

Activation is an explicit user action. Consent is held only in the Core process
memory and applies to the selected skill plus its dependency chain. The consent
is bound to SHA-256 fingerprints of each approved manifest and instruction
file. Editing a skill or one of its dependencies invalidates the affected
consent. Restarting the Core clears all activation consent and requires the
user to authorize skills again. This intentionally prevents project files or
modified skill content from silently granting or inheriting approval. Without
valid consent for the complete dependency chain, the agent continues the task
without those skills.

## External marketplace imports

The Studio marketplace can preview and import an instruction-only Skill from a
public GitHub folder URL in the form
`https://github.com/OWNER/REPO/tree/BRANCH/SKILL_PATH`. It uses the GitHub
Contents API over HTTPS, disables redirects, limits response sizes, validates
the manifest and dependency graph, and previews the instruction text before
installation. Private repositories and arbitrary hosts are not supported.

Installation requires a separate explicit confirmation after preview. The
installer fetches the package again and compares its SHA-256 fingerprint with
the reviewed preview, so a changed remote package cannot be installed under an
old approval. Replacing an existing Skill requires an additional confirmation.
Only `manifest.json` and `SKILL.md` are imported; no scripts, binaries,
dependencies, or hooks are downloaded or executed. Imported Skills remain
untrusted instructions and must be separately authorized for the current Core
session before task use.

## Evidence-based learning and proposals

The learning report reads only the latest persisted task records (up to 100)
and counts explicit `skill_outcome` events. It reports verified successes and
tasks that ended without a verified result; it does not claim that a Skill
caused a failure. Repeated unmatched task signatures can produce a draft for a
new Skill, while repeated unverified outcomes can produce a review draft for
an existing Skill.

The engine uses minimum sample thresholds and never writes to a Skill. The
Studio's **Analisar uso** action displays the measured metrics and proposals.
**Abrir rascunho** only places a proposed manifest and instruction draft in the
editor; the user must review and explicitly save it. Saving changed content
invalidates its previous SHA-256 activation consent. No model is called and no
automatic self-modification occurs in this first learning iteration.
