from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

class PlanError(ValueError):
    pass

@dataclass(frozen=True)
class ExecutionPlan:
    summary: str
    steps: list[str]
    validation: list[str]
    risks: list[str] = field(default_factory=list)
    completion_criteria: list[str] = field(default_factory=list)
    security_level: str = "normal"
    security_requirements: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "steps": self.steps, "validation": self.validation, "risks": self.risks, "completion_criteria": self.completion_criteria, "security_level": self.security_level, "security_requirements": self.security_requirements}

    def format_for_review(self) -> str:
        lines = ["PLANO DE EXECUÇÃO", "", self.summary, "", "Etapas:"]
        lines.extend(f"{i}. {step}" for i, step in enumerate(self.steps, 1))
        lines.append("")
        lines.append("Validação:")
        lines.extend(f"- {item}" for item in self.validation)
        lines.append("")
        lines.append(f"Nível de segurança: {self.security_level}")
        if self.security_requirements:
            lines.append("Requisitos de segurança:")
            lines.extend(f"- {item}" for item in self.security_requirements)
        lines.append("")
        lines.append("Critérios de conclusão:")
        lines.extend(f"- {item}" for item in self.completion_criteria)
        if self.risks:
            lines.extend(["", "Riscos / observações:"])
            lines.extend(f"- {item}" for item in self.risks)
        return "\n".join(lines)

@dataclass
class PlanTracker:
    plan: ExecutionPlan
    current_step: int = 1
    completed_steps: list[int] = field(default_factory=list)
    started_steps: list[int] = field(default_factory=list)

    @classmethod
    def resume_from(cls, plan: ExecutionPlan, next_step: int) -> "PlanTracker":
        if not 1 <= next_step <= len(plan.steps) + 1:
            raise PlanError("resume step is outside the approved plan")
        completed = list(range(1, next_step))
        return cls(
            plan=plan,
            current_step=next_step,
            completed_steps=completed,
            started_steps=completed.copy(),
        )

    @property
    def total_steps(self) -> int:
        return len(self.plan.steps)

    @property
    def completed(self) -> bool:
        return len(self.completed_steps) == self.total_steps

    def can_start(self, step: int) -> bool:
        return step == self.current_step and 1 <= step <= self.total_steps

    def start(self, step: int) -> None:
        if not self.can_start(step):
            raise PlanError(f"step {step} is not the active plan step")
        if step not in self.started_steps:
            self.started_steps.append(step)

    def complete(self, step: int) -> None:
        if step != self.current_step:
            raise PlanError(f"step {step} is not the active plan step")
        if step not in self.completed_steps:
            self.completed_steps.append(step)
        if step < self.total_steps:
            self.current_step += 1

    def status(self) -> list[str]:
        return [
            "completed" if i in self.completed_steps else "in_progress" if i == self.current_step else "pending"
            for i in range(1, self.total_steps + 1)
        ]

    def as_dict(self) -> dict[str, Any]:
        return {"current_step": None if self.completed else self.current_step, "total_steps": self.total_steps, "completed_steps": list(self.completed_steps), "status": self.status()}

from ..llm.cleaner import extract_json_payload


def _normalize_text_list(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if isinstance(item, str):
            text = item.strip()
            if text:
                result.append(text)
        elif isinstance(item, dict):
            parts = [str(v).strip() for v in item.values() if isinstance(v, str) and str(v).strip()]
            if parts:
                result.append(": ".join(parts))
    return result


def parse_plan(content: str) -> ExecutionPlan:
    try:
        payload = extract_json_payload(content)
    except Exception as exc:
        raise PlanError("invalid plan JSON: " + str(exc)) from exc
    if not isinstance(payload, dict):
        raise PlanError("plan must be a JSON object")
    summary = payload.get("summary")
    raw_steps = payload.get("steps")
    raw_validation = payload.get("validation")
    raw_risks = payload.get("risks")
    raw_criteria = payload.get("completion_criteria")
    raw_requirements = payload.get("security_requirements")
    security_level = payload.get("security_level", "normal")

    steps = _normalize_text_list(raw_steps)
    validation = _normalize_text_list(raw_validation if raw_validation is not None else [])
    risks = _normalize_text_list(raw_risks if raw_risks is not None else [])
    completion_criteria = _normalize_text_list(raw_criteria if raw_criteria is not None else [])
    security_requirements = _normalize_text_list(raw_requirements if raw_requirements is not None else [])

    if not isinstance(summary, str) or not summary.strip():
        raise PlanError("plan requires a non-empty summary")
    if not isinstance(raw_steps, list) or not steps:
        raise PlanError("plan requires a non-empty list of textual steps")
    if raw_validation is not None and not isinstance(raw_validation, list):
        raise PlanError("validation must be a list of textual checks")
    if raw_risks is not None and not isinstance(raw_risks, list):
        raise PlanError("risks must be a list of textual notes")
    if not isinstance(raw_criteria, list) or not completion_criteria:
        raise PlanError("completion_criteria must be a non-empty list of textual criteria")
    if security_level not in {"normal", "low_risk", "sensitive", "high_risk"}:
        raise PlanError("security_level must be normal, low_risk, sensitive, or high_risk")
    if raw_requirements is not None and not isinstance(raw_requirements, list):
        raise PlanError("security_requirements must be a list of textual requirements")
    if security_level in {"sensitive", "high_risk"} and not security_requirements:
        raise PlanError("security-sensitive plans require security_requirements")
    return ExecutionPlan(
        summary.strip(),
        steps,
        validation,
        risks,
        completion_criteria,
        security_level,
        security_requirements,
    )

def plan_instructions() -> str:
    return (
        'Return ONLY JSON for the execution plan: '
        '{"summary":"...","steps":["..."],"validation":["..."],"risks":["..."],"completion_criteria":["..."],"security_level":"normal","security_requirements":["..."]}. '
        'All list fields must be lists of plain strings, NOT objects. '
        '"completion_criteria" is REQUIRED: include at least one concrete criterion to check from implementation evidence. '
        'Create a concrete implementation plan based on the detected project and task. '
        'Include files/components likely to be created or changed, implementation order, how the result will be validated, and concrete completion criteria that can be checked from implementation evidence and automated validation. Do not modify files while planning.'
    )
