from __future__ import annotations

import argparse
import json
import logging

from .agent import AgentLoop
from .config import Settings, resolve_project_root
from .llm import AdaptiveLLMClient, LocalLLMClient, OllamaClient
from .llm.router import EscalatingLLMClient
from .llm.benchmark import default_models, run_benchmark, run_agentic_benchmark
from .llm.catalog import LOCAL_MODEL_CATALOG
from .project import detect_project
from .agent.persistence import TaskStore, PersistenceError

logger = logging.getLogger(__name__)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="kardec-agent",
        description="Local-first autonomous coding agent.",
    )
    subs = parser.add_subparsers(dest="command", required=True)
    run = subs.add_parser("run", help="Create a plan, request approval, then execute it.")
    run.add_argument("--project", required=True)
    run.add_argument("--task", required=True)
    run.add_argument("--model", default=None, help="LLM model name (e.g. qwen2.5-coder:7b).")
    run.add_argument("--provider", default=None, choices=("ollama", "openai-compatible", "local"), help="LLM provider (default: ollama).")
    run.add_argument("--timeout", type=float, default=None, help="Timeout in seconds for LLM calls (default: 180.0).")
    run.add_argument("--max-iterations", type=int, default=None)
    resume = subs.add_parser("resume", help="Resume a previously approved interrupted task.")
    resume.add_argument("--project", required=True)
    resume.add_argument("--task", required=True)
    resume.add_argument("--model", default=None, help="LLM model name.")
    resume.add_argument("--provider", default=None, choices=("ollama", "openai-compatible", "local"), help="LLM provider.")
    resume.add_argument("--timeout", type=float, default=None, help="Timeout in seconds for LLM calls (default: 180.0).")
    resume.add_argument("--max-iterations", type=int, default=None)
    doctor = subs.add_parser("doctor", help="Check local project and LLM configuration without changing files.")
    doctor.add_argument("--project", required=True)
    doctor.add_argument("--model", default=None, help="LLM model name to check.")
    doctor.add_argument("--provider", default=None, choices=("ollama", "openai-compatible", "local"), help="LLM provider to check.")
    tasks = subs.add_parser("tasks", help="List persisted tasks for a project.")
    tasks.add_argument("--project", required=True)
    studio = subs.add_parser("studio", help="Run the JSONL desktop Studio runtime.")
    studio.add_argument("--project", required=True)
    subs.add_parser("ollama-health", help="Return a machine-readable Ollama health check for the desktop Studio.")
    models = subs.add_parser("models", help="List installed Ollama models and a curated local catalog.")
    models.add_argument("--installed-only", action="store_true", help="Show only models installed in Ollama.")
    models.add_argument("--inspect", default=None, help="Inspect metadata for an installed model tag.")
    benchmark = subs.add_parser("benchmark", help="Benchmark local Ollama coding models.")
    benchmark.add_argument("--models", default=None, help="Comma-separated Ollama model names.")
    benchmark.add_argument("--timeout", type=float, default=180.0)
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


def _approve_model_switch(request) -> bool:
    print()
    print(request.format_for_review())
    print()
    while True:
        answer = input("Autorizar a troca de modelo? [s/N]: ").strip().lower()
        if answer in {"s", "sim", "y", "yes"}:
            return True
        if answer in {"", "n", "nao", "não", "no"}:
            return False
        print("Resposta inválida. Digite 's' para aprovar ou Enter/N para rejeitar.")


