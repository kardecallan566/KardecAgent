from __future__ import annotations
import subprocess
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class GitResult:
    command: str
    returncode: int
    stdout: str
    stderr: str

def _git(root: Path, args: list[str], timeout: float = 30.0) -> GitResult:
    command = 'git ' + ' '.join(args)
    try:
        p = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
        return GitResult(command, p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired as exc:
        return GitResult(command, -1, str(exc.stdout or ''), str(exc.stderr or 'timeout'))

def git_status(root: Path) -> GitResult: return _git(root, ['status','--short','--branch'])
def git_diff(root: Path) -> GitResult: return _git(root, ['diff','--no-ext-diff'])
def git_log(root: Path, limit: int = 10) -> GitResult: return _git(root, ['log',f'-{limit}','--oneline','--decorate'])

def create_checkpoint(root: Path, branch: str) -> GitResult:
    current = _git(root, ['rev-parse','--verify','HEAD'])
    if current.returncode != 0: return current
    return _git(root, ['branch', branch, 'HEAD'])

def rollback_to(root: Path, ref: str) -> GitResult:
    return _git(root, ['reset','--hard',ref])