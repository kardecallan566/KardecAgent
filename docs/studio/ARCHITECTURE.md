# KardecAgent Studio — Architecture & Implementation Plan

## Product goal

Build a native desktop workspace for KardecAgent. The UI is a control and observation surface; the existing Python core remains the source of truth for task execution, authorization, state transitions, persistence, verification, and recovery.

The application must remain economical in CPU and RAM because local model inference is already resource-intensive.

## Architectural boundaries

- **Desktop shell:** Tauri. Keep the webview lightweight and avoid bundling a second runtime unnecessarily.
- **UI:** React + TypeScript; feature-oriented modules, strict types, small components, lazy-loaded routes/panels.
- **Editor:** Monaco loaded only when an editor tab is opened; avoid eagerly mounting workers or multiple editor instances.
- **Core:** existing Python package, launched/managed as a child process by the desktop shell. Do not duplicate business logic in TypeScript.
- **Bridge:** versioned, typed IPC messages. Prefer request/response for commands and event streams for updates. Validate every payload at the boundary.
- **Persistence:** Python core owns durable task state and journal. UI stores only preferences/layout/session presentation state.
- **Filesystem:** backend mediates project operations; enforce canonical paths, workspace boundaries and symlink policy. UI must not become a privileged filesystem executor.

## Runtime principles

1. One orchestrator owns task scheduling and state transitions.
2. Agents are logical workers; concurrency is bounded by explicit resource budgets.
3. Approval requests are durable, operation-scoped and independently resolvable.
4. A worker awaiting approval releases execution capacity; unrelated eligible work may continue.
5. File write conflicts are detected before concurrent edits; no implicit shared-write concurrency.
6. Cancellation, pause, resume and recovery are explicit commands with observable outcomes.
7. UI renders snapshots plus incremental events; it never polls at high frequency.
8. Event subscriptions are lifecycle-managed and removed when panels/workspaces unmount.
9. Logs/events are bounded, paginated and virtualized; never retain unbounded output in memory.
10. Avoid background indexing, file watchers and syntax services until needed; debounce and cap them.

## Resource-efficiency requirements

- Idle UI target: no continuous polling and near-zero recurring CPU work.
- Event-driven updates with batching/coalescing for progress telemetry.
- A single editor model per open file; dispose models/workers on close.
- Lazy-load Monaco, diff viewer, graph visualization and heavy panels.
- Virtualize large file trees, task lists and event timelines.
- Limit event/log buffers and expose pagination.
- File watcher scope is restricted to the active workspace and relevant paths.
- No embedded local LLM in the UI process; inference remains in the existing runtime/provider.
- Bounded concurrency configurable by user and conservative by default.
- Instrument startup time, idle CPU, resident memory, event throughput and task latency.

## Initial UX layout

- **Activity/navigation rail:** Explorer, Agent, Tasks, Approvals, History, Settings.
- **Explorer:** project tree and recently opened files.
- **Editor area:** tabs and code editor.
- **Agent panel:** prompt, plan, current activity and task context.
- **Bottom/side panel:** task graph, event timeline, output and changes (switchable).
- **Status bar:** active project, runtime/model, resource/concurrency state and pending approvals.

## Core domain contracts (initial)

- Project
- Task
- Subtask / Dependency
- AgentRun
- ApprovalRequest
- Operation / ChangeSet
- Checkpoint
- Event
- ResourceBudget

Every entity needs stable IDs, timestamps, schema versioning and explicit lifecycle states. Approval decisions must reference the exact request and operation fingerprint; never authorize a broad future action.

## Delivery sequence

### 0 — Baseline and contracts
- Audit current core APIs, controller, persistence and tests.
- Define IPC protocol and event schemas.
- Add architecture decisions and resource benchmarks.

### 1 — Desktop shell
- Tauri + React + TypeScript scaffold.
- Design system, app shell, navigation, project-open flow and empty states.
- No agent execution duplicated in frontend.

### 2 — Project workspace
- Safe project selection, file tree, tabs and lazy editor.
- Bounded filesystem/event handling and tests.

### 3 — Runtime bridge
- Launch/health-check Python core.
- Typed IPC, snapshots, event subscriptions and reconnect behavior.

### 4 — Task & execution observability
- Task list/graph, timeline, status, output and change inspection.
- Event-driven UI with bounded buffers.

### 5 — Approval center
- Durable inbox, detailed operation context, approve/reject and carefully scoped batch decisions.
- Approval audit trail and tests for stale/replayed approvals.

### 6 — Orchestration and mini-agents
- Resource-budgeted scheduler, dependency handling, concurrency limits and conflict detection.
- Waiting approvals do not occupy worker slots.
- Safe pause/cancel/recovery semantics.

### 7 — Hardening
- Accessibility, keyboard navigation, error recovery, packaging, performance budgets and end-to-end tests.

## Quality gates

- Python tests remain green; new core behavior has unit/integration tests.
- Frontend lint, typecheck and tests are required.
- IPC schema and authorization boundary tests.
- No approval replay or scope widening.
- No unbounded logs, polling loops or leaked subscriptions.
- Performance measurements recorded for cold start, idle CPU/RAM and large workspaces.
- Build/package smoke test on supported desktop platforms.

## Non-goals for the first release

- Multiple independent LLM instances by default.
- Always-on semantic indexing.
- Unbounded parallel agents.
- A browser-based remote control plane.
- Reimplementing controller, task state machine or safety logic in the frontend.
