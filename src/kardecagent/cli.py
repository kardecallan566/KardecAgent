from __future__ import annotations
import argparse,logging
from .agent import AgentLoop
from .config import Settings,resolve_project_root
from .llm import LocalLLMClient
def build_parser():
    p=argparse.ArgumentParser(prog="kardec-agent",description="Local-first autonomous coding agent.");s=p.add_subparsers(dest="command",required=True);r=s.add_parser("run",help="Run an agent task");r.add_argument("--project",required=True);r.add_argument("--task",required=True);r.add_argument("--max-iterations",type=int,default=None);return p
def main()->int:
    logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(message)s");a=build_parser().parse_args();settings=Settings.from_env()
    if a.command=="run":
        if a.max_iterations is not None:settings=Settings(**{**settings.__dict__,"max_iterations":a.max_iterations})
        state=AgentLoop(LocalLLMClient(settings.llm_base_url,settings.llm_model,settings.llm_api_key,settings.llm_timeout_seconds),settings).run(resolve_project_root(a.project),a.task)
        print(f"Status: {state.status.value}\nIterations: {state.iteration}");[print(f"[{e.event_type}] {e.message}") for e in state.events[-10:]];return 0 if state.status.value=="completed" else 1
    return 2
