# KardecAgent Studio — Core Runtime

## Windows release

The Windows installer bundles a self-contained `kardecagent-core.exe` built from the Python package with PyInstaller. The Studio launches that executable as a hidden child process when a project is opened. The Core communicates with the UI through newline-delimited JSON over stdin/stdout; stderr is forwarded to the Studio diagnostics panel.

A normal Windows installation does **not** require a separate Python installation, a terminal window, or a manual `pip install`.

The Windows release workflow:
1. Installs the project and PyInstaller in its build environment.
2. Packages the Core into `frontend/src-tauri/core-dist/kardecagent-core.exe`.
3. Runs `scripts/smoke_studio_core.py` to verify the `runtime.ready` JSONL handshake.
4. Bundles the executable as a Tauri resource in the Studio installer.

## Ollama requirement

The Core process and the Ollama model server are separate processes. The Studio starts the Core automatically, but Ollama must be installed and its local server must be reachable at the configured URL (by default `http://127.0.0.1:11434`). The selected model must already be downloaded in Ollama. The Core's Ollama health command checks the server, model list, and a small inference request without requiring a system Python installation in the packaged app.

## Developer fallback

When running an unpackaged Studio build, the launcher uses the bundled executable if one exists. Otherwise it falls back to a Python interpreter that can import `kardecagent` and `kardecagent.cli`. Install the project in the selected environment with:

```powershell
python -m pip install -e .
```

The optional Python path in Studio settings is intended for development and troubleshooting. Leave it blank for normal installed releases so the bundled Core is selected.

## Diagnostics

Open **Settings → Diagnostics** to inspect the real child-process PID, launch mode, executable path, workspace, exit code, and recent stderr output. A successful Ollama connection test alone does not prove that the Core process has started; the UI reports the Core as connected only after receiving `runtime.ready`.
