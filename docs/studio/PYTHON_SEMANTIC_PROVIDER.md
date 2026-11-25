# Python semantic language provider

KardecAgent Studio retains its deterministic, dependency-free lexical completion
provider and adds an optional Jedi-backed semantic provider for Python files.

## Editor integration

When the Core is online and a Python or stub file is open, Studio sends bounded
JSONL requests to the existing Core process. Responses are correlated by command
ID; newer requests supersede stale completion and diagnostic responses.

- `Ctrl+Space`: merges Jedi context-aware completions with lexical fallback.
- `F12`: navigates to the symbol definition, including project imports.
- `Shift+F12`: lists references in the Problems panel.
- Live syntax diagnostics are refreshed after edits with a short debounce.

The editor continues to provide lexical completions when Jedi is unavailable.
Semantic requests never execute project code or apply edits.

## Supported operations

- `complete`: context-aware Python completions with kind, detail and docstring.
- `definition`: resolve symbol definitions, following project imports but not
  jumping into builtins.
- `references`: locate references to the symbol at the current cursor.
- `diagnostics`: return Python syntax errors from the current editor buffer.

## Installation and packaging

Install the optional dependency with:

```bash
python -m pip install "kardecagent[studio]"
```

The Windows installer bundles Jedi and Parso in the standalone Core. Source
installations without the extra continue to use lexical completion and show an
informational notice when semantic features are requested.

## Safety and resource boundaries

- Python and stub files only; source is capped at 1 MB.
- The file must resolve inside the selected workspace.
- Results are capped at 25 and descriptions/docstrings are bounded to keep JSONL responses below the desktop protocol frame limit.
- Builtin navigation is disabled; references exclude builtins.
- The Core validates the workspace root and request identity.
- Semantic operations are read-only and do not execute project code.
