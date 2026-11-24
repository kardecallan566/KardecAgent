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
        "payload": {"instruction": "inspect this project", "project_root": str(tmp_path)},
    }
    monkeypatch.setattr(studio_protocol.sys, "stdin", io.StringIO(json.dumps(command) + "\n" + json.dumps(command) + "\n"))
    output = io.StringIO()
    monkeypatch.setattr(studio_protocol.sys, "stdout", output)
    monkeypatch.setattr(studio_protocol, "_approval", lambda *args, **kwargs: True)

    class FakeAgent:
        settings = SimpleNamespace(max_iterations=4, max_command_output_chars=1200, llm_timeout_seconds=30.0, command_timeout_seconds=15.0)

        def __init__(self):
            self.calls = 0

        def run(self, root, instruction, *, event_callback, approval_callback, high_risk_approval_callback, **kwargs):
            self.calls += 1
            event_callback(AgentEvent(1, "plan_created", "Plan created", {"steps": 2}, sequence=1))
            return SimpleNamespace(status=TaskStatus.COMPLETED, events=[])

    fake_agent = FakeAgent()
    assert studio_protocol.run_stdio(Path(tmp_path), fake_agent) == 0
    assert fake_agent.calls == 1
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    ready = next(item for item in messages if item["type"] == "runtime.ready")
    created = next(item for item in messages if item["type"] == "task.created")
    core_event = next(item for item in messages if item["type"] == "core.event")
    terminal = next(item for item in messages if item["type"] == "task.completed")
    rejected = next(item for item in messages if item["type"] == "command.rejected")

    assert rejected["reason"] == "command_id has already been processed."
    assert ready["limits"]["max_concurrent_tasks"] == 1
    assert ready["limits"]["max_concurrent_subtasks"] == 1
    assert created["execution_id"]
    assert core_event["execution_id"] == created["execution_id"]
    assert terminal["execution_id"] == created["execution_id"]
    assert core_event["payload"]["event_type"] == "plan_created"


def test_run_stdio_restores_persisted_task_history_without_resuming(monkeypatch, tmp_path):
    persisted_state = SimpleNamespace(
        task="continue implementing the feature",
        status=TaskStatus.RUNNING,
        events=[AgentEvent(2, "plan_created", "Approved plan", {"plan": {"steps": ["Implement"]}}, sequence=1)],
    )
    fake_store = SimpleNamespace(
        list_recent=lambda limit: [("persisted-task-id", persisted_state, None, None)]
    )
    monkeypatch.setattr(studio_protocol, "TaskStore", lambda root: fake_store)
    monkeypatch.setattr(studio_protocol.sys, "stdin", io.StringIO(""))
    output = io.StringIO()
    monkeypatch.setattr(studio_protocol.sys, "stdout", output)

    class FakeAgent:
        settings = SimpleNamespace(max_iterations=4, max_command_output_chars=1200, llm_timeout_seconds=30.0, command_timeout_seconds=15.0)

    assert studio_protocol.run_stdio(Path(tmp_path), FakeAgent()) == 0
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    restored = next(item for item in messages if item["type"] == "task.created")
    restored_event = next(item for item in messages if item["type"] == "core.event")
    terminal = next(item for item in messages if item["type"] == "task.failed")

    assert restored["task_id"] == "persisted-task-id"
    assert restored["payload"]["restored"] is True
    assert restored_event["payload"]["event_type"] == "plan_created"
    assert restored_event["payload"]["restored"] is True
    assert terminal["payload"]["recoverable"] is True
    assert "interrompida" in terminal["payload"]["message"].lower()


def test_run_stdio_resumes_a_persisted_task_and_streams_new_events(monkeypatch, tmp_path):
    persisted_state = SimpleNamespace(
        task="resume this task",
        status=TaskStatus.RUNNING,
        events=[AgentEvent(1, "plan_created", "Previously approved plan", {}, sequence=1)],
    )
    fake_store = SimpleNamespace(
        list_recent=lambda limit: [("persisted-task-id", persisted_state, None, None)]
    )
    monkeypatch.setattr(studio_protocol, "TaskStore", lambda root: fake_store)
    command = {
        "type": "task.resume",
        "schema_version": 1,
        "command_id": "resume-command",
        "payload": {"task_id": "persisted-task-id", "project_root": str(tmp_path)},
    }
    monkeypatch.setattr(studio_protocol.sys, "stdin", io.StringIO(json.dumps(command) + "\n"))
    output = io.StringIO()
    monkeypatch.setattr(studio_protocol.sys, "stdout", output)

    class FakeAgent:
        settings = SimpleNamespace(max_iterations=4, max_command_output_chars=1200, llm_timeout_seconds=30.0, command_timeout_seconds=15.0)

        def resume(self, root, task, *, event_callback=None):
            assert task == "resume this task"
            event = AgentEvent(2, "resume_started", "Resume started", {}, sequence=2)
            event_callback(event)
            return SimpleNamespace(
                status=TaskStatus.COMPLETED,
                events=[persisted_state.events[0], event],
            )

    assert studio_protocol.run_stdio(Path(tmp_path), FakeAgent()) == 0
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    resumed = [item for item in messages if item["type"] == "task.created" and item.get("payload", {}).get("resumed")]
    assert len(resumed) == 1
    execution_id = resumed[0]["execution_id"]
    accepted = next(item for item in messages if item.get("command_id") == "resume-command")
    history_event = next(item for item in messages if item["type"] == "core.event" and item.get("execution_id") == execution_id and item["payload"].get("restored"))
    streamed = next(item for item in messages if item["type"] == "core.event" and item["payload"].get("resumed"))
    terminal = next(item for item in messages if item["type"] == "task.completed" and item.get("execution_id") == execution_id)

    assert accepted["type"] == "command.accepted"
    assert history_event["execution_id"] == execution_id
    assert history_event["payload"]["event_type"] == "plan_created"
    assert streamed["execution_id"] == execution_id
    assert streamed["payload"]["event_type"] == "resume_started"
    assert terminal["payload"]["resumed"] is True


