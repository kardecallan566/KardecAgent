from pathlib import Path
from kardecagent.project.detector import detect_project, discover_command

def test_detect_pnpm_project(tmp_path: Path):
    (tmp_path/'package.json').write_text('{"scripts":{"test":"vitest","lint":"eslint ."},"dependencies":{"expo":"^54"}}',encoding='utf-8')
    (tmp_path/'pnpm-lock.yaml').write_text('',encoding='utf-8')
    profile=detect_project(tmp_path)
    assert profile.language=='javascript'
    assert profile.framework=='expo'
    assert profile.package_manager=='pnpm'
    assert profile.commands['test']=='pnpm run test'

def test_python_command(tmp_path: Path):
    (tmp_path/'pyproject.toml').write_text('[project]\nname="x"',encoding='utf-8')
    assert discover_command(tmp_path,'test')=='python -m pytest'

def test_missing_check(tmp_path: Path):
    assert discover_command(tmp_path,'build') is None