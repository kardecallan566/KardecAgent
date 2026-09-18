from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Callable

from .ollama import OllamaClient


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    prompt: str
    check: Callable[[str], bool]


@dataclass(frozen=True)
class BenchmarkResult:
    model: str
    case: str
    passed: bool
    elapsed_seconds: float
    eval_tokens: int
    tokens_per_second: float
    response: str


CASES = (
    BenchmarkCase(
        "python_function",
        "Write only a Python function named add(a, b) that returns the sum of a and b.",
        lambda text: "def add" in text and "return" in text,
    ),
    BenchmarkCase(
        "bug_fix",
        "Fix this Python bug. Return only corrected code: def is_even(n): return n % 2 == 1",
        lambda text: "n % 2 == 0" in text or "n % 2 != 1" in text,
    ),
    BenchmarkCase(
        "test_creation",
        "Write only a pytest test that asserts add(2, 3) equals 5.",
        lambda text: "assert" in text and "2" in text and "3" in text and "5" in text,
    ),
    BenchmarkCase(
        "planning",
        "Give a concise implementation plan with exactly 3 numbered steps for adding input validation to a Python function.",
        lambda text: sum(1 for line in text.splitlines() if line.strip().startswith(("1.", "2.", "3."))) >= 3,
    ),
    BenchmarkCase(
        "traceback",
        "A Python program raises NameError: name 'total' is not defined. Explain the direct cause and one concrete fix in two sentences or fewer.",
        lambda text: "total" in text.lower() and ("defined" in text.lower() or "define" in text.lower()),
    ),
)


def default_models() -> tuple[str, ...]:
    return ("qwen2.5-coder:1.5b", "qwen2.5-coder:3b", "qwen2.5-coder:7b")


def run_benchmark(
    client: OllamaClient,
    *,
    cases: tuple[BenchmarkCase, ...] = CASES,
) -> list[BenchmarkResult]:
    results: list[BenchmarkResult] = []
    for case in cases:
        started = perf_counter()
        response = client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a coding benchmark. Follow the requested output format "
                        "strictly. Do not discuss the benchmark."
                    ),
                },
                {"role": "user", "content": case.prompt},
            ],
            temperature=0.0,
        )
        elapsed = perf_counter() - started
        raw = response.raw
        eval_tokens = int(raw.get("eval_count") or 0)
        eval_duration_ns = int(raw.get("eval_duration") or 0)
        tokens_per_second = (
            eval_tokens / (eval_duration_ns / 1_000_000_000)
            if eval_tokens and eval_duration_ns
            else 0.0
        )
        results.append(
            BenchmarkResult(
                model=client.model,
                case=case.name,
                passed=case.check(response.content),
                elapsed_seconds=elapsed,
                eval_tokens=eval_tokens,
                tokens_per_second=tokens_per_second,
                response=response.content.strip(),
            )
        )
    return results
