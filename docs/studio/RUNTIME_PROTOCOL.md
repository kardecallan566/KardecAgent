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

Every command has `schema_version: 1` and a unique `command_id`. The runtime keeps a bounded recent-command ledger and rejects repeated task submission IDs to avoid replaying a request.

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

The runtime root is fixed and canonicalized when the child process starts. Each `task.submit` must carry a `project_root` resolving to that same workspace; mismatches are rejected and cannot switch the process to another workspace.

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

- `runtime.ready`: process initialized, workspace fixed and resource limits announced (`max_concurrent_tasks`, `max_concurrent_subtasks`, `max_subtasks`, LLM iteration and output caps).
- `command.accepted` / `command.rejected`: command acknowledgement.
- `task.created`: task accepted by the manager.
- `core.event`: bounded lifecycle envelope from `TaskState.record`, including event type, sequence, iteration, message and event data. The UI uses `plan_created`, `plan_step_started`, `plan_step_completed` and subtask board snapshots to build the live task map. Events carry both the persisted task ID and a per-execution ID so repeated runs of the same task remain distinct.
- `task.created`, `approval.requested`, `core.event`, `task.completed` and `task.failed` include the execution ID.
- `approval.requested`: plan or additional high-risk approval; contains the exact approval ID and formatted plan.
- `task.completed` / `task.failed`: terminal task result.
- `runtime.error`: protocol or runtime error.

Event IDs are unique per runtime execution; task IDs remain the Python persistence IDs. Oversized event payloads are compacted at the bridge boundary. The UI should deduplicate by `event_id`, cap retained events and treat unknown event types as forward-compatible.

## Current operational limits

- One submitted task executes at a time per runtime process. The process reads the next task only after the current task reaches a terminal state.
- Approval waits are synchronous in this first bridge; the Python core remains the authority for plan and high-risk approval gates.
- The existing Python orchestrator still executes logical subtasks sequentially. The Studio renders the actual subtask dependency board and one real Python executor; it does not fabricate parallel agents.
- Default resource envelope: one active task per runtime, one active subtask, up to six planned subtasks, configured `max_iterations`, bounded command output, 500-file project snapshots, configured LLM/command timeouts, 8,000-character instructions and 256 KiB event messages.
- These are application-level workload limits, not hard operating-system CPU/RAM quotas; process isolation will be evaluated with profiling before workers are enabled.
- Cancellation, pause/resume commands, startup/reconnect snapshots, durable approval inbox recovery and concurrent submissions are not yet exposed through this protocol.
- Do not add concurrency until resource budgets, workspace write-conflict checks, cancellation and recovery semantics are implemented in the Python scheduler.
