from pathlib import Path

import pytest

from kardecagent.studio.language import python_semantic_request


def test_rejects_paths_outside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("value = 1", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the project"):
        python_semantic_request("definition", "value = 1", str(outside), str(root), 1, 0)


def test_rejects_unsupported_actions_and_languages(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.js"
    source.write_text("const value = 1", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported semantic action"):
        python_semantic_request("rename", "", str(source), str(root), 1, 0)
    with pytest.raises(ValueError, match="Python files"):
        python_semantic_request("complete", "", str(source), str(root), 1, 0)


def test_rejects_oversized_source(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.py"
    source.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="1 MB"):
        python_semantic_request("complete", "x" * 1_000_001, str(source), str(root), 1, 0)


def test_missing_jedi_is_a_supported_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.py"
    source.write_text("value = 1", encoding="utf-8")
    real_import = builtins.__import__

    def without_jedi(name, *args, **kwargs):
        if name == "jedi":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_jedi)
    result = python_semantic_request("complete", "value = 1", str(source), str(root), 1, 5)
    assert result["available"] is False
    assert result["items"] == []



def test_rejects_unsupported_semantic_action(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.py"
    source.write_text("value = 1", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported semantic action"):
        python_semantic_request("rename", "value = 1", str(source), str(root), 1, 0)


def test_python_syntax_diagnostics_are_returned_when_jedi_is_available(tmp_path: Path) -> None:
    pytest.importorskip("jedi")
    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.py"
    code = "def broken(:\n    pass\n"
    source.write_text(code, encoding="utf-8")

    result = python_semantic_request("diagnostics", code, str(source), str(root), 1, 0)

    assert result["available"] is True
    assert result["action"] == "diagnostics"
    assert result["items"]
    assert result["items"][0]["line"] == 1


def test_python_signature_help_returns_function_parameters_when_jedi_is_available(tmp_path: Path) -> None:
    pytest.importorskip("jedi")
    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.py"
    code = "def add(first, second):\n    return first + second\n\nadd(1, "
    source.write_text(code, encoding="utf-8")

    result = python_semantic_request("signature", code, str(source), str(root), 4, 7)

    assert result["available"] is True
    assert result["action"] == "signature"
    assert result["items"]
    assert result["items"][0]["name"] == "add"
    assert result["items"][0]["parameters"][:2] == ["first", "second"]
