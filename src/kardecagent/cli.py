from __future__ import annotations

import argparse
import logging

from .agent import AgentLoop
from .config import Settings, resolve_project_root
from .llm import LocalLLMClient


def build_parser():
    parser = argparse.ArgumentParser(
        prog="kardec-agent",
        description="Local-first autonomous coding agent.",
    )
    subs = parser.add_subparsers(dest="command", required=True)
    run = subs.add_parser("run", help="Create a plan, request approval, then execute it.")
    run.add_argument("--project", required=True)
    run.add_argument("--task", required=True)
    run.add_argument("--max-iterations", type=int, default=None)
    resume = subs.add_parser("resume", help="Resume a previously approved interrupted task.")
    resume.add_argument("--project", required=True)
    resume.add_argument("--task", required=True)
    resume.add_argument("--max-iterations", type=int, default=None)
    return parser


def _approve_plan(plan) -> bool:
    print()
    print(plan.format_for_review())
    print()
    while True:
        answer = input("Aprovar este plano e iniciar a execução? [s/N]: ").strip().lower()
        if answer in {"s", "sim", "y", "yes"}:
            return True
        if answer in {"", "n", "nao", "não", "no"}:
            return False
        print("Resposta inválida. Digite 's' para aprovar ou Enter/N para rejeitar.")



def _approve_high_risk(plan) -> bool:
    print()
    print("ATENÇÃO: esta tarefa foi classificada como HIGH_RISK.")
    print("Uma segunda aprovação é necessária antes de qualquer alteração.")
    print()
    print(plan.format_for_review())
    print()
    while True:
        answer = input("Confirmar a execução da tarefa de alto risco? [s/N]: ").strip().lower()
        if answer in {"s", "sim", "y", "yes"}:
            return True
        if answer in {"", "n", "nao", "não", "no"}:
            return False
        print("Resposta inválida. Digite 's' para aprovar ou Enter/N para rejeitar.")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    args = build_parser().parse_args()
    settings = Settings.from_env()

    if args.command in {"run", "resume"}:
        if args.max_iterations is not None:
            settings = Settings(**{
                **settings.__dict__,
                "max_iterations": args.max_iterations,
            })

        agent = AgentLoop(
            LocalLLMClient(
                settings.llm_base_url,
                settings.llm_model,
                settings.llm_api_key,
                settings.llm_timeout_seconds,
            ),
            settings,
        )
        root = resolve_project_root(args.project)
        if args.command == "run":
            state = agent.run(
                root, args.task,
                approval_callback=_approve_plan,
                high_risk_approval_callback=_approve_high_risk,
            )
        else:
            state = agent.resume(root, args.task)
        print(f"Status: {state.status.value}\nIterations: {state.iteration}")
        for event in state.events[-10:]:
            print(f"[{event.event_type}] {event.message}")
        return 0 if state.status.value == "completed" else 1

    return 2
