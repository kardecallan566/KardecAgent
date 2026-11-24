from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class ChatClient(Protocol):
    model: str

    def chat(self, messages: list[dict[str, Any]], *, temperature: float = 0.2): ...


@dataclass(frozen=True)
class RoutingDecision:
    tier: str
    model: str
    reason: str


_COMPLEX_SIGNALS = (
    "implement", "create", "build", "fix", "bug", "refactor", "debug", "code",
    "repository", "project", "test", "security", "architecture", "migrate",
    "optimize", "traceback", "compile", "function", "class", "api", "feature",
    "plan", "change", "modify", "update", "crie", "criar", "implemente",
    "implementar", "corrija", "corrigir", "refatore", "refatorar", "projeto",
    "código", "codigo", "arquivo", "arquivos", "teste", "testes", "segurança",
    "seguranca", "arquitetura", "migre", "migrar", "otimize", "otimizar",
    "erro", "aplicativo", "função", "funcao", "classe", "recurso", "plano",
    "altere", "alterar", "modifique", "modificar", "construa", "construir",
    "adicione", "adicionar",
)
_CODE_OR_PATH = re.compile(
    r"\x60\x60\x60|(?:^|\s)(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+|\b(?:def|class|function|import)\s+\w+",
    re.IGNORECASE,
)


def _latest_task_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if not isinstance(content, str):
            return str(content)
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            return content
        if isinstance(payload, dict):
            task = payload.get("task")
            if isinstance(task, str) and task.strip():
                return task
            instruction = payload.get("instruction")
            if isinstance(instruction, str) and instruction.strip():
                return instruction
        return content
    return ""


class ModelRouter:
    """Deterministic complexity router; it never sends source to an external classifier."""

    def __init__(self, primary_model: str, fast_model: str) -> None:
        self.primary_model = primary_model
        self.fast_model = fast_model

    def decide(self, messages: list[dict[str, Any]]) -> RoutingDecision:
        if not self.fast_model or self.fast_model == self.primary_model:
            return RoutingDecision("primary", self.primary_model, "fast model is not configured")

        system_text = "\n".join(
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "system"
        ).lower()
        if "planning phase" in system_text or "implementation phase" in system_text:
            return RoutingDecision("primary", self.primary_model, "agent execution phase requires the primary model")

        protocol_markers = ("complete_step", "write_file", "apply_patch", "run_command", "criteria_evidence")
        transcript = "\n".join(str(message.get("content", "")) for message in messages[-6:])
        if any(marker in transcript for marker in protocol_markers):
            return RoutingDecision("primary", self.primary_model, "agent tool protocol detected")

        task = _latest_task_text(messages).strip()
        normalized = task.casefold()
        if not normalized:
            return RoutingDecision("primary", self.primary_model, "no user task available")
        if len(task) > 600 or task.count("\n") >= 4 or _CODE_OR_PATH.search(task):
            return RoutingDecision("primary", self.primary_model, "long or code-oriented request")

        signal = next((word for word in _COMPLEX_SIGNALS if re.search(r"\\b" + re.escape(word) + r"\\b", normalized)), None)
        if signal:
            return RoutingDecision("primary", self.primary_model, f"software-work signal: {signal}")

        return RoutingDecision("fast", self.fast_model, "short conversational request")

    def chat(self, primary: ChatClient, fast: ChatClient, messages: list[dict[str, Any]], *, temperature: float = 0.2):
        decision = self.decide(messages)
        selected = fast if decision.tier == "fast" else primary
        fallback_reason = ""
        try:
            response = selected.chat(messages, temperature=temperature)
        except Exception as exc:
            if decision.tier != "fast":
                raise
            fallback_reason = f"fast model failed: {type(exc).__name__}"
            logger.warning("Fast model %s failed; falling back to %s: %s", self.fast_model, self.primary_model, exc)
            response = primary.chat(messages, temperature=temperature)
            decision = RoutingDecision("primary", self.primary_model, fallback_reason)

        raw = dict(getattr(response, "raw", {}) or {})
        raw["_kardecagent_routing"] = {
            "tier": decision.tier,
            "model": decision.model,
            "reason": decision.reason,
            "fallback": bool(fallback_reason),
        }
        try:
            return replace(response, raw=raw)
        except TypeError:
            response.raw = raw
            return response


class AdaptiveLLMClient:
    """Drop-in chat client that routes short conversation to a fast model."""

    def __init__(self, primary: ChatClient, fast: ChatClient) -> None:
        self.primary = primary
        self.fast = fast
        self.model = primary.model
        self.router = ModelRouter(primary.model, fast.model)

    def chat(self, messages: list[dict[str, Any]], *, temperature: float = 0.2):
        return self.router.chat(self.primary, self.fast, messages, temperature=temperature)
