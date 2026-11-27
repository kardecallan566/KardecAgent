from pathlib import Path

import pytest

from kardecagent.agent.loop import AgentLoop
from kardecagent.agent.qa import ProjectQAEngine
from kardecagent.agent.tools_schema import ToolCallError, parse_tool_call
from kardecagent.config import Settings


def test_static_qa_discovers_html_routes(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        '<!doctype html><html lang="pt-BR"><head>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Home</title></head><body><a href="/about/">About</a></body></html>',
        encoding="utf-8",
    )
    about = tmp_path / "about" / "index.html"
    about.parent.mkdir()
    about.write_text(
        '<!doctype html><html lang="pt-BR"><head>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>About</title></head><body><main>About</main></body></html>',
        encoding="utf-8",
    )

    report = ProjectQAEngine(tmp_path).run_static()

    assert report["available"]
    assert report["passed"]
    assert report["routes"]["count"] == 2
    assert [route["path"] for route in report["routes"]["routes"]] == ["/", "/about"]


def test_static_qa_discovers_next_app_and_react_router_routes(tmp_path: Path):
    app_page = tmp_path / "app" / "dashboard" / "page.tsx"
    app_page.parent.mkdir(parents=True)
    app_page.write_text("export default function Page() { return <main>Dashboard</main> }", encoding="utf-8")
    router = tmp_path / "src" / "router.tsx"
    router.parent.mkdir()
    router.write_text(
        'const routes = [{ path: "/settings", element: <Settings /> }];',
        encoding="utf-8",
    )

    routes = ProjectQAEngine(tmp_path).discover_routes()

    assert routes["count"] == 2
    assert {item["path"] for item in routes["routes"]} == {"/dashboard", "/settings"}


def test_static_qa_reports_missing_accessible_names_and_viewport(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        '<!doctype html><html><head><title>Bad page</title></head><body>'
        '<img src="hero.png"><input type="email"><button><svg></svg></button>'
        '</body></html>',
        encoding="utf-8",
    )

    report = ProjectQAEngine(tmp_path).run_static()

    assert not report["passed"]
    joined = "\\n".join(report["errors"])
    assert "missing alt text" in joined
    assert "no associated label" in joined
    assert "<button> has no accessible name" in joined
    assert "viewport meta tag is missing" in joined


def test_static_qa_skips_non_web_projects(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")

    report = ProjectQAEngine(tmp_path).run_static()

    assert report["available"] is False
    assert report["passed"] is True
    assert report["routes"]["count"] == 0


def test_browser_qa_rejects_non_loopback_urls(tmp_path: Path):
    engine = ProjectQAEngine(tmp_path)

    with pytest.raises(ValueError, match="loopback"):
        engine.run_browser_smoke("https://example.com")


def test_browser_qa_accepts_only_explicit_local_urls(tmp_path: Path):
    engine = ProjectQAEngine(tmp_path)

    with pytest.raises(ValueError, match="loopback"):
        engine.run_browser_smoke("file:///tmp/index.html")


def test_browser_qa_tool_requires_an_approved_plan_step():
    with pytest.raises(ToolCallError, match="plan_step is required"):
        parse_tool_call(
            '{"tool":"run_browser_qa","arguments":{"base_url":"http://localhost:3000"}}'
        )

    action = parse_tool_call(
        '{"tool":"run_browser_qa","arguments":{"base_url":"http://localhost:3000"},'
        '"plan_step":1}'
    )
    assert action.tool == "run_browser_qa"
    assert action.plan_step == 1


def test_executor_includes_static_project_qa_in_web_verification(tmp_path: Path, monkeypatch):
    (tmp_path / "index.html").write_text(
        '<!doctype html><html><head><title>Missing viewport</title></head><body></body></html>',
        encoding="utf-8",
    )

    class UnusedLLM:
        def chat(self, messages):
            raise AssertionError("verification must not call the language model")

    loop = AgentLoop(UnusedLLM(), Settings())
    monkeypatch.setattr(
        loop.executor,
        "_run_check",
        lambda root, kind: {"kind": kind, "available": True, "passed": True},
    )

    report = loop.executor.verify(tmp_path)

    assert "project_qa" in report
    assert report["project_qa"]["available"]
    assert not report["project_qa"]["passed"]
    assert not report["verified"]
    assert report["summary"]["verified"] is False
    assert report["checks"][-1]["kind"] == "project_qa"
    assert report["checks"][-1]["status"] == "failed"


def test_executor_browser_qa_tool_refuses_remote_hosts(tmp_path: Path):
    import json
    from kardecagent.agent.tools_schema import parse_tool_call

    class UnusedLLM:
        def chat(self, messages):
            raise AssertionError("browser QA tool must not call the language model")

    loop = AgentLoop(UnusedLLM(), Settings())
    action = parse_tool_call(json.dumps({
        "tool": "run_browser_qa",
        "arguments": {"base_url": "https://example.com"},
        "plan_step": 1,
    }))

    with pytest.raises(ValueError, match="loopback"):
        loop.executor._execute_tool(tmp_path, action, None)


def test_browser_qa_rejects_external_and_traversal_routes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ProjectQAEngine, "playwright_available", staticmethod(lambda: True))
    engine = ProjectQAEngine(tmp_path)

    with pytest.raises(ValueError, match="local absolute paths"):
        engine.run_browser_smoke("http://localhost:3000", routes=["https://example.com"])

    with pytest.raises(ValueError, match="local absolute paths"):
        engine.run_browser_smoke("http://localhost:3000", routes=["/../../etc/passwd"])


def test_static_qa_checks_motion_preferences_in_external_stylesheets(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        '<!doctype html><html lang="pt-BR"><head>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<link rel="stylesheet" href="style.css"><title>Motion</title>'
        '</head><body><main>Ready</main></body></html>',
        encoding="utf-8",
    )
    (tmp_path / "style.css").write_text(
        ".hero { animation: fade-in 1s ease; }",
        encoding="utf-8",
    )

    report = ProjectQAEngine(tmp_path).run_static()

    assert report["passed"]
    assert any("prefers-reduced-motion" in warning for warning in report["warnings"])
