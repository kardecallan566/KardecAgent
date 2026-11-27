"""Task-aware skill selection with explicit approval and auditable outcomes."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .skills import SkillCatalog, SkillError

_TOKEN = re.compile(r"[a-z0-9_+-]{3,}", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SkillPreparation:
    candidate_ids: tuple[str, ...] = ()
    loaded_ids: tuple[str, ...] = ()
    prompt_sections: tuple[str, ...] = ()
    scores: tuple[tuple[str, float], ...] = ()
    approval_status: str = "not_required"

    def as_dict(self) -> dict:
        return {
            "candidate_ids": list(self.candidate_ids),
            "loaded_ids": list(self.loaded_ids),
            "scores": {key: round(value, 4) for key, value in self.scores},
            "approval_status": self.approval_status,
        }


class SkillOrchestrator:
    """Select relevant skills deterministically and never activate without approval."""

    def __init__(self, catalog: SkillCatalog, *, max_skills: int = 3) -> None:
        if isinstance(max_skills, bool) or not isinstance(max_skills, int) or not 1 <= max_skills <= 8:
            raise ValueError("max_skills must be an integer between 1 and 8")
        self.catalog = catalog
        self.max_skills = max_skills

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {item.casefold() for item in _TOKEN.findall(value)}

    def select(self, task: str) -> SkillPreparation:
        if not isinstance(task, str) or not task.strip():
            raise SkillError("task must be a non-empty string")
        tokens = self._tokens(task)
        if not tokens:
            return SkillPreparation()
        manifests = self.catalog.discover()
        scored = []
        for item in manifests:
            tags = tokens & self._tokens(" ".join(item.tags))
            names = tokens & self._tokens(item.name + " " + item.id.replace("-", " "))
            descriptions = tokens & self._tokens(item.description)
            raw = 3 * len(tags) + 2 * len(names) + len(descriptions)
            if raw:
                scored.append((item.id, raw / max(1, 3 * len(tokens))))
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        chosen = [skill_id for skill_id, _ in scored[:self.max_skills]]
        if not chosen:
            return SkillPreparation(scores=tuple(scored))
        by_id = {item.id: item for item in manifests}
        ordered, seen = [], set()

        def include(skill_id: str) -> None:
            if skill_id in seen:
                return
            for dependency in by_id[skill_id].dependencies:
                include(dependency)
            seen.add(skill_id)
            ordered.append(skill_id)

        for skill_id in chosen:
            include(skill_id)
        score_map = dict(scored)
        return SkillPreparation(
            candidate_ids=tuple(ordered),
            scores=tuple((skill_id, score_map.get(skill_id, 0.0)) for skill_id in ordered),
            approval_status="approval_required",
        )

    def prepare(self, task: str, *, approval_callback: Callable[[tuple[str, ...]], bool] | None = None) -> SkillPreparation:
        selection = self.select(task)
        if not selection.candidate_ids or approval_callback is None:
            return selection
        if not bool(approval_callback(selection.candidate_ids)):
            return SkillPreparation(
                candidate_ids=selection.candidate_ids,
                scores=selection.scores,
                approval_status="rejected",
            )
        sections = self.catalog.load_prompt_sections(list(selection.candidate_ids), approved=True)
        return SkillPreparation(
            candidate_ids=selection.candidate_ids,
            loaded_ids=selection.candidate_ids,
            prompt_sections=tuple(sections),
            scores=selection.scores,
            approval_status="approved",
        )

    @staticmethod
    def outcome(selection: SkillPreparation, *, success: bool) -> dict:
        return {
            "skill_ids": list(selection.loaded_ids),
            "success": bool(success),
            "approval_status": selection.approval_status,
            "evaluation": "verified_success" if success else "not_verified",
        }
