from __future__

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

    def as_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "steps": self.steps, "validation": self.validation, "risks": self.risks}

    def format_for_review(self) -> str:
        lines = ["PLANO DE EXECUÇÃO", "", self.summary, "", "Etapas:"]
        lines.extend(f"{i}. {step}" for i, step in enumerate(self.steps, 1))
        lines.append("")
        lines.append("Validação:")
        lines.extend(f"- {item}" for item in self.validation)
        if self.risks:
            lines.append("")
            lines.append("Riscos / observações:")
            lines.extend(f"- {item}" for item in self.risks)
        return "\n".join(lines)

def parse_plan(content: str) -> ExecutionPlan:
    text = content.strip()
    if text.startswith("```"): raise PlanError("markdown code fences are not allowed")
    try: payload = json.loads(text)
    except json.JSONDecodeError as exc: raise PlanError("invalid plan JSON: " + exc.msg) from exc
    if not isinstance(payload, dict): raise PlanError("plan must be a JSON object")
    summary, steps = payload.get("summary"), payload.get("steps")
    validation, risks = payload.get("validation", []), payload.get("risks", [])
    if not isinstance(summary, str) or not summary.strip(): raise PlanError("plan requires a non-empty summary")
    if not isinstance(steps, list) or not steps or not all(isinstance(x, str) and x.strip() for x in steps): raise PlanError("plan requires a non-empty list of textual steps")
    if not isinstance(validation, list) or not all(isinstance(x, str) and x.strip() for x in validation): raise PlanError("validation must be a list of textual checks")
    if not isinstance(risks, list) or not all(isinstance(x, str) and x.strip() for x in risks): raise PlanError("risks must be a list of textual notes")
    return ExecutionPlan(summary.strip(), [x.strip() for x in steps], [x.strip() for x in validation], [x.strip() for x in risks])

def plan_instructions() -> str:
    return ('Return ONLY JSON for the execution plan: {"summary":"...","steps":["..."],"validation":["..."],"risks":["..."]}. '
            'Create a concrete implementation plan based on the detected project and task. '
            'Include files/components likely to be created or changed, implementation order, and how the result will be validated. Do not modify files while planning.')
