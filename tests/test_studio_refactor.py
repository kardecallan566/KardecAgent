from pathlib import Path

import pytest

from kardecagent.studio.refactor import RenamePreviewManager


def test_preview_does_not_modify_files_until_apply(tmp_path: Path) -> None:
    pytest.importorskip("jedi")
    root = tmp_path / "project"
    root.mkdir()
    source = "def greet():\n    return 'hello'\n"
    active = root / "main.py"
    active.write_text(source, encoding="utf-8")
    consumer = root / "consumer.py"
    consumer.write_text("from main import greet\ngreet()\n", encoding="utf-8")

    manager = RenamePreviewManager()
    preview = manager.preview(str(root), "main.py", source, 1, 5, "welcome")

    assert preview["preview_id"]
    assert {item["path"] for item in preview["changes"]} == {"main.py", "consumer.py"}
    assert "welcome" in preview["diff"]
    assert active.read_text(encoding="utf-8") == source
    assert "greet" in consumer.read_text(encoding="utf-8")

    applied = manager.apply(str(root), preview["preview_id"])

    assert applied == {"applied": True, "files": ["consumer.py", "main.py"]}
    assert "def welcome" in active.read_text(encoding="utf-8")
    assert "from main import welcome" in consumer.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="not found"):
        manager.apply(str(root), preview["preview_id"])


def test_apply_rejects_files_changed_after_preview(tmp_path: Path) -> None:
    pytest.importorskip("jedi")
    root = tmp_path / "project"
    root.mkdir()
    source = "def greet():\n    return 'hello'\n"
    active = root / "main.py"
    active.write_text(source, encoding="utf-8")
    manager = RenamePreviewManager()
    preview = manager.preview(str(root), "main.py", source, 1, 5, "welcome")

    active.write_text(source + "# concurrent edit\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed after preview"):
        manager.apply(str(root), preview["preview_id"])
    assert active.read_text(encoding="utf-8").endswith("# concurrent edit\n")


def test_preview_rejects_unsaved_source_and_invalid_identifier(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    active = root / "main.py"
    active.write_text("value = 1\n", encoding="utf-8")
    manager = RenamePreviewManager()

    with pytest.raises(ValueError, match="Save the active file"):
        manager.preview(str(root), "main.py", "value = 2\n", 1, 0, "renamed")
    with pytest.raises(ValueError, match="valid"):
        manager.preview(str(root), "main.py", "value = 1\n", 1, 0, "not valid")


def test_preview_rejects_paths_outside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    manager = RenamePreviewManager()

    with pytest.raises(ValueError, match="inside the workspace"):
        manager.preview(str(root), str(outside), "value = 1\n", 1, 0, "renamed")
