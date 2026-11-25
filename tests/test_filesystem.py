from pathlib import Path
import pytest
from kardecagent.tools.filesystem import ProjectFilesystem
def test_read_write(tmp_path:Path):
    fs=ProjectFilesystem(tmp_path);fs.write_file("src/example.py","hello");assert fs.read_file("src/example.py")=="hello"
def test_escape(tmp_path:Path):
    with pytest.raises(ValueError):ProjectFilesystem(tmp_path).safe_path("../outside.txt")


def test_create_directory_and_append_file(tmp_path: Path):
    fs = ProjectFilesystem(tmp_path)
    fs.create_directory("src/nested")
    fs.append_file("src/nested/example.py", "print(1)\\n")
    fs.append_file("src/nested/example.py", "print(2)\\n")
    assert fs.read_file("src/nested/example.py") == "print(1)\\nprint(2)\\n"
    assert (tmp_path / "src/nested").is_dir()


def test_replace_in_file_is_exact_and_reports_missing_text(tmp_path: Path):
    fs = ProjectFilesystem(tmp_path)
    fs.write_file("src/example.py", "value = 1\\nvalue = 1\\n")
    assert fs.replace_in_file("src/example.py", "value = 1", "value = 2") == 1
    assert fs.read_file("src/example.py") == "value = 2\\nvalue = 1\\n"
    assert fs.replace_in_file("src/example.py", "not present", "x") == 0
