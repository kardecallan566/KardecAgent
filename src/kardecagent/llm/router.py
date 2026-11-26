from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Protocol
from threading import RLock

logger = logging.getLogger(__name__)


class ChatClient(Protocol):
    model: str

    def chat(self, messages: list[dict[str, Any]], *, temperature: float = 0.2): ...


@dataclass(frozen=True)
class RoutingDecision:
    tier: str
    model: str
    reason: str

@dataclass(frozen=True)
class ModelSwitchRequest:
    current_model: str
    target_model: str
    reason: str
    trigger: str = "capability"

    @property
    def security_level(self) -> str:
        return "normal"

    def format_for_review(self) -> str:
        return (
            "Solicitação de troca de modelo\n"
            f"Modelo atual: {self.current_model}\n"
            f"Modelo sugerido: {self.target_model}\n"
            f"Motivo: {self.reason}\n"
            "A conversa e as evidências da tarefa serão preservadas. "
            "Nenhuma troca ocorrerá sem aprovação."
        )


class EscalatingLLMClient:
    """Bounded model escalation that never changes models without explicit consent."""

    def __init__(self, primary: ChatClient, alternatives: list[ChatClient] | None = None) -> None:
        self.clients = [primary, *(alternatives or [])]
        self.active_index = 0
        self.approval_callback: Callable[[ModelSwitchRequest], bool] | None = None
        self._lock = RLock()
        self.last_switch: dict[str, str] | None = None

    @property
    def model(self) -> str:
        return str(getattr(self.clients[self.active_index], "model", "unknown"))

    def set_approval_callback(self, callback: Callable[[ModelSwitchRequest], bool] | None) -> None:
        with self._lock:
            self.approval_callback = callback
            for client in self.clients:
                setter = getattr(client, "set_approval_callback", None)
                if callable(setter):
                    setter(callback)

    def request_escalation(self, reason: str, *, trigger: str = "capability") -> tuple[bool, ModelSwitchRequest | None]:
        with self._lock:
            if self.active_index + 1 >= len(self.clients):
                return False, None
            current = self.model
            target = str(getattr(self.clients[self.active_index + 1], "model", "unknown"))
            request = ModelSwitchRequest(current, target, reason, trigger)
            callback = self.approval_callback
            if callback is None or not bool(callback(request)):
                return False, request
            self.active_index += 1
            self.last_switch = {
                "from_model": current,
                "to_model": target,
                "reason": reason,
                "trigger": trigger,
            }
            return True, request

    def chat(self, messages: list[dict[str, Any]], *, temperature: float = 0.2):
        # Serialize model calls so an approved switch cannot change the active
        # model underneath another parallel subtask's in-flight request.
        with self._lock:
            while True:
                client = self.clients[self.active_index]
                try:
                    response = client.chat(messages, temperature=temperature)
                    raw = dict(getattr(response, "raw", {}) or {})
                    if self.last_switch:
                        raw["_kardecagent_escalation"] = dict(self.last_switch)
                        self.last_switch = None
                    try:
                        return replace(response, raw=raw)
                    except TypeError:
                        response.raw = raw
                        return response
                except Exception as exc:
                    approved, _request = self.request_escalation(
                        f"Falha do modelo {self.model}: {type(exc).__name__}: {exc}",
                        trigger="provider_error",
                    )
                    if not approved:
                        raise


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

        signal = next((word for word in _COMPLEX_SIGNALS if re.search(r"\b" + re.escape(word) + r"\b", normalized)), None)
        if signal:
            return RoutingDecision("primary", self.primary_model, f"software-work signal: {signal}")

        return RoutingDecision("fast", self.fast_model, "short conversational request")

    def chat(self, primary: ChatClient, fast: ChatClient, messages: list[dict[str, Any]], *, temperature: float = 0.2):
        decision = self.decide(messages)
        logger.info("Model route: tier=%s model=%s reason=%s", decision.tier, decision.model, decision.reason)
        selected = fast if decision.tier == "fast" else primary
        fallback_reason = ""
        try:
            response = selected.chat(messages, temperature=temperature)
        except Exception:
            # A route change is a user-visible decision. The adaptive client
            # owns the approval gate and may retry on the primary model.
            raise

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
        self.fast_disabled_reason: str | None = None
        self.approval_callback: Callable[[ModelSwitchRequest], bool] | None = None

    def set_approval_callback(self, callback: Callable[[ModelSwitchRequest], bool] | None) -> None:
        self.approval_callback = callback

    def chat(self, messages: list[dict[str, Any]], *, temperature: float = 0.2):
        decision = self.router.decide(messages)
        if decision.tier == "primary":
            return self.primary.chat(messages, temperature=temperature)
        if self.fast_disabled_reason:
            return self.primary.chat(messages, temperature=temperature)
        try:
            response = self.fast.chat(messages, temperature=temperature)
        except Exception as exc:
            request = ModelSwitchRequest(
                self.fast.model,
                self.primary.model,
                f"O modelo rápido falhou: {type(exc).__name__}: {exc}",
                trigger="provider_error",
            )
            if self.approval_callback is None or not self.approval_callback(request):
                raise RuntimeError("Model switch was not approved; the fast-model failure was not retried.") from exc
            self.fast_disabled_reason = str(exc)
            response = self.primary.chat(messages, temperature=temperature)
            raw = dict(getattr(response, "raw", {}) or {})
            raw["_kardecagent_routing"] = {
                "tier": "primary",
                "model": self.primary.model,
                "reason": request.reason,
                "fallback": True,
            }
            raw["_kardecagent_escalation"] = {
                "from_model": self.fast.model,
                "to_model": self.primary.model,
                "reason": request.reason,
                "trigger": "provider_error",
            }
            return replace(response, raw=raw)
        raw = dict(getattr(response, "raw", {}) or {})
        raw["_kardecagent_routing"] = {
            "tier": "fast",
            "model": self.fast.model,
            "reason": decision.reason,
            "fallback": False,
        }
        return replace(response, raw=raw)
