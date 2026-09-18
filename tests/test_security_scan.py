from pathlib import Path

from kardecagent.project.security import scan_project


def test_security_scan_detects_private_key(tmp_path: Path):
    (tmp_path / "config.py").write_text(
        "KEY = '-----BEGIN PRIVATE KEY-----'\n",
        encoding="utf-8",
    )
    result = scan_project(tmp_path)
    assert not result.passed
    assert result.findings[0].kind == "private_key"


def test_security_scan_accepts_clean_project(tmp_path: Path):
    (tmp_path / "config.py").write_text(
        "KEY = os.getenv('API_KEY')\n",
        encoding="utf-8",
    )
    result = scan_project(tmp_path)
    assert result.passed
