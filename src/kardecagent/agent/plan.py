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

def parse_plan(content: str) -> ExecutionPlan:
    text = content.strip()
    if text.startswith("```"): raise PlanError("markdown code fences are not allowed")
    try: payload = json.loads(text)
    except json.JSONDecodeError as exc: raise PlanError("invalid plan JSON: " + exc.msg) from exc
    if not isinstance(payload, dict): raise PlanError("plan must be a JSON object")
    summary, steps = payload.get("summary"), payload.get("steps")
    validation, risks = payload.get("validation", []), payload.get("risks", [])
    completion_criteria = payload.get("completion_criteria", [])
    security_level = payload.get("security_level", "normal")
    security_requirements = payload.get("security_requirements", [])
    if not isinstance(summary, str) or not summary.strip(): raise PlanError("plan requires a non-empty summary")
    if not isinstance(steps, list) or not steps or not all(isinstance(x, str) and x.strip() for x in steps): raise PlanError("plan requires a non-empty list of textual steps")
    if not isinstance(validation, list) or not all(isinstance(x, str) and x.strip() for x in validation): raise PlanError("validation must be a list of textual checks")
    if not isinstance(risks, list) or not all(isinstance(x, str) and x.strip() for x in risks): raise PlanError("risks must be a list of textual notes")
    if not isinstance(completion_criteria, list) or not completion_criteria or not all(isinstance(x, str) and x.strip() for x in completion_criteria): raise PlanError("completion_criteria must be a non-empty list of textual criteria")
    if security_level not in {"normal", "low_risk", "sensitive", "high_risk"}: raise PlanError("security_level must be normal, low_risk, sensitive, or high_risk")
    if not isinstance(security_requirements, list) or not all(isinstance(x, str) and x.strip() for x in security_requirements): raise PlanError("security_requirements must be a list of textual requirements")
    if security_level in {"sensitive", "high_risk"} and not security_requirements: raise PlanError("security-sensitive plans require security_requirements")
    return ExecutionPlan(summary.strip(), [x.strip() for x in steps], [x.strip() for x in validation], [x.strip() for x in risks], [x.strip() for x in completion_criteria], security_level, [x.strip() for x in security_requirements])

def plan_instructions() -> str:
    return ('Return ONLY JSON for the execution plan: {"summary":"...","steps":["..."],"validation":["..."],"risks":["..."],"completion_criteria":["..."],"security_level":"normal","security_requirements":["..."]}. '
            'Create a concrete implementation plan based on the detected project and task. '
            'Include files/components likely to be created or changed, implementation order, how the result will be validated, and concrete completion criteria that can be checked from implementation evidence and automated validation. Do not modify files while planning.')
