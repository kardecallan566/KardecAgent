from pathlib import Path

import pytest

from kardecagent.tools.patch import apply_unified_patch


def test_apply_unified_patch(tmp_path: Path):
    path = tmp_path / "app.py"
    path.write_text("def hello():\n    return 'old'\n", encoding="utf-8")
    patch = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def hello():
-    return 'old'
+    return 'new'
"""
    result = apply_unified_patch(tmp_path, patch)
    assert result.applied
    assert result.changed_files == ("app.py",)
    assert path.read_text(encoding="utf-8") == "def hello():\n    return 'new'\n"


def test_patch_rejects_context_mismatch(tmp_path: Path):
    path = tmp_path / "app.py"
    path.write_text("def hello():\n    return 'different'\n", encoding="utf-8")
    patch = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def hello():
-    return 'old'
+    return 'new'
"""
    result = apply_unified_patch(tmp_path, patch)
    assert not result.applied
    assert "mismatch" in (result.error or "").lower()
    assert "different" in path.read_text(encoding="utf-8")


def test_patch_rejects_path_escape(tmp_path: Path):
    patch = """--- a/../secret.txt
+++ b/../secret.txt
@@ -1 +1 @@
-old
+new
"""
    result = apply_unified_patch(tmp_path, patch)
    assert not result.applied


def test_patch_rejects_new_files_for_now(tmp_path: Path):
    patch = """--- /dev/null
+++ b/new.txt
@@ -0,0 +1 @@
+hello
"""
    result = apply_unified_patch(tmp_path, patch)
    assert not result.applied