def _build_llm(settings: Settings):
    def build_client(model: str):
        if settings.llm_provider == "ollama":
            return OllamaClient(
                settings.ollama_base_url,
                model,
                settings.llm_timeout_seconds,
            )
        if settings.llm_provider in {"openai-compatible", "local"}:
            return LocalLLMClient(
                settings.llm_base_url,
                model,
                settings.llm_api_key,
                settings.llm_timeout_seconds,
            )
        raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")

    if settings.llm_provider == "ollama":
        # Ollama is the source of truth: never require users to maintain a
        # second, manually curated escalation list.
        primary = build_client(settings.llm_model)
        inventory = primary.list_models()
        installed = [
            item for item in inventory
            if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip()
        ]
        if not installed:
            raise RuntimeError(
                f"No Ollama models are installed at {settings.ollama_base_url}. "
                "Pull a model with Ollama before starting KardecAgent."
            )

        by_name = {item["name"]: item for item in installed}
        configured = settings.llm_model.strip()
        if configured and configured in by_name:
            primary_name = configured
        else:
            def primary_key(item):
                name = item["name"].casefold()
                coding_hint = any(
                    marker in name
                    for marker in ("coder", "code", "deepseek", "qwen", "starcoder", "codegemma")
                )
                size = item.get("size")
                size_key = float(size) if isinstance(size, (int, float)) else float("inf")
                return (0 if coding_hint else 1, size_key, name)

            primary_name = min(installed, key=primary_key)["name"]
            logger.info(
                "Configured Ollama model %r is unavailable; auto-selected installed model %r",
                configured or None,
                primary_name,
            )
        primary.model = primary_name
        primary_size = by_name[primary_name].get("size")

        base = primary
        fast_name = settings.llm_fast_model.strip() if settings.llm_routing_enabled else ""
        if fast_name not in by_name:
            fast_name = ""
        if settings.llm_routing_enabled and not fast_name:
            smaller = [
                item for item in installed
                if item["name"] != primary_name
                and isinstance(item.get("size"), (int, float))
                and isinstance(primary_size, (int, float))
                and item["size"] < primary_size
            ]
            if smaller:
                fast_name = min(smaller, key=lambda item: (item["size"], item["name"]))["name"]
        if settings.llm_routing_enabled and fast_name and fast_name != primary_name:
            base = AdaptiveLLMClient(primary, build_client(fast_name))

        alternatives = []
        for item in sorted(
            installed,
            key=lambda candidate: (
                candidate.get("size") if isinstance(candidate.get("size"), (int, float)) else float("inf"),
                candidate["name"],
            ),
        ):
            name = item["name"]
            if name in {primary_name, fast_name}:
                continue
            size = item.get("size")
            # Escalation must not silently downgrade. When Ollama does not
            # report sizes, retain deterministic discovery order by name.
            if isinstance(primary_size, (int, float)) and isinstance(size, (int, float)) and size <= primary_size:
                continue
            alternatives.append(build_client(name))

        def refresh_alternatives():
            latest = primary.list_models()
            discovered = [
                item for item in latest
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and item["name"].strip()
            ]
            refreshed = []
            for item in sorted(
                discovered,
                key=lambda candidate: (
                    candidate.get("size") if isinstance(candidate.get("size"), (int, float)) else float("inf"),
                    candidate["name"],
                ),
            ):
                name = item["name"]
                if name in {primary_name, fast_name}:
                    continue
                size = item.get("size")
                if (
                    isinstance(primary_size, (int, float))
                    and isinstance(size, (int, float))
                    and size <= primary_size
                ):
                    continue
                refreshed.append(build_client(name))
            return refreshed

        return (
            EscalatingLLMClient(base, alternatives, alternatives_provider=refresh_alternatives)
            if alternatives or settings.llm_provider == "ollama"
            else base
        )

    primary = build_client(settings.llm_model)
    base = primary
    if settings.llm_routing_enabled:
        if not settings.llm_fast_model:
            raise ValueError(
                "Model routing is enabled but KARDECAGENT_FAST_MODEL is empty. "
                "Configure a fast model or disable KARDECAGENT_MODEL_ROUTING_ENABLED."
            )
        if settings.llm_fast_model != settings.llm_model:
            base = AdaptiveLLMClient(primary, build_client(settings.llm_fast_model))
    alternatives = [
        build_client(model)
        for model in settings.llm_escalation_models
        if model and model not in {settings.llm_model, settings.llm_fast_model}
    ]
    return EscalatingLLMClient(base, alternatives) if alternatives else base

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
            inventory_client = OllamaClient(
                settings.ollama_base_url,
                settings.llm_model,
                settings.llm_timeout_seconds,
            )
            models = inventory_client.list_models()
            names = sorted({
                str(item.get("name"))
                for item in models
                if isinstance(item, dict) and item.get("name")
            })
            client = _build_llm(settings)
            print("[OK] Ollama installed models: " + (", ".join(names) if names else "(none)"))
            print(f"[OK] Active primary model: {client.model}")
            response = client.chat(
                [
                    {"role": "system", "content": "Reply with exactly: KARDECAGENT_OK"},
                    {"role": "user", "content": "Health check."},
                ],
                temperature=0.0,
            )
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



