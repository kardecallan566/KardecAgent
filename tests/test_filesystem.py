from pathlib import Path
import pytest
from kardecagent.tools.filesystem import ProjectFilesystem
def test_read_write(tmp_path:Path):
    fs=ProjectFilesystem(tmp_path);fs.write_file("src/example.py","hello");assert fs.read_file("src/example.py")=="hello"
def test_escape(tmp_path:Path):
    with pytest.raises(ValueError):ProjectFilesystem(tmp_path).safe_path("../outside.txt")

def test_create_directory(tmp_path: Path):
    fs = ProjectFilesystem(tmp_path)
    fs.create_directory("src/components")
    assert (tmp_path / "src/components").is_dir()


def test_create_directory_rejects_escape(tmp_path: Path):
    with pytest.raises(ValueError):
        ProjectFilesystem(tmp_path).create_directory("../outside")
