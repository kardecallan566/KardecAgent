"""Evidence-based skill analytics and human-reviewed improvement proposals."""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path

from .persistence import TaskStore
from .skills import SkillCatalog, SkillError

_TOKEN = re.compile(r"^[a-z0-9_+-]{3,40}$")
_STOP_WORDS = {
    "the", "and", "for", "with", "from", "this", "that", "please", "help",
    "about", "into", "your", "task", "project", "uma", "para", "com", "que",
    "por", "como", "quero", "preciso", "fazer", "sobre", "mais", "das", "dos",
}


class SkillLearningEngine:
    """Summarize persisted outcomes; propose drafts but never modify skills."""

    def __init__(self, project_root: str | Path, *, task_limit: int = 100, min_samples: int = 3) -> None:
        if isinstance(task_limit, bool) or not isinstance(task_limit, int) or not 1 <= task_limit <= 100:
            raise ValueError("task_limit must be between 1 and 100")
        if isinstance(min_samples, bool) or not isinstance(min_samples, int) or not 2 <= min_samples <= 20:
            raise ValueError("min_samples must be between 2 and 20")
        self.root = Path(project_root).resolve()
        self.task_limit = task_limit
        self.min_samples = min_samples

    def analyze(self) -> dict:
        tasks = TaskStore(self.root).list_recent(limit=self.task_limit)
        usage: dict[str, dict] = defaultdict(lambda: {
            "attempts": 0, "verified_successes": 0, "not_verified": 0, "last_used_at": None,
            "task_ids": [],
        })
        unmatched: dict[str, dict] = {}
        for task_identifier, state, _plan, _board in tasks:
            for event in state.events:
                data = event.data if isinstance(event.data, dict) else {}
                if event.event_type == "skill_outcome":
                    success = data.get("success")
                    skill_ids = data.get("skill_ids", [])
                    if not isinstance(success, bool) or not isinstance(skill_ids, list):
                        continue
                    for skill_id in set(item for item in skill_ids if isinstance(item, str)):
                        item = usage[skill_id]
                        item["attempts"] += 1
                        item["verified_successes"] += int(success)
                        item["not_verified"] += int(not success)
                        item["last_used_at"] = max(
                            filter(None, [item["last_used_at"], event.timestamp])
                        )
                        if task_identifier not in item["task_ids"]:
                            item["task_ids"].append(task_identifier)
                elif event.event_type == "skills_unmatched":
                    fingerprint = data.get("task_fingerprint")
                    terms = data.get("task_terms", [])
                    if not isinstance(fingerprint, str) or not fingerprint or not isinstance(terms, list):
                        continue
                    entry = unmatched.setdefault(fingerprint, {"count": 0, "terms": Counter(), "task_ids": []})
                    entry["count"] += 1
                    entry["terms"].update(
                        term.casefold() for term in terms
                        if isinstance(term, str) and _TOKEN.fullmatch(term.casefold())
                        and term.casefold() not in _STOP_WORDS
                    )
                    if task_identifier not in entry["task_ids"]:
                        entry["task_ids"].append(task_identifier)

        metrics = []
        for skill_id, item in sorted(usage.items(), key=lambda pair: (-pair[1]["attempts"], pair[0])):
            attempts = item["attempts"]
            metrics.append({
                "skill_id": skill_id,
                "attempts": attempts,
                "verified_successes": item["verified_successes"],
                "not_verified": item["not_verified"],
                "verified_success_rate": round(item["verified_successes"] / attempts * 100, 1) if attempts else None,
                "last_used_at": item["last_used_at"],
            })

        proposals = self._improvement_proposals(usage)
        proposals.extend(self._new_skill_proposals(unmatched))
        proposals.sort(key=lambda item: (item["kind"], item["proposal_id"]))
        return {
            "tasks_analyzed": len(tasks),
            "analysis_limit": self.task_limit,
            "metrics": metrics,
            "proposals": proposals[:20],
            "proposals_are_drafts": True,
            "auto_changes": False,
        }

    def _improvement_proposals(self, usage: dict[str, dict]) -> list[dict]:
        try:
            catalog = SkillCatalog(self.root)
            manifests = {item.id: item for item in catalog.discover()}
        except SkillError:
            return []
        proposals = []
        for skill_id, item in usage.items():
            attempts = item["attempts"]
            failures = item["not_verified"]
            if attempts < self.min_samples or failures < 2 or failures / attempts < 0.5:
                continue
            manifest = manifests.get(skill_id)
            if manifest is None:
                continue
            try:
                instructions_path = catalog._safe_file(
                    catalog.root / skill_id, catalog.INSTRUCTIONS, catalog.MAX_INSTRUCTIONS_BYTES
                )
                current = instructions_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, SkillError):
                continue
            addition = (
                "\n\n## Revisão sugerida pelo KardecAgent (rascunho)\n"
                f"- Revise pré-condições, tratamento de erros e critérios de verificação: "
                f"{failures} de {attempts} tarefas que utilizaram esta Skill terminaram sem verificação.\n"
                "- Este indicador não prova que a Skill causou o resultado; revise as evidências antes de alterar as instruções.\n"
            )
            draft = current.rstrip() + addition
            if len(draft.encode("utf-8")) > catalog.MAX_INSTRUCTIONS_BYTES:
                draft = ""
            proposal_id = hashlib.sha256(
                f"improve:{skill_id}:{attempts}:{failures}".encode("utf-8")
            ).hexdigest()[:12]
            proposals.append({
                "proposal_id": proposal_id,
                "kind": "improve_skill",
                "skill_id": skill_id,
                "title": f"Revisar a Skill {manifest.name}",
                "reason": (
                    f"{failures} de {attempts} tarefas que utilizaram esta Skill "
                    "terminaram sem resultado verificado. Isso é um sinal para revisão, não prova de causalidade."
                ),
                "attempts": attempts,
                "not_verified": failures,
                "evidence_task_ids": item["task_ids"][:5],
                "draft_manifest": {
                    "id": manifest.id, "name": manifest.name, "description": manifest.description,
                    "version": manifest.version, "tags": list(manifest.tags),
                    "dependencies": list(manifest.dependencies),
                },
                "draft_instructions": draft or None,
            })
        return proposals

    def _new_skill_proposals(self, unmatched: dict[str, dict]) -> list[dict]:
        try:
            existing = {item.id for item in SkillCatalog(self.root).discover()}
        except SkillError:
            return []
        proposals = []
        for fingerprint, item in unmatched.items():
            if item["count"] < self.min_samples:
                continue
            terms = [term for term, _count in item["terms"].most_common(5)]
            if not terms:
                continue
            base_id = "proposed-" + "-".join(terms[:3])
            skill_id = base_id[:64]
            suffix = 2
            while skill_id in existing:
                suffix_text = f"-{suffix}"
                skill_id = base_id[:64 - len(suffix_text)] + suffix_text
                suffix += 1
            name = " ".join(term.capitalize() for term in terms[:3])
            proposal_id = hashlib.sha256(
                f"create:{fingerprint}:{item['count']}".encode("utf-8")
            ).hexdigest()[:12]
            proposals.append({
                "proposal_id": proposal_id,
                "kind": "create_skill",
                "skill_id": skill_id,
                "title": f"Criar Skill: {name}",
                "reason": (
                    f"O mesmo padrão de solicitação apareceu em {item['count']} tarefas sem uma Skill correspondente."
                ),
                "attempts": item["count"],
                "not_verified": 0,
                "evidence_task_ids": item["task_ids"][:5],
                "draft_manifest": {
                    "id": skill_id,
                    "name": name,
                    "description": f"Rascunho para o padrão recorrente: {', '.join(terms)}.",
                    "version": "1.0.0",
                    "tags": terms,
                    "dependencies": [],
                },
                "draft_instructions": (
                    f"# {name}\n\n"
                    "## Objetivo\nDefina o resultado esperado para este tipo de tarefa.\n\n"
                    "## Fluxo recomendado\n"
                    "1. Inspecione o contexto e confirme os requisitos.\n"
                    "2. Execute mudanças pequenas e verificáveis.\n"
                    "3. Valide o resultado e registre evidências.\n\n"
                    "## Critérios de qualidade\n"
                    "- Preserve alterações não relacionadas.\n"
                    "- Não instale dependências nem execute ações externas sem aprovação.\n"
                    "- Não declare conclusão sem verificação.\n"
                ),
            })
        return proposals
