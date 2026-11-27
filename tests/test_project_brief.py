from types import SimpleNamespace

from kardecagent.agent.project_brief import build_project_brief, project_brief_instructions


def _profile(kind="unknown", framework=None):
    return SimpleNamespace(kind=kind, framework=framework)


def test_animated_multi_page_website_is_classified_as_full_project():
    brief = build_project_brief(
        "Crie um website de portfólio com várias páginas, animações e layout responsivo do início ao fim",
        _profile(),
        [],
    )

    assert brief.mode == "full_project"
    assert brief.project_type == "website"
    assert brief.complexity == "large"
    assert any("visual design system" in item for item in brief.delivery_requirements)
    assert any("browser smoke test" in item for item in brief.quality_gates)
    assert "reduced-motion" in " ".join(brief.delivery_requirements)


def test_simple_existing_file_change_stays_maintenance():
    brief = build_project_brief(
        "Corrija o espaçamento do cabeçalho existente",
        _profile(kind="web"),
        ["src/header.tsx"],
    )

    assert brief.mode == "maintenance"
    assert brief.complexity == "focused"
    assert brief.delivery_requirements == ()


def test_backend_project_gets_integration_and_failure_path_quality_gates():
    brief = build_project_brief(
        "Desenvolva uma API completa com autenticação e banco de dados",
        _profile(kind="python"),
        ["pyproject.toml"],
    )

    assert brief.mode == "full_project"
    assert brief.project_type == "backend_service"
    assert any("failure paths" in item for item in brief.delivery_requirements)
    assert any("success and failure" in item for item in brief.quality_gates)


def test_brief_instructions_are_empty_for_maintenance_and_specific_for_website():
    maintenance = build_project_brief("Ajuste uma variável", _profile())
    website = build_project_brief("Crie um website com animações", _profile())

    assert project_brief_instructions(maintenance) == ""
    instructions = project_brief_instructions(website)
    assert "PROJECT DELIVERY MODE" in instructions
    assert "responsive" in instructions
    assert "Do not invent product requirements" in instructions



def test_agent_context_includes_project_delivery_profile(tmp_path):
    from kardecagent.agent.loop import AgentLoop
    from kardecagent.config import Settings

    loop = AgentLoop(object(), Settings())
    context = loop._build_context(
        tmp_path,
        "Crie um website institucional com várias páginas e animações",
    )

    assert context["project_brief"]["mode"] == "full_project"
    assert context["project_brief"]["project_type"] == "website"
    assert context["project_brief"]["quality_gates"]
