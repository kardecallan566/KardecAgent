from pathlib import Path

import pytest

from kardecagent.agent.qa import ProjectQAEngine


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
