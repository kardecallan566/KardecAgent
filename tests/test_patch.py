from pathlib import Path

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
    assert result.content_sha256
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


def test_patch_rejects_path_escape(tmp_path: Path):
    patch = """--- a/../secret.txt
+++ b/../secret.txt
@@ -1 +1 @@
-old
+new
"""
    result = apply_unified_patch(tmp_path, patch)
    assert not result.applied


def test_patch_creates_new_file(tmp_path: Path):
    patch = """--- /dev/null
+++ b/new.txt
@@ -0,0 +1 @@
+hello
"""
    result = apply_unified_patch(tmp_path, patch)
    assert result.applied
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hello\n"


def test_patch_rejects_creation_over_existing_file(tmp_path: Path):
    path = tmp_path / "new.txt"
    path.write_text("existing\n", encoding="utf-8")
    patch = """--- /dev/null
+++ b/new.txt
@@ -0,0 +1 @@
+replacement
"""
    result = apply_unified_patch(tmp_path, patch)
    assert not result.applied
    assert path.read_text(encoding="utf-8") == "existing\n"


def test_patch_deletes_file(tmp_path: Path):
    path = tmp_path / "old.txt"
    path.write_text("one\ntwo\n", encoding="utf-8")
    patch = """--- a/old.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-one
-two
"""
    result = apply_unified_patch(tmp_path, patch)
    assert result.applied
    assert not path.exists()


def test_patch_multiple_files_is_transactional(tmp_path: Path):
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("A\n", encoding="utf-8")
    second.write_text("B\n", encoding="utf-8")
    patch = """--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-A
+A2
--- a/b.txt
+++ b/b.txt
@@ -1 +1 @@
-WRONG
+B2
"""
    result = apply_unified_patch(tmp_path, patch)
    assert not result.applied
    assert first.read_text(encoding="utf-8") == "A\n"
    assert second.read_text(encoding="utf-8") == "B\n"


def test_patch_rejects_overlapping_hunks(tmp_path: Path):
    path = tmp_path / "app.txt"
    path.write_text("a\nb\nc\n", encoding="utf-8")
    patch = """--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
-a
+A
@@ -1 +1 @@
-b
+B
"""
    result = apply_unified_patch(tmp_path, patch)
    assert not result.applied
    assert "overlap" in (result.error or "").lower()


def test_patch_preserves_no_newline_at_eof(tmp_path: Path):
    path = tmp_path / "app.txt"
    path.write_bytes(b"old")
    patch = """--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
-old
\ No newline at end of file
+new
\ No newline at end of file
"""
    result = apply_unified_patch(tmp_path, patch)
    assert result.applied
    assert path.read_bytes() == b"new"


def test_patch_rejects_malformed_extra_hunk_line(tmp_path: Path):
    path = tmp_path / "app.txt"
    path.write_text("old\n", encoding="utf-8")
    patch = """--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
-old
+new
unexpected
"""
    result = apply_unified_patch(tmp_path, patch)
    assert not result.applied
    assert path.read_text(encoding="utf-8") == "old\n"


def test_patch_handles_diff_line_starting_with_dashes(tmp_path: Path):
    path = tmp_path / "app.txt"
    path.write_text("--- old\n", encoding="utf-8")
    patch = """--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
---- old
+--- new
"""
    result = apply_unified_patch(tmp_path, patch)
    assert result.applied
    assert path.read_text(encoding="utf-8") == "--- new\n"
