# KardecAgent Studio (frontend)

Desktop UI scaffold for the KardecAgent local-first development environment.

## Development

Requirements: Node.js (LTS), npm, Rust toolchain, and platform prerequisites for Tauri 2.

```bash
cd frontend
npm install
npm run dev          # browser preview (UI only)
npm run desktop:dev  # desktop shell
npm run typecheck
npm run desktop:build
```

The Studio starts the Python core as a managed child process when a project is opened. The desktop bridge uses bounded JSON Lines messages over stdin/stdout; runtime events are forwarded to the UI without polling. Task instructions go to the central manager, and plan/high-risk approvals return through the same channel.

Install the core into the Python environment used by the desktop process from the repository root with `python -m pip install -e .`.

On macOS/Linux the default executable is `python3`; on Windows it is `python`. Set `KARDECAGENT_PYTHON` to an explicit interpreter path when needed. The runtime command is `python -m kardecagent.cli studio --project <path>`.

The initial bridge intentionally handles one submitted task at a time. It waits for approval decisions and does not claim concurrent agent execution. The existing Python orchestrator remains sequential until bounded concurrency and file-conflict handling are implemented.

## Performance constraints

- No polling loop in the idle UI.
- Load heavy editor modules only when a file is opened.
- Keep one editor model per file and dispose it on close.
- Virtualize large trees and event lists.
- Bound logs and event buffers.
- Keep task scheduling and durable state in Python, not React.
