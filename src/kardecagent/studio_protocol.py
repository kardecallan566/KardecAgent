from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone

from .agent.loop import AgentLoop
from .agent.plan import ExecutionPlan
from .agent.state import AgentEvent
from .agent.persistence import task_id


MAX_MESSAGE_BYTES = 64 * 1024
MAX_EVENT_BYTES = 256 * 1024
MAX_INSTRUCTION_LENGTH = 8000


class StudioProtocolError(ValueError):
    pass


def _write(message: dict) -> None:
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
        bounded = dict(message)
        payload = bounded.get("payload")
        if isinstance(payload, dict):
            payload = dict(payload)
            if "data" in payload:
                payload["data"] = {"truncated": True}
            for key in ("message", "summary", "result_summary"):
                if isinstance(payload.get(key), str):
                    payload[key] = payload[key][:4000]
            bounded["payload"] = payload
        if isinstance(bounded.get("message"), str):
            bounded["message"] = bounded["message"][:4000]
        encoded = json.dumps(bounded, ensure_ascii=False, separators=(",", ":"), default=str)
    sys.stdout.write(encoded + "\n")
    sys.stdout.flush()


def _read_message() -> dict:
    line = sys.stdin.readline(MAX_MESSAGE_BYTES + 1)
    if not line:
        raise EOFError("Studio disconnected while waiting for a command or approval.")
    if len(line.encode("utf-8")) > MAX_MESSAGE_BYTES:
        while line and not line.endswith("\n"):
            line = sys.stdin.readline(MAX_MESSAGE_BYTES + 1)
        raise StudioProtocolError("Command exceeds the 64 KiB protocol limit.")
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise StudioProtocolError("Command must be a valid JSON object.") from exc
    if not isinstance(message, dict):
        raise StudioProtocolError("Command must be a JSON object.")
    return message


def _approval(
    task_identifier: str,
    plan: ExecutionPlan,
    *,
    kind: str,
) -> bool:
    approval_identifier = str(uuid.uuid4())
    risk = "high" if plan.security_level == "high_risk" else (
        "medium" if plan.security_level == "sensitive" else "low"
    )
    _write({
        "type": "approval.requested",
        "schema_version": 1,
        "event_id": str(uuid.uuid4()),
        "task_id": task_identifier,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "approval_id": approval_identifier,
            "kind": kind,
            "summary": plan.format_for_review(),
            "risk": risk,
        },
    })

    while True:
        try:
            message = _read_message()
        except StudioProtocolError as exc:
            _write({"type": "command.rejected", "schema_version": 1, "reason": str(exc)})
            continue
        if message.get("schema_version") != 1 or message.get("type") != "approval.respond":
            _write({
                "type": "command.rejected",
                "schema_version": 1,
                "command_id": command_identifier,
                "reason": "Only the matching approval response is accepted while approval is pending.",
            })
            continue
        command_identifier = message.get("command_id")
        if not isinstance(command_identifier, str) or not command_identifier.strip():
            _write({"type": "command.rejected", "schema_version": 1, "reason": "command_id is required."})
            continue
        payload = message.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if payload.get("approval_id") != approval_identifier:
            _write({
                "type": "command.rejected",
                "schema_version": 1,
                "command_id": message.get("command_id"),
                "reason": "Approval identifier does not match the pending request.",
            })
            continue
        decision = payload.get("decision")
        if decision not in {"approve", "reject"}:
            _write({
                "type": "command.rejected",
                "schema_version": 1,
                "command_id": message.get("command_id"),
                "reason": "Decision must be approve or reject.",
            })
            continue
        _write({
            "type": "command.accepted",
            "schema_version": 1,
            "command_id": message.get("command_id"),
            "accepted": True,
        })
        return decision == "approve"


def run_stdio(project_root: Path, agent: AgentLoop) -> int:
    """Serve a bounded JSONL command/event stream for the desktop Studio."""
    root = project_root.resolve()
    _write({"type": "runtime.ready", "schema_version": 1, "project_root": str(root)})

    while True:
        try:
            message = _read_message()
        except EOFError:
            return 0
        except StudioProtocolError as exc:
            _write({"type": "runtime.error", "schema_version": 1, "message": str(exc)})
            continue

        if message.get("schema_version") != 1 or message.get("type") != "task.submit":
            _write({
                "type": "command.rejected",
                "schema_version": 1,
                "command_id": message.get("command_id"),
                "reason": "Unsupported command. Expected task.submit.",
            })
            continue

        command_identifier = message.get("command_id")
        payload = message.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        instruction = payload.get("instruction")
        if not isinstance(command_identifier, str) or not command_identifier.strip():
            _write({"type": "command.rejected", "reason": "command_id is required."})
            continue
        if not isinstance(instruction, str) or not instruction.strip():
            _write({"type": "command.rejected", "command_id": command_identifier, "reason": "instruction is required."})
            continue
        instruction = instruction.strip()
        if len(instruction) > MAX_INSTRUCTION_LENGTH:
            _write({"type": "command.rejected", "command_id": command_identifier, "reason": "instruction exceeds 8000 characters."})
            continue

        identifier = task_id(instruction, root)
        execution_id = str(uuid.uuid4())
        _write({
            "type": "command.accepted",
            "schema_version": 1,
            "command_id": command_identifier,
            "accepted": True,
            "task_id": identifier,
        })
        _write({
            "type": "task.created",
            "schema_version": 1,
            "event_id": str(uuid.uuid4()),
            "task_id": identifier,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {"title": instruction},
        })

        def forward_event(event: AgentEvent) -> None:
            _write({
                "type": "core.event",
                "schema_version": 1,
                "event_id": f"{identifier}:{execution_id}:{event.sequence}",
                "task_id": identifier,
                "timestamp": event.timestamp,
                "payload": {
                    "event_type": event.event_type,
                    "message": event.message,
                    "data": event.data,
                    "sequence": event.sequence,
                    "iteration": event.iteration,
                },
            })

        try:
            state = agent.run(
                root,
                instruction,
                event_callback=forward_event,
                approval_callback=lambda plan: _approval(identifier, plan, kind="plan"),
                high_risk_approval_callback=lambda plan: _approval(identifier, plan, kind="high_risk"),
            )
            _write({
                "type": "task.completed" if state.status.value == "completed" else "task.failed",
                "schema_version": 1,
                "event_id": f"{identifier}:{execution_id}:terminal",
                "task_id": identifier,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "result_summary": f"Task ended with status {state.status.value}.",
                    "message": f"Task ended with status {state.status.value}.",
                    "recoverable": state.status.value in {"failed", "max_iterations"},
                },
            })
        except (BrokenPipeError, EOFError):
            return 0
        except Exception as exc:
            _write({
                "type": "task.failed",
                "schema_version": 1,
                "event_id": f"{identifier}:{execution_id}:terminal-error",
                "task_id": identifier,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"message": str(exc), "recoverable": True},
            })
            _write({
                "type": "runtime.error",
                "schema_version": 1,
                "task_id": identifier,
                "message": str(exc),
            })
