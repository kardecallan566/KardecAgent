"""Deterministic adaptive task assessment and strategy recommendations."""
from __future__ import annotations
import re
from dataclasses import asdict, dataclass
from typing import Iterable

_TOKEN = re.compile(r"[a-z0-9_+#.-]{3,}", re.IGNORECASE)
_COMPLEXITY_SIGNALS = {
    "architecture": 3, "architect": 3, "security": 3, "migration": 2,
    "migrate": 2, "refactor": 2, "concurrency": 3, "parallel": 2,
    "database": 2, "backend": 2, "frontend": 2, "integration": 2,
    "debug": 2, "debugging": 2, "performance": 2, "optimize": 2,
    "implement": 2, "build": 2, "create": 1, "fix": 1, "test": 1,
    "implemente": 2, "implementar": 2, "criar": 1, "corrigir": 1,
    "refatorar": 2, "segurança": 3, "seguranca": 3, "arquitetura": 3,
    "migração": 2, "migracao": 2, "banco": 2, "depurar": 2,
    "desempenho": 2, "paralelo": 2, "integração": 2, "integracao": 2,
}
_RISK_SIGNALS = {
    "delete", "remove", "deploy", "publish", "production", "credential",
    "credentials", "secret", "permission", "payment", "auth", "authentication",
    "excluir", "remover", "publicar", "produção", "producao",
    "credencial", "segredo", "permissão", "permissao", "pagamento",
    "autenticação", "autenticacao",
}

@dataclass(frozen=True, slots=True)
class ComplexityAssessment:
    score: int
    level: str
    factors: tuple[str, ...]
    recommended_parallelism: int
    recommend_decomposition: bool
    requires_strong_model: bool
    risk_signals: tuple[str, ...]
    def as_dict(self) -> dict:
        value = asdict(self)
        value["factors"] = list(self.factors)
        value["risk_signals"] = list(self.risk_signals)
        return value

@dataclass(frozen=True, slots=True)
class StrategyRecommendation:
    action: str
    reason: str
    requires_user_approval: bool = False
    def as_dict(self) -> dict:
        return asdict(self)

class AdaptiveIntelligenceEngine:
    """Evidence-bounded heuristics; never calls models or executes actions."""
    def __init__(self, *, max_parallelism: int = 4) -> None:
        if isinstance(max_parallelism, bool) or not isinstance(max_parallelism, int) or not 1 <= max_parallelism <= 4:
            raise ValueError("max_parallelism must be an integer between 1 and 4")
        self.max_parallelism = max_parallelism

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token.casefold() for token in _TOKEN.findall(text)}

    def assess(self, task: str, *, plan_steps: Iterable[str] = (),
               project_file_count: int | None = None,
               requested_parallelism: int = 2) -> ComplexityAssessment:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a non-empty string")
        if isinstance(requested_parallelism, bool) or not isinstance(requested_parallelism, int):
            raise ValueError("requested_parallelism must be an integer")
        requested_parallelism = max(1, min(self.max_parallelism, requested_parallelism))
        steps = [item for item in plan_steps if isinstance(item, str) and item.strip()]
        tokens = self._tokens(task)
        score, factors = 0, []
        for token in sorted(tokens & _COMPLEXITY_SIGNALS.keys()):
            score += _COMPLEXITY_SIGNALS[token]
            factors.append(f"task signal: {token}")
        if len(task) >= 500:
            score += 2
            factors.append("long task description")
        elif len(task) >= 220:
            score += 1
            factors.append("moderately detailed task description")
        if len(steps) >= 5:
            score += 3
            factors.append(f"{len(steps)} approved plan steps")
        elif len(steps) >= 3:
            score += 2
            factors.append(f"{len(steps)} approved plan steps")
        elif len(steps) == 2:
            score += 1
            factors.append("multi-step approved plan")
        if project_file_count is not None:
            if isinstance(project_file_count, bool) or not isinstance(project_file_count, int) or project_file_count < 0:
                raise ValueError("project_file_count must be a non-negative integer or None")
            if project_file_count >= 500:
                score += 2
                factors.append("large project snapshot")
            elif project_file_count >= 100:
                score += 1
                factors.append("medium project snapshot")
        risks = tuple(sorted(tokens & _RISK_SIGNALS))
        if risks:
            factors.append("sensitive operation indicators present")
        level = "high" if score >= 9 else "medium" if score >= 4 else "low"
        decompose = level == "high" or (level == "medium" and len(steps) >= 4)
        return ComplexityAssessment(score, level, tuple(factors),
            requested_parallelism if decompose else 1, decompose,
            level == "high", risks)

    @staticmethod
    def recommend_after_failure(*, failure_kind: str, attempts: int,
                                same_hypothesis_repeated: bool = False,
                                verification_failed: bool = False) -> StrategyRecommendation:
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
            raise ValueError("attempts must be a positive integer")
        kind = str(failure_kind).strip().casefold()
        if same_hypothesis_repeated or attempts >= 2:
            return StrategyRecommendation("replan", "A estratégia atual não avançou; diagnosticar a causa e propor uma abordagem diferente.")
        if verification_failed:
            return StrategyRecommendation("repair_within_approved_scope", "A verificação encontrou divergências; corrigir somente dentro do escopo aprovado.")
        if kind in {"provider_error", "context_limit", "capability_limit"}:
            return StrategyRecommendation("request_model_escalation", "A capacidade ou disponibilidade do modelo atual pode ser insuficiente.", True)
        if kind in {"tool_error", "invalid_output"}:
            return StrategyRecommendation("diagnose_then_retry", "Investigar o erro específico antes de uma nova tentativa.")
        return StrategyRecommendation("request_clarification", "Não há evidência suficiente para escolher uma recuperação segura.")

    @staticmethod
    def rank_skills(task: str, skills: Iterable[dict], *, limit: int = 3) -> list[dict]:
        """Rank metadata only; SkillOrchestrator still owns consent and loading."""
        if not isinstance(task, str) or not task.strip():
            return []
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8:
            raise ValueError("limit must be between 1 and 8")
        task_tokens = AdaptiveIntelligenceEngine._tokens(task)
        ranked = []
        for skill in skills:
            if not isinstance(skill, dict):
                continue
            skill_id = skill.get("id")
            if not isinstance(skill_id, str) or not skill_id.strip():
                continue
            tags = skill.get("tags", [])
            metadata = " ".join(value for value in (
                skill.get("name"), skill.get("description"),
                " ".join(tags) if isinstance(tags, list) and all(isinstance(tag, str) for tag in tags) else ""
            ) if isinstance(value, str))
            overlap = task_tokens & AdaptiveIntelligenceEngine._tokens(metadata)
            if overlap:
                ranked.append({"skill_id": skill_id, "score": len(overlap), "matched_terms": sorted(overlap)})
        ranked.sort(key=lambda item: (-item["score"], item["skill_id"]))
        return ranked[:limit]
