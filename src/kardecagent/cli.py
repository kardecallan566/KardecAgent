from __future__ import annotations

import argparse
import logging

from .agent import AgentLoop
from .config import Settings, resolve_project_root
from .llm import LocalLLMClient, OllamaClient
from .llm.benchmark import default_models, run_benchmark, run_agentic_benchmark
from .project import detect_project
from .agent.persistence import TaskStore, PersistenceError


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
    doctor = subs.add_parser("doctor", help="Check local project and LLM configuration without changing files.")
    doctor.add_argument("--project", required=True)
    tasks = subs.add_parser("tasks", help="List persisted tasks for a project.")
    tasks.add_argument("--project", required=True)
    benchmark = subs.add_parser("benchmark", help="Benchmark local Ollama coding models.")
    benchmark.add_argument("--models", default=None, help="Comma-separated Ollama model names.")
    benchmark.add_argument("--timeout", type=float, default=300.0)
    benchmark.add_argument("--level", choices=("basic", "agentic"), default="agentic", help="Benchmark level (default: agentic).")
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


def _build_llm(settings: Settings):
    if settings.llm_provider == "ollama":
        return OllamaClient(
            settings.ollama_base_url,
            settings.llm_model,
            settings.llm_timeout_seconds,
        )
    if settings.llm_provider in {"openai-compatible", "local"}:
        return LocalLLMClient(
            settings.llm_base_url,
            settings.llm_model,
            settings.llm_api_key,
            settings.llm_timeout_seconds,
        )
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


def _doctor(settings: Settings, root) -> int:
    profile = detect_project(root)
    print("KardecAgent doctor")
    print(f"[OK] Project: {root}")
    print(f"[OK] Kind: {profile.kind}")
    print(f"[OK] Language: {profile.language}")
    print(f"[OK] Framework: {profile.framework or 'none'}")
    print(f"[OK] Package manager: {profile.package_manager or 'none'}")
    print(f"[OK] LLM provider: {settings.llm_provider}")
    print(f"[OK] LLM model: {settings.llm_model}")

    try:
        if settings.llm_provider == "ollama":
            client = OllamaClient(
                settings.ollama_base_url,
                settings.llm_model,
                settings.llm_timeout_seconds,
            )
            models = client.list_models()
            names = {item.get("name") for item in models if isinstance(item, dict)}
            if settings.llm_model not in names:
                print(f"[FAIL] Ollama model not installed: {settings.llm_model}")
                print("Installed models: " + ", ".join(sorted(str(name) for name in names if name)))
                return 1
            response = client.health_check()
        else:
            client = _build_llm(settings)
            response = client.chat(
                [
                    {"role": "system", "content": "Reply with exactly: KARDECAGENT_OK"},
                    {"role": "user", "content": "Health check."},
                ],
                temperature=0.0,
            )

        if response.content.strip() != "KARDECAGENT_OK":
            print("[FAIL] LLM responded, but health-check content was unexpected.")
            return 1
        print("[OK] Local LLM: reachable and responding")
        return 0
    except Exception as exc:
        print(f"[FAIL] Local LLM: {exc}")
        return 1


