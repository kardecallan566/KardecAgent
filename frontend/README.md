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

The bridge intentionally handles one submitted task and one subtask at a time. The Studio graph is driven by `TaskState.record` events and real `TaskBoard` snapshots; it displays the dependency graph, scopes, results and statuses without polling. A single Python executor is shown until the core has actual worker processes. The runtime announces its current limits (one concurrent task and subtask, six planned subtasks, configured LLM iterations and timeouts, bounded command output, 500-file project snapshots, 8,000-character instructions and 256 KiB events). Concurrent execution remains deferred until resource budgets, file-conflict detection, cancellation and recovery are implemented.

## Performance constraints

- No polling loop in the idle UI.
- Load heavy editor modules only when a file is opened.
- Keep one editor model per file and dispose it on close.
- Virtualize large trees and event lists.
- Bound logs and event buffers.
- Keep task scheduling and durable state in Python, not React.
