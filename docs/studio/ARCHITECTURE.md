# KardecAgent Studio — Architecture & Implementation Plan

## Product goal

Build a native desktop workspace for KardecAgent. The UI is a control and observation surface; the existing Python core remains the source of truth for task execution, authorization, state transitions, persistence, verification, and recovery. Rust is a possible future, selective optimization—not a prerequisite for the MVP. See [Rust migration note](./RUST_MIGRATION_NOTE.md).

The application must remain economical in CPU and RAM because local model inference is already resource-intensive.

## Architectural boundaries

- **Desktop shell:** Tauri. Keep the webview lightweight and avoid bundling a second runtime unnecessarily.
- **UI:** React + TypeScript; feature-oriented modules, strict types, small components, lazy-loaded routes/panels.
- **Editor:** Monaco loaded only when an editor tab is opened; avoid eagerly mounting workers or multiple editor instances.
- **Core:** existing Python package, launched/managed as a child process by the desktop shell. Do not duplicate business logic in TypeScript.
- **Bridge:** versioned, typed IPC messages. Prefer request/response for commands and event streams for updates. Validate every payload at the boundary.
- **Frontend contract:** `frontend/src/types/runtime.ts` defines the versioned snapshot, agent, task and event envelopes. It is the UI-facing contract; the Python JSONL adapter and Tauri child-process transport are now connected for task submission, approvals and lifecycle events.
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
   - Deduplicate events by `event_id`, tolerate out-of-order delivery, and ignore unknown schema versions/types safely.
   - Keep event payloads compact; stream large output separately and paginate historical logs.
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

## Technology evolution (deferred)\n\n- Keep Python as the agent/core implementation through the MVP and early versions.\n- Validate desktop UX, real agent execution, approvals, recovery and CPU/RAM usage before architectural rewrites.\n- Revisit Rust after several versions and profiling; port only modules with clear measured benefit.\n- No full rewrite: preserve Python components that remain appropriate.\n\n## Delivery sequence

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
- [x] Launch the Python core and expose a startup handshake with its resource envelope.
- [x] Typed JSONL commands, approval responses and event subscriptions.
- [x] Stream plan steps and actual subtask board snapshots into the task map.
- [x] Bounded startup restoration of the 20 most recently updated persisted tasks and their latest 100 events each.
- [x] Explicit resume action for approved persisted tasks, with a fresh execution ID and live event forwarding.
- [ ] Reconnect during an active child-process session and cooperative runtime shutdown/cancel commands.

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


## Runtime bridge implementation status

The first desktop bridge is implemented in `src/kardecagent/studio_protocol.py` and `frontend/src-tauri/src/lib.rs`. Tauri manages a single Python child process, forwards JSONL stdout messages as runtime events, and sends typed commands through stdin. The Studio instruction composer submits to the central manager; plan and high-risk approval requests are shown in the Agent panel and resolved against their exact approval IDs.

The bridge accepts one task at a time and waits synchronously for approval responses. `TaskState.record` forwards lifecycle events without changing the Python state machine or journal. The Studio consumes the real approved plan steps and `TaskBoard` snapshots embedded in lifecycle events, rendering current step progress, dependency, scope and status information. It shows one Python executor rather than simulated parallel agents. The runtime announces conservative limits (one task and one subtask at a time, at most six planned subtasks, configured LLM iterations and timeouts, bounded command output, 500-file project snapshots and 256 KiB events). Concurrent scheduling remains deferred until resource budgets, workspace write-conflict protection, cancellation and recovery are implemented in Python.


### Startup task restoration

The Python runtime now queries the existing `TaskStore` once at startup and replays a bounded history into the Studio event stream (up to 20 tasks and 100 events per task). This reuses the core's atomic snapshots and hash-chained journals rather than introducing a second persistence system. Interrupted executions are labeled recoverable but are not resumed automatically; explicit user intent is required before any further project mutation. Pending approvals that were not yet durably persisted remain outside this restoration scope. Recoverable task cards now expose an explicit **Retomar tarefa** action; the protocol validates the active project root and task ID, then calls the existing `AgentLoop.resume` path without creating a new plan or bypassing the original approval record. Resume events are streamed under a new execution ID.

A GitHub Actions workflow now runs the Python test suite, the Studio TypeScript/Vite production build, and a Linux Tauri/Rust `cargo check` on pushes to the Studio branch and pull requests targeting the development branches. The Rust check installs the documented Tauri Linux system libraries and builds the frontend assets first. Superseded runs are cancelled to avoid wasting CI resources. Local validation should still be performed before merging; workflow results are authoritative for the checked commit.
