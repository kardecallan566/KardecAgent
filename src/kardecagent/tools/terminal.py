from __future__ import annotations
import subprocess
from dataclasses import dataclass
from pathlib import Path
@dataclass
class CommandResult:
    command:str;returncode:int;stdout:str;stderr:str;timed_out:bool=False
DEFAULT_BLOCKED_PREFIXES=("format ","shutdown","restart-computer","stop-computer","diskpart","reg delete","remove-item -recurse","rm -rf","del /s")
def is_command_blocked(command:str)->bool:
    normalized=" ".join(command.strip().lower().split());return any(normalized.startswith(p) for p in DEFAULT_BLOCKED_PREFIXES)
def run_command(root:Path,command:str,*,timeout:float=120.0,max_output_chars:int=20000)->CommandResult:
    if is_command_blocked(command):raise PermissionError(f"Command blocked by safety policy: {command}")
    try:
        c=subprocess.run(command,cwd=root,shell=True,capture_output=True,text=True,timeout=timeout,encoding="utf-8",errors="replace")
        return CommandResult(command,c.returncode,c.stdout[-max_output_chars:],c.stderr[-max_output_chars:])
    except subprocess.TimeoutExpired as exc:return CommandResult(command,-1,str(exc.stdout or "")[-max_output_chars:],str(exc.stderr or "")[-max_output_chars:],True)