def _benchmark(settings: Settings, models_arg: str | None, timeout: float, level: str = "agentic") -> int:
    if settings.llm_provider != "ollama":
        print("[FAIL] benchmark requires LLM provider 'ollama'.")
        return 1
    models = tuple(x.strip() for x in models_arg.split(",") if x.strip()) if models_arg else default_models()
    client = OllamaClient(settings.ollama_base_url, models[0], timeout)
    try:
        installed = {item.get("name") for item in client.list_models() if isinstance(item, dict)}
    except Exception as exc:
        print(f"[FAIL] Ollama: {exc}")
        return 1

    print("KardecAgent Ollama benchmark")
    print("Level:", level)
    print("Models:", ", ".join(models))
    print()
    overall: list[tuple[str, int, int, float, float]] = []
    for model in models:
        if model not in installed:
            print(f"[SKIP] {model} - not installed")
            continue
        model_client = OllamaClient(settings.ollama_base_url, model, timeout)
        try:
            results = (
                run_agentic_benchmark(model_client)
                if level == "agentic"
                else run_benchmark(model_client)
            )
        except Exception as exc:
            print(f"[FAIL] {model} - {exc}")
            continue
        passed = sum(r.passed for r in results)
        total = len(results)
        elapsed = sum(r.elapsed_seconds for r in results)
        tps = sum(r.tokens_per_second for r in results if r.tokens_per_second) / max(
            1, sum(1 for r in results if r.tokens_per_second)
        )
        overall.append((model, passed, total, elapsed, tps))
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            details = ""
            if level == "agentic":
                details = (
                    f" | attempts={result.attempts}"
                    f" | tool_calls={result.tool_calls}"
                    f" | recovery={result.recovery_attempts}"
                    f" | first_pass={'yes' if result.first_attempt_passed else 'no'}"
                    f" | reads={result.reads}"
                    f" | writes={result.writes}"
                    f" | runs={result.runs}"
                    f" | dones={result.dones}"
                    f" | invalid={result.invalid_actions}"
                )
            print(
                f"[{status}] {model} / {result.case} | "
                f"{result.elapsed_seconds:.2f}s | {result.tokens_per_second:.2f} tok/s"
                f"{details}"
            )
            if not result.passed and getattr(result, "error", ""):
                print(f"  error: {result.error}")
            if level == "agentic" and getattr(result, "action_trace", ()):
                print("  trace:")
                for action in result.action_trace:
                    print(f"    {action}")

    if not overall:
        print("No installed benchmark models were found.")
        return 1

    print("Summary")
    for model, passed, total, elapsed, tps in overall:
        print(f"- {model}: {passed}/{total} passed | {elapsed:.2f}s total | {tps:.2f} tok/s avg")
    if level == "agentic":
        print()
        print("Agentic metrics: attempts = test executions; recovery = failed test runs; first_pass = passed on first test; reads/writes/runs/dones = recognized actions; invalid = malformed or premature actions.")
    print()
    print("Use the results to choose the model; no automatic winner is selected.")
    return 0


def _tasks(root) -> int:
    store = TaskStore(root)
    try:
        paths = sorted(store.directory.glob("*.json"))
    except OSError as exc:
        print(f"[FAIL] Cannot read task store: {exc}")
        return 1
    if not paths:
        print("No persisted tasks.")
        return 0
    print("Persisted tasks:")
    for path in paths:
        task_id = path.stem
        try:
            state, plan, board = store.load(task_id)
            print(f"- {task_id}: {state.status.value} | iteration={state.iteration} | plan={plan.summary}")
            if board is not None:
                print(f"  subtasks: {len(board.subtasks)} | completed={board.completed}")
        except PersistenceError as exc:
            print(f"- {task_id}: CORRUPT/UNREADABLE | {exc}")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    args = build_parser().parse_args()
    settings = Settings.from_env()

    if args.command == "doctor":
        root = resolve_project_root(args.project)
        return _doctor(settings, root)
    if args.command == "tasks":
        root = resolve_project_root(args.project)
        return _tasks(root)
    if args.command == "benchmark":
        return _benchmark(settings, args.models, args.timeout, args.level)

    if args.max_iterations is not None:
        settings = Settings(**{
            **settings.__dict__,
            "max_iterations": args.max_iterations,
        })

    agent = AgentLoop(_build_llm(settings), settings)
    root = resolve_project_root(args.project)
    if args.command == "run":
        state = agent.run(root, args.task, approval_callback=_approve_plan,
                          high_risk_approval_callback=_approve_high_risk)
    else:
        state = agent.resume(root, args.task)
    print(f"Status: {state.status.value}\nIterations: {state.iteration}")
    for event in state.events[-10:]:
        print(f"[{event.event_type}] {event.message}")
    return 0 if state.status.value == "completed" else 1
