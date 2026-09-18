import json

from kardecagent.project.dependencies import discover_audit_command, parse_npm_audit, parse_pip_audit


def test_discovers_npm_audit_for_package_project(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert discover_audit_command(tmp_path) == ("npm", "npm audit --json")


def test_discovers_pnpm_audit(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9", encoding="utf-8")
    assert discover_audit_command(tmp_path) == ("npm", "pnpm audit --json")


def test_npm_audit_clean():
    passed, summary = parse_npm_audit(json.dumps({"metadata": {"vulnerabilities": {"info": 0, "low": 0, "moderate": 0, "high": 0, "critical": 0}}}))
    assert passed
    assert "No known" in summary


def test_npm_audit_detects_vulnerability():
    passed, summary = parse_npm_audit(json.dumps({"metadata": {"vulnerabilities": {"low": 1, "high": 2}}}))
    assert not passed
    assert "3" in summary


def test_pip_audit_detects_vulnerability():
    passed, summary = parse_pip_audit(json.dumps([{"name": "demo", "vulns": [{"id": "CVE-demo"}]}]))
    assert not passed
    assert "1" in summary
