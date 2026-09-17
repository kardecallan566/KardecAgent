from pathlib import Path
import pytest
from kardecagent.config import resolve_project_root
def test_resolve_project_root(tmp_path:Path):assert resolve_project_root(tmp_path)==tmp_path.resolve()
def test_missing(tmp_path:Path):
    with pytest.raises(ValueError):resolve_project_root(tmp_path/"missing")
