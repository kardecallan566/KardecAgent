/**
 * Runtime event contract shared by the Studio UI and the Python core.
 * Keep payloads small and append-only; UI updates should be driven by events,
 * not by frequent polling. This is a transport contract, not a runtime client.
 */

export type RuntimeStatus = "queued" | "running" | "waiting_approval" | "succeeded" | "failed" | "cancelled";

export interface RuntimeAgent {
  id: string;
  name: string;
  role: string;
  status: RuntimeStatus;
  current_task_id?: string;
}

export interface RuntimeTask {
  id: string;
  parent_id?: string;
  title: string;
  status: RuntimeStatus;
  progress?: number;
  agent_id?: string;
  depends_on: string[];
  resource_paths?: string[];
}

export interface RuntimeEventBase {
  event_id: string;
  schema_version: 1;
  task_id: string;
  timestamp: string;
  agent_id?: string;
}

export type RuntimeEvent =
  | (RuntimeEventBase & { type: "core.event"; payload: { event_type: string; message: string; data: Record<string, unknown>; sequence: number; iteration: number } })
  | (RuntimeEventBase & { type: "task.created"; payload: { title: string; parent_id?: string } })
  | (RuntimeEventBase & { type: "task.status_changed"; payload: { status: RuntimeStatus; reason?: string } })
  | (RuntimeEventBase & { type: "task.progress"; payload: { progress: number; message?: string } })
  | (RuntimeEventBase & { type: "agent.assigned"; payload: { agent_id: string } })
  | (RuntimeEventBase & { type: "agent.output"; payload: { channel: "stdout" | "stderr" | "summary"; content: string } })
  | (RuntimeEventBase & { type: "approval.requested"; payload: { approval_id: string; kind: "plan" | "high_risk"; summary: string; risk: "low" | "medium" | "high" } })
  | (RuntimeEventBase & { type: "resource.changed"; payload: { path: string; operation: "created" | "modified" | "deleted" } })
  | (RuntimeEventBase & { type: "task.completed"; payload: { result_summary?: string } })
  | (RuntimeEventBase & { type: "task.failed"; payload: { message: string; recoverable: boolean } });

export interface RuntimeCommandBase {
  command_id: string;
  schema_version: 1;
}

export type RuntimeCommand =
  | (RuntimeCommandBase & { type: "task.submit"; payload: { instruction: string; project_root: string } })
  | (RuntimeCommandBase & { type: "approval.respond"; payload: { approval_id: string; decision: "approve" | "reject" } });

export interface RuntimeCommandAccepted {
  command_id: string;
  accepted: boolean;
  reason?: string;
}

export interface RuntimeSnapshot {
  schema_version: 1;
  generated_at: string;
  agents: RuntimeAgent[];
  tasks: RuntimeTask[];
}

/**
 * Event consumers should ignore already-seen event_id values and tolerate
 * out-of-order delivery by comparing timestamp plus event_id.
 * Unknown event types/versions must be ignored safely by the UI.
 */
