import json
from pathlib import Path

from kardecagent.agent.loop import AgentLoop
from kardecagent.config import Settings
from kardecagent.llm.cleaner import extract_json_payload
from kardecagent.llm.client import LLMResponse
from kardecagent.project import detect_project


class FakeLLM:
    def __init__(self, response: str):
        self.response = response

    def chat(self, messages):
        return LLMResponse(self.response, {})


def test_json_parser_accepts_control_characters_inside_model_strings():
    payload = extract_json_payload('{"intent":"chat","response":"Linha 1\nLinha 2","reason":"ok"}')
    assert payload["response"] == "Linha 1\nLinha 2"


def test_simple_arithmetic_is_answered_without_calling_the_model(tmp_path: Path):
    class ExplodingLLM:
        def chat(self, messages):
            raise AssertionError("simple arithmetic must be evaluated locally")

    result = AgentLoop(ExplodingLLM(), Settings(max_iterations=2)).interpret_message(
        tmp_path, "quanto é 5+4?"
    )
    assert result["intent"] == "chat"
    assert result["response"] == "5+4 = 9"


def test_capability_question_is_not_routed_as_a_task(tmp_path: Path):
    class ExplodingLLM:
        def chat(self, messages):
            raise AssertionError("capability questions must not be classified as tasks")

    result = AgentLoop(ExplodingLLM(), Settings(max_iterations=2)).interpret_message(
        tmp_path, "voce consegue pesquisar coisas na internet?"
    )
    assert result["intent"] == "chat"
    assert "ferramenta de busca" in result["response"]


def test_malformed_json_falls_back_to_a_chat_response(tmp_path: Path):
    result = AgentLoop(
        FakeLLM("Posso explicar como esse projeto funciona com base nos arquivos disponíveis."),
        Settings(max_iterations=2),
    ).interpret_message(tmp_path, "O que esse projeto faz?")
    assert result["intent"] == "chat"
    assert "Posso explicar" in result["response"]


def test_invalid_classifier_response_never_creates_a_task_for_a_question(tmp_path: Path):
    result = AgentLoop(
        FakeLLM('{"intent":"task","response":"","reason":"wrong"}'),
        Settings(max_iterations=2),
    ).interpret_message(tmp_path, "Proponha um plano de implementação para um app de desenho.")
    assert result["intent"] == "chat"
    assert "plano" in result["response"].lower() or "desenho" in result["response"].lower()


def test_project_detector_reports_multiple_languages(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main", encoding="utf-8")
    (tmp_path / "main.rs").write_text("fn main() {}", encoding="utf-8")

    profile = detect_project(tmp_path)

    assert profile.language == "python"
    assert set(profile.languages) >= {"python", "go", "rust"}
