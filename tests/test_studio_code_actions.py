import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kardecagent.studio import code_actions


def test_returns_only_safe_ruff_fixes_as_source_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("import os\nprint('ok')\n", encoding="utf-8")
    payload = [{
        "code": "F401",
        "message": "`os` imported but unused",
        "fix": {
            "applicability": "safe",
            "edits": [{
                "location": {"row": 1, "column": 1},
                "end_location": {"row": 2, "column": 1},
                "content": "",
            }],
        },
    }, {
        "code": "E501",
        "message": "Line too long",
        "fix": {"applicability": "unsafe", "edits": []},
    }]
    monkeypatch.setattr(code_actions.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout=json.dumps(payload), stderr=""
    ))

    result = code_actions.python_code_actions("import os\nprint('ok')\n", "main.py", str(root), 1, 0)

    assert result["available"] is True
    assert len(result["actions"]) == 1
    assert result["actions"][0]["code"] == "F401"
    assert result["actions"][0]["edits"] == [{"start": 0, "end": 10, "content": ""}]
    assert (root / "main.py").read_text(encoding="utf-8") == "import os\nprint('ok')\n"


def test_code_actions_reject_paths_outside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the active workspace"):
        code_actions.python_code_actions("value = 1\n", str(outside), str(root), 1, 0)


def test_code_actions_reject_non_python_files(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    with pytest.raises(ValueError, match="Python files"):
        code_actions.python_code_actions("const x = 1", "main.js", str(root), 1, 0)


def test_code_actions_report_missing_ruff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(code_actions.shutil, "which", lambda name: None)
    monkeypatch.setattr(code_actions.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout="", stderr="/python: No module named ruff"
    ))

    result = code_actions.python_code_actions("value = 1", "main.py", str(root), 1, 0)

    assert result["available"] is False
    assert "extra 'studio'" in result["message"]


def test_code_actions_reject_overlapping_ruff_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    payload = [{
        "code": "FIX",
        "message": "Invalid overlapping edit",
        "fix": {
            "applicability": "safe",
            "edits": [
                {"location": {"row": 1, "column": 1}, "end_location": {"row": 1, "column": 4}, "content": "x"},
                {"location": {"row": 1, "column": 2}, "end_location": {"row": 1, "column": 5}, "content": "y"},
            ],
        },
    }]
    monkeypatch.setattr(code_actions.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout=json.dumps(payload), stderr=""
    ))

    result = code_actions.python_code_actions("abcde", "main.py", str(root), 1, 0)

    assert result["available"] is True
    assert result["actions"] == []


def test_real_ruff_exposes_safe_quick_fix(tmp_path: Path) -> None:
    import importlib.util

    if importlib.util.find_spec("ruff") is None:
        pytest.skip("Ruff is not installed in this test environment.")
    root = tmp_path / "project"
    root.mkdir()
    source = 'value = f"hello"\\nprint(value)\\n'
    (root / "main.py").write_text(source, encoding="utf-8")

    result = code_actions.python_code_actions(source, "main.py", str(root), 1, 0)

    assert result["available"] is True
    assert any(action["code"] == "F541" for action in result["actions"])
