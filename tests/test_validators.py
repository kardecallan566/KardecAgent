from pathlib import Path

from kardecagent.project.detector import detect_project
from kardecagent.project.validators import validate_static_html


def test_detect_static_html(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html lang='pt-BR'><head>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Site</title></head><body><a href='#about'>Sobre</a>"
        "<section id='about'>OK</section></body></html>",
        encoding="utf-8",
    )
    profile = detect_project(tmp_path)
    assert profile.kind == "static-html"
    assert profile.language == "html"


def test_valid_static_html(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html lang='pt-BR'><head><title>Site</title>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<link rel='stylesheet' href='style.css'></head>"
        "<body><script src='script.js'></script></body></html>",
        encoding="utf-8",
    )
    (tmp_path / "style.css").write_text("body { margin: 0; }", encoding="utf-8")
    (tmp_path / "script.js").write_text("console.log('ok');", encoding="utf-8")
    result = validate_static_html(tmp_path)
    assert result.passed
    assert result.errors == []


def test_static_html_missing_asset_fails(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head><title>x</title></head>"
        "<body><img src='missing.png'></body></html>",
        encoding="utf-8",
    )
    result = validate_static_html(tmp_path)
    assert not result.passed
    assert any("missing local reference" in error for error in result.errors)


def test_static_html_broken_anchor_fails(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head><title>x</title></head>"
        "<body><a href='#missing'>x</a></body></html>",
        encoding="utf-8",
    )
    result = validate_static_html(tmp_path)
    assert not result.passed
    assert any("broken internal anchor" in error for error in result.errors)