def _ollama_health(settings: Settings) -> int:
    """Emit exactly one JSON object for the desktop Studio health-check bridge."""
    try:
        if settings.llm_provider != "ollama":
            raise RuntimeError("The Studio health check currently requires the Ollama provider.")
        inventory_client = OllamaClient(
            settings.ollama_base_url,
            settings.llm_model,
            settings.llm_timeout_seconds,
        )
        version = inventory_client.server_version()
        models = inventory_client.list_models()
        names = sorted({
            str(item.get("name"))
            for item in models
            if isinstance(item, dict) and item.get("name")
        })
        client = _build_llm(settings)
        response = client.chat(
            [
                {"role": "system", "content": "Reply with exactly: KARDECAGENT_OK"},
                {"role": "user", "content": "Health check."},
            ],
            temperature=0.0,
        )
        if response.content.strip() != "KARDECAGENT_OK":
            raise RuntimeError("Ollama responded, but the health-check content was unexpected.")
        print(json.dumps({
            "version": version,
            "models": names,
            "model": client.model,
            "fast_model": settings.llm_fast_model or None,
            "routing_enabled": settings.llm_routing_enabled,
            "response": response.content.strip(),
        }, ensure_ascii=True))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True))
        return 1

def _benchmark(settings: Settings, models_arg: str | None, timeout: float, level: str = "agentic") -> int:
    overall: list[tuple[str, int, int, float, float]] = []

    if settings.llm_provider in {"openai-compatible", "local"}:
        models = tuple(x.strip() for x in models_arg.split(",") if x.strip()) if models_arg else (settings.llm_model,)
        print(f"KardecAgent OpenAI-Compatible benchmark ({settings.llm_base_url})")
        print("Level:", level)
        print("Models:", ", ".join(models))
        print()
        for model in models:
            model_client = LocalLLMClient(settings.llm_base_url, model, settings.llm_api_key, timeout)
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
                if level == "agentic" and getattr(result, "recovery_feedback", ()):
                    print("  recovery feedback:")
                    for feedback in result.recovery_feedback:
                        print(f"    {feedback.replace(chr(10), chr(10) + '    ')}")
        if not overall:
            print("Benchmark completed with no successful model runs.")
            return 1
        print("\nSummary")
        for model, passed, total, elapsed, tps in overall:
            print(f"- {model}: {passed}/{total} passed | {elapsed:.2f}s total | {tps:.2f} tok/s avg")
        return 0

    if settings.llm_provider != "ollama":
        print(f"[FAIL] Unsupported LLM provider for benchmark: {settings.llm_provider}")
        return 1

    candidate_models = tuple(x.strip() for x in models_arg.split(",") if x.strip()) if models_arg else default_models()
    probe_model = candidate_models[0] if candidate_models else "qwen2.5-coder:3b"
    client = OllamaClient(settings.ollama_base_url, probe_model, timeout)
    try:
        installed = {str(item.get("name")) for item in client.list_models() if isinstance(item, dict) and item.get("name")}
    except Exception as exc:
        print(f"[FAIL] Ollama: {exc}")
        return 1

    # Match models with or without tags
    models_to_run: list[str] = []
    for model in candidate_models:
        if model in installed:
            models_to_run.append(model)
        elif f"{model}:latest" in installed:
            models_to_run.append(f"{model}:latest")
        elif any(inst.startswith(f"{model}:") for inst in installed):
            matched = next(inst for inst in sorted(installed) if inst.startswith(f"{model}:"))
            models_to_run.append(matched)

    if not models_to_run and not models_arg and installed:
        # Fallback to installed coding/general models
        models_to_run = sorted(list(installed))

    print("KardecAgent Ollama benchmark")
    print("Level:", level)
    print("Models to test:", ", ".join(models_to_run) if models_to_run else "None")
    print()

    for model in candidate_models:
        resolved = None
        if model in installed:
            resolved = model
        elif f"{model}:latest" in installed:
            resolved = f"{model}:latest"
        elif any(inst.startswith(f"{model}:") for inst in installed):
            resolved = next(inst for inst in sorted(installed) if inst.startswith(f"{model}:"))

        if not resolved and models_arg:
            print(f"[SKIP] {model} - not installed")
            continue
        elif not resolved:
            continue

        model_client = OllamaClient(settings.ollama_base_url, resolved, timeout)
        try:
            results = (
                run_agentic_benchmark(model_client)
                if level == "agentic"
                else run_benchmark(model_client)
            )
        except Exception as exc:
            print(f"[FAIL] {resolved} - {exc}")
            continue
        passed = sum(r.passed for r in results)
        total = len(results)
        elapsed = sum(r.elapsed_seconds for r in results)
        tps = sum(r.tokens_per_second for r in results if r.tokens_per_second) / max(
            1, sum(1 for r in results if r.tokens_per_second)
        )
        overall.append((resolved, passed, total, elapsed, tps))
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
            if level == "agentic" and getattr(result, "recovery_feedback", ()):
                print("  recovery feedback:")
                for feedback in result.recovery_feedback:
                    print(f"    {feedback.replace(chr(10), chr(10) + '    ')}")

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


