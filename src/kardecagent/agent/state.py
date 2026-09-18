from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    COMPLETED = "completed"
    FAILED = "failed"
    MAX_ITERATIONS = "max_iterations"
    RECOVERING = "recovering"
    RESUMING = "resuming"


_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.RUNNING, TaskStatus.FAILED}),
    TaskStatus.RUNNING: frozenset({
        TaskStatus.VERIFYING, TaskStatus.FAILED, TaskStatus.MAX_ITERATIONS,
    }),
    TaskStatus.VERIFYING: frozenset({
        TaskStatus.RUNNING, TaskStatus.VERIFIED, TaskStatus.FAILED,
    }),
    TaskStatus.VERIFIED: frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.RECOVERING}),
    TaskStatus.MAX_ITERATIONS: frozenset({TaskStatus.RECOVERING}),
    TaskStatus.RECOVERING: frozenset({TaskStatus.RESUMING, TaskStatus.FAILED}),
    TaskStatus.RESUMING: frozenset({TaskStatus.RUNNING, TaskStatus.FAILED}),
}


@dataclass
class AgentEvent:
    iteration: int
    event_type: str
    message: str
    data: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sequence: int = 0


@dataclass
class TaskState:
    task: str
    project_root: str
    status: TaskStatus = TaskStatus.PENDING
    iteration: int = 0
    events: list[AgentEvent] = field(default_factory=list)
    active_plan_step: int = 1
    active_subtask_id: str | None = None
    active_subtask_step: int = 1

    def record(self, event_type: str, message: str, **data) -> None:
        # Keep the in-memory cursor synchronized. The journal replays the same
        # lifecycle events and is the durable source of truth when loading.
        if event_type == "plan_step_started":
            step = data.get("step")
            if isinstance(step, int) and step > 0:
                self.active_plan_step = step
        elif event_type == "plan_step_completed":
            step = data.get("step")
            if isinstance(step, int) and step > 0:
                self.active_plan_step = step + 1
        elif event_type in {"recovery_completed", "subtask_resume_retry_started"}:
            step = data.get("resume_step")
            if isinstance(step, int) and step > 0:
                self.active_plan_step = step
        elif event_type == "subtask_started":
            value = data.get("subtask_id")
            self.active_subtask_id = value if isinstance(value, str) else None
            self.active_subtask_step = 1
        elif event_type in {"subtask_progress", "subtask_retry_started"}:
            step = data.get("plan_step") if event_type == "subtask_progress" else data.get("resume_step")
            if isinstance(step, int) and step > 0:
                self.active_subtask_step = step
        elif event_type in {"subtask_completed", "subtask_failed", "subtask_blocked"}:
            if data.get("subtask_id") == self.active_subtask_id:
                self.active_subtask_id = None
                self.active_subtask_step = 1
        self.events.append(AgentEvent(self.iteration, event_type, message, data, sequence=len(self.events) + 1))

    def transition(self, new_status: TaskStatus, *, reason: str = "", **data) -> None:
        """Move the controller-owned task state through a legal transition."""
        new_status = TaskStatus(new_status)
        old_status = self.status
        if new_status is old_status:
            return
        if new_status not in _ALLOWED_TRANSITIONS[old_status]:
            raise ValueError(
                f"invalid task status transition: {old_status.value} -> {new_status.value}"
            )
        self.status = new_status
        self.record(
            "status_changed",
            reason or f"Task status changed to {new_status.value}.",
            from_status=old_status.value,
            to_status=new_status.value,
            **data,
        )

    def can_transition(self, new_status: TaskStatus) -> bool:
        return TaskStatus(new_status) in _ALLOWED_TRANSITIONS[self.status]
