from pathlib import Path

from kardecagent.agent.loop import AgentLoop
from kardecagent.config import Settings
from kardecagent.llm.client import LLMResponse


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.messages = []

    def chat(self, messages: list[dict]) -> LLMResponse:
        self.messages.append(messages)
        try:
            return LLMResponse(next(self.responses), {})
        except StopIteration:
            return LLMResponse('{"tool":"run_checks","arguments":{"kind":"test"}}', {})


def _plan(steps=("Finish validation",)) -> str:
    return '{"summary":"Do task","steps":' + str(list(steps)).replace("'", '"') + ',"validation":["Run checks"],"risks":[],"completion_criteria":["result is correct"]}'


def test_finish_requires_verification(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    (tmp_path / "test_example.py").write_text(
        "def test_example():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    llm = FakeLLM([
        _plan(),
        '{"tool":"complete_step","arguments":{"step":1,"evidence":"tests are present"},"plan_step":1}',
        '{"tool":"finish","arguments":{"reason":"implemented","criteria_evidence":["pytest passed"]}}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=3)).run(
        tmp_path, "verify task", approval_callback=lambda plan: True
    )
    assert state.status.value == "completed"
    assert any(event.event_type == "verification" for event in state.events)
    assert any(event.event_type == "plan_step_completed" for event in state.events)


def test_failed_verification_returns_to_model(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    (tmp_path / "test_example.py").write_text(
        "def test_example():\n    assert False\n",
        encoding="utf-8",
    )
    llm = FakeLLM([
        _plan(),
        '{"tool":"complete_step","arguments":{"step":1,"evidence":"attempted"},"plan_step":1}',
        '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":["verified"]}}',
        '{"tool":"finish","arguments":{"reason":"done again","criteria_evidence":["verified"]}}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=4)).run(
        tmp_path, "fix task", approval_callback=lambda plan: True
    )
    assert state.status.value == "max_iterations"
    assert any(event.event_type == "verification_failed" for event in state.events)


def test_static_html_finish_is_verified(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html lang='pt-BR'><head>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Site</title></head><body><main>OK</main></body></html>",
        encoding="utf-8",
    )
    llm = FakeLLM([
        _plan(),
        '{"tool":"complete_step","arguments":{"step":1,"evidence":"HTML created"},"plan_step":1}',
        '{"tool":"finish","arguments":{"reason":"site implemented","criteria_evidence":["HTML validator passed"]}}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=3)).run(
        tmp_path, "create site", approval_callback=lambda plan: True
    )
    assert state.status.value == "completed"


def test_plan_must_be_approved_before_execution(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    llm = FakeLLM([_plan()])
    state = AgentLoop(llm, Settings(max_iterations=1)).run(
        tmp_path, "do task", approval_callback=lambda plan: False
    )
    assert state.status.value == "failed"
    assert any(event.event_type == "plan_created" for event in state.events)
    assert any(
        event.event_type == "plan_approval" and event.data.get("approved") is False
        for event in state.events
    )
    assert not any(event.event_type == "execution_started" for event in state.events)
    assert not any(event.event_type == "checkpoint_created" for event in state.events)


def test_approved_plan_starts_execution_and_checkpoint_after_approval(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    llm = FakeLLM([
        _plan(),
        '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":["verified"]}}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=1)).run(
        tmp_path, "do task", approval_callback=lambda plan: True
    )
    assert any(event.event_type == "execution_started" for event in state.events)
    assert not any(event.event_type == "checkpoint_created" for event in state.events)


def test_plan_scope_violation_is_rejected(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    llm = FakeLLM([
        _plan(),
        '{"tool":"write_file","arguments":{"path":"x.txt","content":"x"},"plan_step":2}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=2)).run(
        tmp_path, "do task", approval_callback=lambda plan: True
    )
    assert any(event.event_type == "plan_scope_violation" for event in state.events)
    assert not (tmp_path / "x.txt").exists()


def test_plan_change_requires_second_approval(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    responses = [
        _plan(("Create file",)),
        '{"tool":"request_plan_change","arguments":{"plan":{"summary":"Expanded","steps":["Create file","Add test"],"validation":["Run tests"],"risks":["Extra scope"],"completion_criteria":["result works"]}}}',
    ]
    approvals = []
    llm = FakeLLM(responses)

    def approve(plan):
        approvals.append(plan.summary)
        return True

    state = AgentLoop(llm, Settings(max_iterations=2)).run(
        tmp_path, "do task", approval_callback=approve
    )
    assert approvals == ["Do task", "Expanded"]
    assert any(event.event_type == "plan_change_proposed" for event in state.events)
    assert any(event.event_type == "plan_change_approval" for event in state.events)
    assert any(event.event_type == "plan_changed" for event in state.events)


def test_rejected_plan_change_keeps_original_plan(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    llm = FakeLLM([
        _plan(("Original step",)),
        '{"tool":"request_plan_change","arguments":{"plan":{"summary":"Expanded","steps":["Original step","New step"],"validation":["Run tests"],"risks":[],"completion_criteria":["result works"]}}}',
    ])
    approvals = []

    def approve(plan):
        approvals.append(plan.summary)
        return len(approvals) == 1

    state = AgentLoop(llm, Settings(max_iterations=2)).run(
        tmp_path, "do task", approval_callback=approve
    )
    assert approvals == ["Do task", "Expanded"]
    assert any(
        event.event_type == "plan_change_approval" and event.data.get("approved") is False
        for event in state.events
    )


def test_finish_requires_evidence_for_every_completion_criterion(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html lang='pt-BR'><head><meta name='viewport' content='width=device-width, initial-scale=1'><title>Site</title></head><body>OK</body></html>",
        encoding="utf-8",
    )
    llm = FakeLLM([
        '{"summary":"Do task","steps":["Finish validation"],"validation":["Run checks"],"risks":[],"completion_criteria":["result is verified","tests pass"]}',
        '{"tool":"complete_step","arguments":{"step":1,"evidence":"implemented"},"plan_step":1}',
        '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":["only one"]}}',
        '{"tool":"finish","arguments":{"reason":"done","criteria_evidence":["validator passed"]}}',
    ])
    state = AgentLoop(llm, Settings(max_iterations=4)).run(
        tmp_path, "create site", approval_callback=lambda plan: True
    )
    assert state.status.value == "completed"
    assert any(event.event_type == "completion_criteria_failed" for event in state.events)


def test_intent_router_answers_current_date_without_model_guessing(tmp_path: Path):
    class ExplodingLLM:
        def chat(self, messages):
            raise AssertionError("date and time must come from the local system clock")

    result = AgentLoop(ExplodingLLM(), Settings(max_iterations=2)).interpret_message(
        tmp_path, "Que dia é hoje?"
    )
    assert result["intent"] == "chat"
    assert "Hoje é" in result["response"]
    assert any(month in result["response"] for month in (
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    ))


def test_intent_router_answers_simple_greeting_without_calling_the_model(tmp_path: Path):
    class ExplodingLLM:
        def chat(self, messages):
            raise AssertionError("a plain greeting must not call the LLM")

    result = AgentLoop(ExplodingLLM(), Settings(max_iterations=2)).interpret_message(
        tmp_path, "oi"
    )
    assert result["intent"] == "chat"
    assert "Olá!" in result["response"]


def test_intent_router_does_not_turn_how_to_questions_into_tasks(tmp_path: Path):
    llm = FakeLLM([
        '{"intent":"task","response":"","reason":"The model mistook a how-to question for an action."}'
    ])
    result = AgentLoop(llm, Settings(max_iterations=2)).interpret_message(
        tmp_path, "Como criar uma tela de login neste projeto?"
    )
    assert result["intent"] == "clarify"
    assert "alterações" in result["response"]


def test_intent_router_accepts_a_polite_direct_action_request():
    assert AgentLoop._has_explicit_action_request("Pode criar uma tela de login?")
    assert AgentLoop._has_explicit_action_request("Por favor, implemente o tema escuro.")
    assert not AgentLoop._has_explicit_action_request("Como implementar o tema escuro?")


def test_intent_router_downgrades_task_without_explicit_action_verb(tmp_path: Path):
    llm = FakeLLM([
        '{"intent":"task","response":"","reason":"The model guessed a task."}'
    ])
    result = AgentLoop(llm, Settings(max_iterations=2)).interpret_message(
        tmp_path, "Preciso de uma nova tela para o aplicativo."
    )
    assert result["intent"] == "clarify"
    assert "alterações" in result["response"]


def test_intent_router_uses_project_files_and_persistent_memory(tmp_path: Path, monkeypatch):
    (tmp_path / "README.md").write_text(
        "# Atlas\nA scheduling app for independent music teachers.", encoding="utf-8"
    )
    user_memory = tmp_path / "user-memory.json"
    from kardecagent.agent.memory import MemoryStore
    MemoryStore(tmp_path, user_path=user_memory).remember(
        scope="project", category="fact",
        content="O projeto atende professores independentes de música."
    )
    llm = FakeLLM([
        '{"intent":"chat","response":"O Atlas organiza horários de professores de música.","reason":"README evidence."}'
    ])
    result = AgentLoop(llm, Settings(max_iterations=2)).interpret_message(
        tmp_path, "O que esse aplicativo faz?"
    )
    assert result["intent"] == "chat"
    sent_context = json.loads(llm.messages[-1][-1]["content"])
    assert "A scheduling app for independent music teachers." in sent_context["important_file_contents"][0]["content"]
    assert sent_context["persistent_memory"][0]["content"].startswith("O projeto atende")


def test_intent_router_persists_explicit_memory_updates(tmp_path: Path, monkeypatch):
    from kardecagent.agent.memory import MemoryStore
    monkeypatch.setattr(
        "kardecagent.agent.loop.MemoryStore",
        lambda root: MemoryStore(root, user_path=tmp_path / "user-memory.json"),
    )
    llm = FakeLLM([
        '{"intent":"chat","response":"Vou considerar isso nas próximas conversas.","reason":"Preference stated.","memory_updates":[{"scope":"user","category":"preference","content":"Prefiro explicações técnicas em português."}]}'
    ])
    result = AgentLoop(llm, Settings(max_iterations=2)).interpret_message(
        tmp_path, "Prefiro explicações técnicas em português."
    )
    assert result["intent"] == "chat"
    items = MemoryStore(tmp_path, user_path=tmp_path / "user-memory.json").all_items()
    assert len(items) == 1
    assert items[0]["scope"] == "user"


def test_intent_router_answers_questions_without_starting_a_task(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Fixture\nA tiny example project.", encoding="utf-8")
    llm = FakeLLM([
        '{"intent":"chat","response":"Este projeto é um exemplo pequeno.","reason":"The user asked for an explanation."}'
    ])
    result = AgentLoop(llm, Settings(max_iterations=2)).interpret_message(
        tmp_path, "O que esse projeto faz?"
    )
    assert result["intent"] == "chat"
    assert "exemplo" in result["response"]


def test_intent_router_only_marks_explicit_change_requests_as_tasks(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Fixture", encoding="utf-8")
    llm = FakeLLM([
        '{"intent":"task","response":"","reason":"The user explicitly requested implementation."}'
    ])
    result = AgentLoop(llm, Settings(max_iterations=2)).interpret_message(
        tmp_path, "Crie uma tela de login neste projeto."
    )
    assert result["intent"] == "task"
    assert result["response"] == ""


def test_intent_router_asks_for_clarification_when_request_is_ambiguous(tmp_path: Path):
    llm = FakeLLM([
        '{"intent":"clarify","response":"Você quer apenas sugestões ou quer que eu altere os arquivos?","reason":"The requested outcome is unclear."}'
    ])
    result = AgentLoop(llm, Settings(max_iterations=2)).interpret_message(
        tmp_path, "Melhore isso."
    )
    assert result["intent"] == "clarify"
    assert "arquivos" in result["response"]
