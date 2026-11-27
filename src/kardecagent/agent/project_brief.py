from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


@dataclass(frozen=True)
class ProjectBrief:
    """Deterministic project-scale profile used to guide planning and QA."""

    mode: str
    project_type: str
    complexity: str
    signals: tuple[str, ...]
    delivery_requirements: tuple[str, ...]
    quality_gates: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["signals"] = list(self.signals)
        result["delivery_requirements"] = list(self.delivery_requirements)
        result["quality_gates"] = list(self.quality_gates)
        return result


_WEBSITE_TERMS = (
    "website", "web site", "site", "landing page", "web app", "dashboard",
    "e-commerce", " ecommerce", "loja virtual", "portal", "página web",
    "pagina web", "página", "pagina", "animação", "animacao", "animações",
    "animacoes", "responsivo", "responsiva", "frontend", "front-end",
)
_PROJECT_TERMS = (
    "criar", "construir", "desenvolver", "implementar do zero", "projeto completo",
    "from scratch", "build", "create", "develop", "implement",
)
_LARGE_TERMS = (
    "completo", "do início ao fim", "do inicio ao fim", "várias páginas",
    "varias paginas", "múltiplas páginas", "multiplas paginas", "animações",
    "animacoes", "autenticação", "autenticacao", "banco de dados", "integração",
    "integracao", "admin", "dashboard", "e-commerce", " ecommerce",
)


def build_project_brief(task: str, profile: Any, project_files: list[str] | None = None) -> ProjectBrief:
    """Classify the requested work without imposing an unrequested technology stack."""
    text = task.casefold()
    files = project_files or []
    signals: list[str] = []
    website_hits = [term for term in _WEBSITE_TERMS if term in text]
    project_hits = [term for term in _PROJECT_TERMS if term in text]
    large_hits = [term for term in _LARGE_TERMS if term in text]

    if website_hits:
        project_type = "website"
        signals.append("web_product_request")
    elif any(term in text for term in ("api", "backend", "serviço", "servico")):
        project_type = "backend_service"
        signals.append("backend_request")
    elif any(term in text for term in ("aplicativo", "app", "application", "desktop")):
        project_type = "application"
        signals.append("application_request")
    else:
        project_type = "existing_project_change"

    if project_hits:
        signals.append("greenfield_or_feature_build")
    if large_hits:
        signals.append("large_scope_language")
    if len(files) >= 80:
        signals.append("large_existing_workspace")
    if getattr(profile, "framework", None):
        signals.append("framework_detected")

    is_project = bool(project_hits or project_type != "existing_project_change")
    is_large = bool(large_hits or len(files) >= 80 or len(website_hits) >= 3)
    mode = "full_project" if is_project and is_large else (
        "project" if is_project else "maintenance"
    )
    complexity = "large" if is_large else ("medium" if is_project else "focused")

    requirements: list[str] = []
    gates: list[str] = []
    if project_type == "website":
        requirements.extend([
            "Inspect the existing stack and preserve it unless a justified, approved change is needed.",
            "Define page and route structure, reusable components, and a consistent visual design system.",
            "Implement responsive layouts for mobile, tablet, and desktop.",
            "Implement purposeful motion and interaction states with reduced-motion accessibility support.",
            "Check semantic HTML, keyboard navigation, accessible names, and visible focus states.",
            "Verify loading, empty, error, and success states wherever the product has data or forms.",
        ])
        gates.extend([
            "Run available lint, typecheck, tests, and production build commands.",
            "Perform a browser smoke test when a browser/runtime is available; otherwise report this limitation explicitly.",
            "Check responsive behavior and critical navigation/form interactions.",
            "Inspect console/runtime errors and fix regressions before completion.",
        ])
    elif project_type in {"backend_service", "application"}:
        requirements.extend([
            "Define module boundaries and public interfaces before broad implementation.",
            "Preserve existing conventions and avoid unnecessary dependencies.",
            "Handle invalid input, failure paths, and user-visible errors.",
        ])
        gates.extend([
            "Run available tests, lint, typecheck, and build/validation commands.",
            "Exercise primary success and failure paths.",
        ])

    if mode == "full_project":
        requirements.extend([
            "Break implementation into independently verifiable milestones with explicit integration points.",
            "Keep a project-level acceptance checklist and update it as requirements are implemented.",
            "After integration, run the complete project validation rather than relying only on per-file checks.",
            "Do not mark the project complete while a required page, flow, or acceptance criterion remains unverified.",
        ])
        gates.append("Run a final end-to-end acceptance pass against the original user request.")
    elif mode == "project":
        requirements.append("Deliver a coherent, usable result rather than isolated scaffolding.")
        gates.append("Verify the primary user journey end to end.")

    if not gates:
        gates.extend(["Run the narrowest relevant automated checks.", "Verify the exact changed behavior from evidence."])

    return ProjectBrief(
        mode=mode,
        project_type=project_type,
        complexity=complexity,
        signals=tuple(dict.fromkeys(signals)),
        delivery_requirements=tuple(dict.fromkeys(requirements)),
        quality_gates=tuple(dict.fromkeys(gates)),
    )


def project_brief_instructions(brief: ProjectBrief) -> str:
    """Return planning constraints for the classified project scope."""
    if brief.mode == "maintenance":
        return ""
    requirements = "\n".join(f"- {item}" for item in brief.delivery_requirements)
    gates = "\n".join(f"- {item}" for item in brief.quality_gates)
    return (
        "\nPROJECT DELIVERY MODE: " + brief.mode + " (" + brief.project_type + "). "
        "Plan the requested product as an integrated deliverable, not a collection of disconnected files. "
        "Keep the approved plan within the existing plan step limit by grouping coherent milestones. "
        "Do not invent product requirements or change the technology stack without justification. "
        "The following requirements are quality constraints, not permission to install dependencies or perform external actions.\n"
        "Delivery requirements:\n" + requirements + "\nQuality gates:\n" + gates
    )
