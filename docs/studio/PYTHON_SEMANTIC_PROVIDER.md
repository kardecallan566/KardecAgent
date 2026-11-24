# Python semantic language provider

KardecAgent Studio's editor keeps its deterministic, dependency-free lexical
completion provider. This module adds an optional Jedi-backed semantic provider
for Python projects, designed to be called by the desktop bridge.

## Supported operations

- `complete`: context-aware Python completions with kind, detail and docstring.
- `definition`: resolve symbol definitions, following project imports but not
  jumping into builtins.
- `references`: locate references to the symbol at the current cursor.

## Installation and fallback

Install the optional dependency with:

```bash
python -m pip install "kardecagent[studio]"
```

If Jedi is unavailable, requests return `available: false`; the Studio must
continue to show its existing lexical completions and diagnostics. Jedi parse
or project errors are returned as bounded messages rather than crashing Core.

## Safety and resource boundaries

- Python and stub files only; source is capped at 1 MB.
- The file must resolve inside the selected workspace.
- Results are capped at 100 and descriptions/docstrings are bounded.
- Builtin navigation is disabled; references exclude builtins.
- This provider does not execute project code or apply edits.

This is the provider layer. Wiring the asynchronous desktop request lifecycle,
cancellation, and editor UI is the next integration step.