def _models(settings: Settings, installed_only: bool = False, inspect_model: str | None = None) -> int:
    if settings.llm_provider != "ollama":
        print("The installed-model inventory currently requires provider=ollama.")
        print(f"Configured provider: {settings.llm_provider}")
        return 1
    client = OllamaClient(settings.ollama_base_url, settings.llm_model, settings.llm_timeout_seconds)
    try:
        installed = client.list_models()
    except Exception as exc:
        print(f"[FAIL] Ollama unavailable at {settings.ollama_base_url}: {exc}")
        return 1
    print(f"Ollama: connected ({settings.ollama_base_url})")
    try:
        version = client.server_version()
    except Exception:
        version = "unknown"
    print(f"Ollama: connected ({settings.ollama_base_url}) | version={version}")
    print(f"Configured model: {settings.llm_model}")
    if inspect_model:
        try:
            metadata = client.show_model(inspect_model)
        except Exception as exc:
            print(f"[FAIL] Could not inspect {inspect_model}: {exc}")
            return 1
        details = metadata.get("details", {})
        print(f"Model metadata: {inspect_model}")
        for key in ("family", "parameter_size", "quantization_level", "format"):
            if details.get(key):
                print(f"  {key}: {details[key]}")
        print("  capabilities: " + ", ".join(metadata.get("capabilities", [])))
        model_info = metadata.get("model_info", {})
        context_values = [value for key, value in model_info.items() if key.endswith("context_length")]
        if context_values:
            print(f"  context_length: {max(context_values)}")
        return 0
    print("Installed models:")
    if not installed:
        print("  (none)")
    for item in installed:
        if isinstance(item, dict):
            name = item.get("name", "unknown")
            size = item.get("size")
            size_text = f" | {size / (1024**3):.1f} GiB on disk" if isinstance(size, (int, float)) else ""
            print(f"  - {name}{size_text}")
    if installed_only:
        return 0
    print("Curated local model candidates (indicative memory; benchmark before routing):")
    names = {str(item.get("name", "")) for item in installed if isinstance(item, dict)}
    for spec in LOCAL_MODEL_CATALOG:
        marker = " [installed]" if spec.name in names else ""
        print(f"  - {spec.name}{marker} | {spec.vram_gb} | {spec.role} | {spec.notes}")
    print("Note: model weights are not peak VRAM. Context/KV cache and runtime buffers add memory.")
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

    model_override = getattr(args, "model", None)
    provider_override = getattr(args, "provider", None)
    timeout_override = getattr(args, "timeout", None)
    updates = {}
    if model_override:
        updates["llm_model"] = model_override
    if provider_override:
        updates["llm_provider"] = provider_override
    if timeout_override is not None:
        updates["llm_timeout_seconds"] = timeout_override
    if updates:
        settings = Settings(**{**settings.__dict__, **updates})

    if args.command == "doctor":
        root = resolve_project_root(args.project)
        return _doctor(settings, root)
    if args.command == "tasks":
        root = resolve_project_root(args.project)
        return _tasks(root)
    if args.command == "studio":
        from .studio_protocol import run_stdio
        root = resolve_project_root(args.project)
        return run_stdio(root, AgentLoop(_build_llm(settings), settings))
    if args.command == "ollama-health":
        return _ollama_health(settings)
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
                          high_risk_approval_callback=_approve_high_risk,
                          model_switch_approval_callback=_approve_model_switch)
    else:
        state = agent.resume(
            root, args.task,
            model_switch_approval_callback=_approve_model_switch,
        )
    print(f"Status: {state.status.value}")
    print(f"Iterations: {state.iteration}")
    for event in state.events[-10:]:
        print(f"[{event.event_type}] {event.message}")
    return 0 if state.status.value == "completed" else 1
