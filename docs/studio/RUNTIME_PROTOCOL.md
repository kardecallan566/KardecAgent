# Studio runtime protocol (v1)

## Process boundary

The Tauri desktop process launches the installed Python package as a child process:

`python -m kardecagent.cli studio --project <canonical-project-path>`

The executable defaults to `python3` on macOS/Linux and `python` on Windows. Set `KARDECAGENT_PYTHON` to select an explicit interpreter. The package must be installed in that interpreter (development: `python -m pip install -e .` from the repository root).

The bridge is JSON Lines over stdin/stdout:
- one JSON object per line;
- stdout is reserved for protocol messages;
- Python logs and diagnostics go to stderr;
- inbound command lines are limited to 64 KiB;
- task instructions are limited to 8,000 characters;
- Tauri forwards messages as `runtime-message` events;
- there is no polling loop.

## Commands

Every command has `schema_version: 1` and a unique `command_id`.

Submit an instruction to the central manager:

```json
{
  "type": "task.submit",
  "schema_version": 1,
  "command_id": "client-generated-id",
  "payload": {
    "instruction": "Analyze this project and implement the requested feature.",
    "project_root": "/canonical/project/path"
  }
}
```

The runtime root is fixed when the child process starts. The payload's `project_root` is contextual metadata and cannot switch the process to another workspace.

Resolve an approval:

```json
{
  "type": "approval.respond",
  "schema_version": 1,
  "command_id": "client-generated-id",
  "payload": {
    "approval_id": "id-from-approval.requested",
    "decision": "approve"
  }
}
```

A response is accepted only for the currently pending approval ID. While approval is pending, other commands are rejected. Rejection ends the current task without executing its plan.

## Events

- `runtime.ready`: process initialized and workspace fixed.
- `command.accepted` / `command.rejected`: command acknowledgement.
- `task.created`: task accepted by the manager.
- `core.event`: lossless lifecycle envelope from `TaskState.record`, including event type, sequence, iteration, message and event data.
- `approval.requested`: plan or additional high-risk approval; contains the exact approval ID and formatted plan.
- `task.completed` / `task.failed`: terminal task result.
- `runtime.error`: protocol or runtime error.

Event IDs are unique per runtime execution; task IDs remain the Python persistence IDs. The UI should deduplicate by `event_id`, cap retained events and treat unknown event types as forward-compatible.

## Current operational limits

- One submitted task executes at a time per runtime process. The process reads the next task only after the current task reaches a terminal state.
- Approval waits are synchronous in this first bridge; the Python core remains the authority for plan and high-risk approval gates.
- The existing Python orchestrator still executes logical subtasks sequentially. Visual parallel-agent cards are not evidence of concurrent runtime workers.
- Cancellation, pause/resume commands, task snapshots, durable approval inbox recovery and concurrent submissions are not yet exposed through this protocol.
- Do not add concurrency until resource budgets, workspace write-conflict checks, cancellation and recovery semantics are implemented in the Python scheduler.
