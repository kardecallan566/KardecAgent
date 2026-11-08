import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kardecagent import studio_protocol
from kardecagent.agent.state import AgentEvent, TaskStatus


def test_read_message_rejects_oversized_line_and_recovers(monkeypatch):
    monkeypatch.setattr(
        studio_protocol.sys,
        "stdin",
        io.StringIO("x" * (studio_protocol.MAX_MESSAGE_BYTES + 10) + "\n{}\n"),
    )
    with pytest.raises(studio_protocol.StudioProtocolError, match="64 KiB"):
        studio_protocol._read_message()
    assert studio_protocol._read_message() == {}


def test_write_compacts_oversized_event(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(studio_protocol.sys, "stdout", output)
    studio_protocol._write({
        "type": "core.event",
        "payload": {"data": {"large": "x" * studio_protocol.MAX_EVENT_BYTES}},
    })
    encoded = output.getvalue().strip()
    message = json.loads(encoded)
    assert len(encoded.encode("utf-8")) <= studio_protocol.MAX_EVENT_BYTES
    assert message["payload"]["data"] == {"truncated": True}


def test_run_stdio_forwards_real_state_events_and_execution_identity(monkeypatch, tmp_path):
    command = {
        "type": "task.submit",
        "schema_version": 1,
        "command_id": "command-1",
        "payload": {"instruction": "inspect this project"},
    }
    monkeypatch.setattr(studio_protocol.sys, "stdin", io.StringIO(json.dumps(command) + "\n"))
    output = io.StringIO()
    monkeypatch.setattr(studio_protocol.sys, "stdout", output)
    monkeypatch.setattr(studio_protocol, "_approval", lambda *args, **kwargs: True)

    class FakeAgent:
        settings = SimpleNamespace(max_iterations=4, max_command_output_chars=1200, llm_timeout_seconds=30.0, command_timeout_seconds=15.0)

        def run(self, root, instruction, *, event_callback, approval_callback, high_risk_approval_callback):
            event_callback(AgentEvent(1, "plan_created", "Plan created", {"steps": 2}, sequence=1))
            return SimpleNamespace(status=TaskStatus.COMPLETED)

    assert studio_protocol.run_stdio(Path(tmp_path), FakeAgent()) == 0
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    ready = next(item for item in messages if item["type"] == "runtime.ready")
    created = next(item for item in messages if item["type"] == "task.created")
    core_event = next(item for item in messages if item["type"] == "core.event")
    terminal = next(item for item in messages if item["type"] == "task.completed")

    assert ready["limits"]["max_concurrent_tasks"] == 1
    assert ready["limits"]["max_concurrent_subtasks"] == 1
    assert created["execution_id"]
    assert core_event["execution_id"] == created["execution_id"]
    assert terminal["execution_id"] == created["execution_id"]
    assert core_event["payload"]["event_type"] == "plan_created"