def test_run_stdio_rejects_resume_for_unknown_persisted_task(monkeypatch, tmp_path):
    monkeypatch.setattr(
        studio_protocol,
        "TaskStore",
        lambda root: SimpleNamespace(list_recent=lambda limit: []),
    )
    command = {
        "type": "task.resume",
        "schema_version": 1,
        "command_id": "resume-missing",
        "payload": {"task_id": "missing-task", "project_root": str(tmp_path)},
    }
    monkeypatch.setattr(studio_protocol.sys, "stdin", io.StringIO(json.dumps(command) + "\n"))
    output = io.StringIO()
    monkeypatch.setattr(studio_protocol.sys, "stdout", output)

    class FakeAgent:
        settings = SimpleNamespace(max_iterations=4, max_command_output_chars=1200, llm_timeout_seconds=30.0, command_timeout_seconds=15.0)

    assert studio_protocol.run_stdio(Path(tmp_path), FakeAgent()) == 0
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    rejected = next(item for item in messages if item.get("command_id") == "resume-missing")

    assert rejected["type"] == "command.rejected"
    assert "not found" in rejected["reason"].lower()
    assert not any(item["type"] == "task.created" for item in messages)


def test_write_uses_ascii_safe_jsonl_while_preserving_unicode(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(studio_protocol.sys, "stdout", output)
    studio_protocol._write({"type": "assistant.response", "payload": {"message": "ação concluída — café ☕"}})
    encoded = output.getvalue().strip()
    assert encoded.isascii()
    assert json.loads(encoded)["payload"]["message"] == "ação concluída — café ☕"




def test_repair_mojibake_preserves_unicode_and_fixes_corrupted_portuguese():
    assert studio_protocol._repair_mojibake("Hoje Ã© dia de aÃ§Ã£o.") == "Hoje é dia de ação."
    assert studio_protocol._repair_mojibake("Olá, você!") == "Olá, você!"


def test_local_date_context_uses_current_local_calendar_date():
    context = studio_protocol._local_date_context()
    now = studio_protocol.datetime.now().astimezone()
    assert context["date_pt_br"] == (
        f"{now.day:02d} de {studio_protocol._PT_MONTHS[now.month - 1]} de {now.year}"
    )
    assert context["iso_datetime"].endswith(("+00:00",)) or "T" in context["iso_datetime"]
    assert context["timezone"]



def test_chat_confirms_file_access_only_after_successful_workspace_read(monkeypatch, tmp_path):
    (tmp_path / "loucura.py").write_text("print('olá')\n", encoding="utf-8")
    command = {
        "type": "chat.message", "schema_version": 1, "command_id": "file-access-check",
        "payload": {"message": "Você consegue acessar o arquivo loucura.py?"},
    }
    monkeypatch.setattr(studio_protocol.sys, "stdin", io.StringIO(json.dumps(command) + "\n"))
    output = io.StringIO()
    monkeypatch.setattr(studio_protocol.sys, "stdout", output)

    class FakeLLM:
        def chat(self, *args, **kwargs):
            raise AssertionError("A confirmação simples deve usar a leitura real, sem depender do modelo.")

    class FakeAgent:
        settings = SimpleNamespace(max_iterations=4, max_command_output_chars=1200, llm_timeout_seconds=30.0, command_timeout_seconds=15.0)
        llm = FakeLLM()

    assert studio_protocol.run_stdio(tmp_path, FakeAgent()) == 0
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    answer = next(item for item in messages if item["type"] == "assistant.response")
    assert "Sim. Consegui acessar loucura.py" in answer["payload"]["message"]



def test_repair_mojibake_handles_repeated_windows_1252_corruption():
    assert studio_protocol._repair_mojibake("Hoje ÃƒÂ© dia 01 de janeiro.") == "Hoje é dia 01 de janeiro."
    assert studio_protocol._repair_mojibake("nÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â£o tenho") == "não tenho"
    corrupted = "ExecuÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â§ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â£o"
    assert studio_protocol._repair_mojibake(corrupted) == "Execução"


def test_date_question_uses_local_clock_without_calling_model(monkeypatch, tmp_path):
    command = {
        "type": "chat.message", "schema_version": 1, "command_id": "date-question",
        "payload": {"message": "que dia é hoje?"},
    }
    monkeypatch.setattr(studio_protocol.sys, "stdin", io.StringIO(json.dumps(command) + "\\n"))
    output = io.StringIO()
    monkeypatch.setattr(studio_protocol.sys, "stdout", output)

    class FakeLLM:
        def chat(self, *args, **kwargs):
            raise AssertionError("A data deve vir do relógio local, não do modelo.")

    class FakeAgent:
        settings = SimpleNamespace(max_iterations=4, max_command_output_chars=1200, llm_timeout_seconds=30.0, command_timeout_seconds=15.0)
        llm = FakeLLM()

    assert studio_protocol.run_stdio(tmp_path, FakeAgent()) == 0
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    answer = next(item for item in messages if item["type"] == "assistant.response")
    assert answer["payload"]["message"] == "Hoje é " + studio_protocol._local_date_context()["date_pt_br"] + "."


def test_project_overview_returns_real_workspace_inventory(monkeypatch, tmp_path):
    (tmp_path / "README.md").write_text("# Projeto de teste\\nUm app local.", encoding="utf-8")
    (tmp_path / "loucura.py").write_text("print('olá')\\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main(): pass\\n", encoding="utf-8")
    command = {
        "type": "chat.message", "schema_version": 1, "command_id": "project-overview",
        "payload": {"message": "oque tem nesse projeto?"},
    }
    monkeypatch.setattr(studio_protocol.sys, "stdin", io.StringIO(json.dumps(command) + "\\n"))
    output = io.StringIO()
    monkeypatch.setattr(studio_protocol.sys, "stdout", output)

    class FakeLLM:
        def chat(self, *args, **kwargs):
            raise AssertionError("O inventário do workspace deve ser respondido com dados reais.")

    class FakeAgent:
        settings = SimpleNamespace(max_iterations=4, max_command_output_chars=1200, llm_timeout_seconds=30.0, command_timeout_seconds=15.0)
        llm = FakeLLM()

    assert studio_protocol.run_stdio(tmp_path, FakeAgent()) == 0
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    answer = next(item for item in messages if item["type"] == "assistant.response")
    assert "Consegui acessar o workspace" in answer["payload"]["message"]
    assert "README.md" in answer["payload"]["message"]
    assert "src" in answer["payload"]["message"]
    assert "Resumo do README" in answer["payload"]["message"]


def test_chat_message_includes_real_workspace_tree_and_requested_file(monkeypatch, tmp_path):
    target = tmp_path / "loucura.py"
    target.write_text("def soma(a, b):\n    return a + b\n", encoding="utf-8")
    command = {
        "type": "chat.message",
        "schema_version": 1,
        "command_id": "chat-inspect-file",
        "payload": {
            "message": "Você consegue ver o que tem no arquivo loucura.py?",
            "active_editor": {"path": "loucura.py", "content": "def soma(a, b):\n    return a + b\n", "dirty": True},
        },
    }
    monkeypatch.setattr(studio_protocol.sys, "stdin", io.StringIO(json.dumps(command) + "\n"))
    output = io.StringIO()
    monkeypatch.setattr(studio_protocol.sys, "stdout", output)

    class FakeLLM:
        def chat(self, messages, temperature=0.3):
            context = json.loads(messages[1]["content"].split("JSON): ", 1)[1])
            assert "loucura.py" in context["project_files"]
            assert context["inspected_files"][0]["path"] == "loucura.py"
            assert "def soma" in context["inspected_files"][0]["content"]
            assert context["current_local_datetime"]["date_pt_br"]
            assert context["active_editor"]["path"] == "loucura.py"
            assert context["active_editor"]["dirty"] is True
            assert "def soma" in context["active_editor"]["content"]
            return SimpleNamespace(content="O arquivo define a função soma.")

    class FakeAgent:
        settings = SimpleNamespace(max_iterations=4, max_command_output_chars=1200, llm_timeout_seconds=30.0, command_timeout_seconds=15.0)
        llm = FakeLLM()

    assert studio_protocol.run_stdio(tmp_path, FakeAgent()) == 0
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    answer = next(item for item in messages if item["type"] == "assistant.response")
    assert "função soma" in answer["payload"]["message"]
