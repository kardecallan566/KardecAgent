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

The current screen is a visual shell. Project opening, file operations, task execution, and approvals are intentionally not simulated: they will be connected to the Python core through a typed IPC contract in subsequent milestones.

## Performance constraints

- No polling loop in the idle UI.
- Load heavy editor modules only when a file is opened.
- Keep one editor model per file and dispose it on close.
- Virtualize large trees and event lists.
- Bound logs and event buffers.
- Keep task scheduling and durable state in Python, not React.
