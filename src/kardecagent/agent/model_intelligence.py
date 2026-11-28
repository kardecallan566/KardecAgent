from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ModelCapability:
    model: str
    strengths: tuple[str, ...] = ()
    context_window: int | None = None
    tool_calling: bool | None = None
    reasoning_level: str = "unknown"
    latency_tier: str = "unknown"
    available: bool = True
    metadata_source: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["strengths"] = list(self.strengths)
        return result


@dataclass(frozen=True)
class ModelRecommendation:
    model: str
    score: int
    matched_strengths: tuple[str, ...]
    reason: str
    capability_confidence: str
    requires_approval: bool = True

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["matched_strengths"] = list(self.matched_strengths)
        return result


class ModelCapabilityCatalog:
    """Conservative inventory and deterministic model-to-subtask recommender.

    Only explicit provider/client metadata is treated as a capability. Unknown
    values stay unknown; model names are never used to invent context or skill data.
    """

    def __init__(self, capabilities: Iterable[ModelCapability]) -> None:
        unique: dict[str, ModelCapability] = {}
        for item in capabilities:
            unique.setdefault(item.model, item)
        self.capabilities = tuple(unique.values())

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
        return value

    @classmethod
    def _from_client(cls, client: Any) -> ModelCapability:
        model = str(getattr(client, "model", "unknown") or "unknown")
        raw = getattr(client, "model_capabilities", None)
        if not isinstance(raw, dict):
            raw = getattr(client, "capabilities", None)
        if not isinstance(raw, dict):
            raw = {}
        strengths = raw.get("strengths", ())
        if not isinstance(strengths, (list, tuple, set)):
            strengths = ()
        normalized = tuple(sorted({
            str(value).strip().casefold()
            for value in strengths
            if isinstance(value, str) and value.strip()
        }))
        tool_calling = raw.get("tool_calling")
        if not isinstance(tool_calling, bool):
            tool_calling = None
        reasoning = raw.get("reasoning_level")
        if reasoning not in {"basic", "standard", "advanced", "expert"}:
            reasoning = "unknown"
        latency = raw.get("latency_tier")
        if latency not in {"fast", "balanced", "slow"}:
            latency = "unknown"
        return ModelCapability(
            model=model,
            strengths=normalized,
            context_window=cls._positive_int(raw.get("context_window")),
            tool_calling=tool_calling,
            reasoning_level=reasoning,
            latency_tier=latency,
            available=bool(getattr(client, "available", True)),
            metadata_source="provider" if raw else "unknown",
        )

    @classmethod
    def from_clients(cls, clients: Iterable[Any]) -> "ModelCapabilityCatalog":
        return cls(cls._from_client(client) for client in clients)

    def inventory(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self.capabilities]

    @staticmethod
    def _requested_strengths(objective: str, role: str) -> set[str]:
        text = f"{role} {objective}".casefold()
        requested: set[str] = set()
        rules = {
            "coding": ("implement", "code", "refactor", "program", "bug", "implementação", "código", "corrigir"),
            "reasoning": ("architecture", "architect", "design", "analyze", "analysis", "architecture", "arquitet", "planejar", "analisar"),
            "testing": ("test", "qa", "quality", "verify", "validation", "teste", "validar", "verificar"),
            "review": ("review", "audit", "security", "revis", "auditoria", "segurança"),
            "documentation": ("document", "readme", "docs", "documentação"),
            "vision": ("image", "screenshot", "visual", "imagem", "captura"),
        }
        for strength, terms in rules.items():
            if any(term in text for term in terms):
                requested.add(strength)
        return requested

    def recommend(self, *, objective: str, role: str = "general") -> ModelRecommendation:
        candidates = [item for item in self.capabilities if item.available]
        if not candidates:
            return ModelRecommendation(
                model="configured-primary", score=0, matched_strengths=(),
                reason="No available model inventory; retain the configured primary model.",
                capability_confidence="none",
            )
        requested = self._requested_strengths(objective, role)
        ranked = []
        for index, item in enumerate(candidates):
            matched = tuple(sorted(requested.intersection(item.strengths)))
            score = len(matched) * 10
            if "reasoning" in requested and item.reasoning_level in {"advanced", "expert"}:
                score += 3
            if "testing" in requested and item.tool_calling is True:
                score += 2
            ranked.append((score, -index, item, matched))
        score, _, selected, matched = max(ranked, key=lambda row: (row[0], row[1]))
        confidence = "provider_metadata" if selected.metadata_source == "provider" else "low"
        if not matched:
            reason = "No declared capability matched this subtask; retain the configured primary model."
            confidence = "low"
        else:
            reason = "Selected using explicitly declared capability metadata: " + ", ".join(matched) + "."
        return ModelRecommendation(
            model=selected.model, score=score, matched_strengths=matched,
            reason=reason, capability_confidence=confidence,
        )
