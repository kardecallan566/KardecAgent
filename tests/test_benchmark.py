from kardecagent.llm.benchmark import CASES, default_models, run_benchmark, run_agentic_benchmark


class FakeClient:
    model = "qwen2.5-coder:3b"

    def chat(self, messages, *, temperature=0.0):
        class Response:
            content = "def add(a, b):\n    return a + b"
            raw = {"eval_count": 10, "eval_duration": 2_000_000_000}

        return Response()


def test_default_benchmark_models():
    assert default_models() == (
        "qwen2.5-coder:1.5b",
        "qwen2.5-coder:3b",
        "qwen2.5-coder:7b",
        "qwen3.5:4b",
        "deepseek-coder:1.3b-instruct",
        "deepseek-coder:6.7b",
    )


def test_benchmark_cases_are_present():
    assert len(CASES) == 5


def test_run_benchmark_collects_metrics():
    results = run_benchmark(FakeClient(), cases=CASES[:1])
    assert len(results) == 1
    assert results[0].model == "qwen2.5-coder:3b"
    assert results[0].passed is True
    assert results[0].eval_tokens == 10
    assert results[0].tokens_per_second == 5.0


def test_agentic_benchmark_has_real_coding_cases():
    from kardecagent.llm.benchmark import _fixture_cases
    cases = _fixture_cases()
    assert len(cases) >= 12
    assert any(c.test_command == ("python", "-m", "pytest", "-q") for c in cases)


def test_agentic_file_parser_rejects_unsafe_paths():
    import pytest
    from kardecagent.llm.benchmark import _parse_files

    with pytest.raises(ValueError):
        _parse_files("=== FILE: ../escape.py ===\nprint(1)\n=== END FILE ===")


def test_agentic_file_parser_accepts_complete_file_blocks():
    from kardecagent.llm.benchmark import _parse_files

    files = _parse_files("=== FILE: src/a.py ===\nprint('ok')\n=== END FILE ===")
    assert files == {"src/a.py": "print('ok')\n"}


class FakeAgenticClient:
    model = "fake-agent"

    def __init__(self, responses):
        self.responses = iter(responses)

    def chat(self, messages, *, temperature=0.0):
        content = next(self.responses)
        class Response:
            raw = {"eval_count": 10, "eval_duration": 1_000_000_000}
        Response.content = content
        return Response()


def test_agentic_benchmark_runs_read_write_and_test_loop():
    from kardecagent.llm.benchmark import _fixture_cases

    client = FakeAgenticClient([
        "=== READ: src/math_utils.py ===\n=== END READ ===",
        """=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    if minimum > maximum:
        raise ValueError("invalid range")
    return max(minimum, min(value, maximum))
=== END WRITE ===""",
    ])
    case = _fixture_cases()[0]
    result = run_agentic_benchmark(client, cases=(case,), max_steps=2)[0]
    assert result.passed is True
    assert result.attempts == 1
    assert result.tool_calls == 2
    assert result.first_attempt_passed is True
    assert result.recovery_attempts == 0
    assert result.eval_tokens == 20
    assert result.reads == 1
    assert result.writes == 1
    assert result.runs == 1
    assert result.dones == 0
    assert result.invalid_actions == 0
    assert result.action_trace == (
        "step=1 READ src/math_utils.py",
        "step=2 WRITE src/math_utils.py",
        "step=2 AUTO_RUN python -m pytest -q PASS",
    )


def test_agentic_benchmark_recovers_after_failed_test():
    from kardecagent.llm.benchmark import _fixture_cases

    client = FakeAgenticClient([
        "=== READ: src/math_utils.py ===\n=== END READ ===",
        """=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    return value
=== END WRITE ===""",
        "=== RUN: python -m pytest -q ===\n=== END RUN ===",
        """=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    if minimum > maximum:
        raise ValueError("invalid range")
    return max(minimum, min(value, maximum))
=== END WRITE ===""",
        "=== RUN: python -m pytest -q ===\n=== END RUN ===",
    ])
    case = _fixture_cases()[0]
    result = run_agentic_benchmark(client, cases=(case,), max_steps=5)[0]
    assert result.passed is True
    assert result.attempts == 2
    assert result.recovery_attempts == 1
    assert result.first_attempt_passed is False


def test_agentic_benchmark_records_invalid_response_and_recovers_protocol():
    from kardecagent.llm.benchmark import _fixture_cases

    client = FakeAgenticClient([
        "I will inspect the project first.",
        "=== READ: src/math_utils.py ===\n=== END READ ===",
        """=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    if minimum > maximum:
        raise ValueError("invalid range")
    return max(minimum, min(value, maximum))
=== END WRITE ===""",
        "=== RUN: python -m pytest -q ===\n=== END RUN ===",
    ])
    result = run_agentic_benchmark(client, cases=(_fixture_cases()[0],), max_steps=4)[0]
    assert result.passed is True
    assert result.invalid_actions == 1
    assert result.reads == 1
    assert result.writes == 1
    assert result.runs == 1
    assert result.action_trace[0].startswith("step=1 INVALID")
    assert result.action_trace[-1].endswith("PASS")

def test_normalize_agentic_prompt_removes_legacy_file_protocol():
    from kardecagent.llm.benchmark import _normalize_agentic_prompt

    prompt = """Implement the feature. Return ONLY complete modified files as FILE blocks.
Format:
=== FILE: path ===
<complete file>
=== END FILE ===
Task: add the feature."""
    normalized = _normalize_agentic_prompt(prompt)
    assert "Return ONLY complete modified files as FILE blocks" not in normalized
    assert "=== FILE: path ===" not in normalized
    assert "READ/WRITE/RUN" in normalized


def test_agentic_benchmark_blocks_duplicate_reads_and_repeated_runs():
    from kardecagent.llm.benchmark import _fixture_cases

    client = FakeAgenticClient([
        "=== READ: src/cart.py ===\n=== END READ ===",
        "=== RUN: python -m pytest -q ===\n=== END RUN ===",
        "=== RUN: python -m pytest -q ===\n=== END RUN ===",
        """=== WRITE: src/cart.py ===
def total(items):
    if not items:
        return 0
    total = 0
    for item in items:
        total += item["price"] * item.get("quantity", 1)
    return total
=== END WRITE ===""",
        "=== RUN: python -m pytest -q ===\n=== END RUN ===",
    ])
    case = _fixture_cases()[1]
    result = run_agentic_benchmark(client, cases=(case,), max_steps=5)[0]

    assert result.passed is True
    assert result.runs == 2
    assert result.writes == 1
    assert result.recovery_attempts == 1
    assert any("BLOCKED_AFTER_FAIL" in item for item in result.action_trace)


def test_agentic_benchmark_executes_batched_actions_in_order():
    from kardecagent.llm.benchmark import _fixture_cases

    client = FakeAgenticClient([
        """=== READ: src/math_utils.py ===
=== END READ ===
=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    if minimum > maximum:
        raise ValueError("invalid range")
    return max(minimum, min(value, maximum))
=== END WRITE ===
=== RUN: python -m pytest -q ===
=== END RUN ===""",
    ])
    result = run_agentic_benchmark(client, cases=(_fixture_cases()[0],), max_steps=1)[0]

    assert result.passed is True
    assert result.tool_calls == 3
    assert result.reads == 1
    assert result.writes == 1
    assert result.runs == 1
    assert result.invalid_actions == 0
    assert result.action_trace == (
        "step=1 READ src/math_utils.py",
        "step=1 WRITE src/math_utils.py",
        "step=1 RUN python -m pytest -q PASS",
    )



def test_agentic_benchmark_auto_validates_after_write_without_run():
    from kardecagent.llm.benchmark import _fixture_cases

    client = FakeAgenticClient([
        """=== READ: src/math_utils.py ===
=== END READ ===
=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    if minimum > maximum:
        raise ValueError("invalid range")
    return max(minimum, min(value, maximum))
=== END WRITE ===
=== DONE: success ===""",
    ])
    result = run_agentic_benchmark(client, cases=(_fixture_cases()[0],), max_steps=1)[0]

    assert result.passed is True
    assert result.attempts == 1
    assert result.runs == 1
    assert result.writes == 1
    assert result.dones == 1
    assert any("AUTO_RUN python -m pytest -q PASS" in item for item in result.action_trace)


def test_agentic_benchmark_stops_batched_actions_after_failed_run():
    from kardecagent.llm.benchmark import _fixture_cases

    client = FakeAgenticClient([
        """=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    return value
=== END WRITE ===
=== RUN: python -m pytest -q ===
=== END RUN ===
=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    if minimum > maximum:
        raise ValueError("invalid range")
    return max(minimum, min(value, maximum))
=== END WRITE ===""",
        """=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    if minimum > maximum:
        raise ValueError("invalid range")
    return max(minimum, min(value, maximum))
=== END WRITE ===
=== RUN: python -m pytest -q ===
=== END RUN ===""",
    ])
    result = run_agentic_benchmark(client, cases=(_fixture_cases()[0],), max_steps=2)[0]

    assert result.passed is True
    assert result.attempts == 2
    assert result.recovery_attempts == 1
    assert result.writes == 2
    assert result.runs == 2
    assert result.action_trace[1].endswith("RUN python -m pytest -q FAIL")
    assert result.action_trace[2] == "step=2 WRITE src/math_utils.py"


def test_agentic_benchmark_rejects_duplicate_read_without_executing_it():
    from kardecagent.llm.benchmark import _fixture_cases

    client = FakeAgenticClient([
        "=== READ: src/math_utils.py ===\n=== END READ ===",
        "=== READ: src/math_utils.py ===\n=== END READ ===",
        """=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    if minimum > maximum:
        raise ValueError("invalid range")
    return max(minimum, min(value, maximum))
=== END WRITE ===""",
        "=== RUN: python -m pytest -q ===\n=== END RUN ===",
    ])
    case = _fixture_cases()[0]
    result = run_agentic_benchmark(client, cases=(case,), max_steps=4)[0]

    assert result.passed is True
    assert result.reads == 1
    assert result.writes == 1
    assert result.runs == 1
    assert result.invalid_actions == 0
    assert any(item.endswith("READ src/math_utils.py DUPLICATE") for item in result.action_trace)


def test_agentic_project_snapshot_uses_real_line_breaks():
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from kardecagent.llm.benchmark import _project_snapshot

    with TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("x = 1")
        (root / "tests").mkdir()
        (root / "tests" / "test_a.py").write_text("def test_a(): pass")
        snapshot = _project_snapshot(root)
        assert "\\n" not in snapshot
        assert snapshot == "src/a.py\ntests/test_a.py"


def test_agentic_recovery_prompt_mentions_authoritative_failure():
    from kardecagent.llm.benchmark import run_agentic_benchmark, _fixture_cases

    client = FakeAgenticClient([
        """=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    return value
=== END WRITE ===""",
        "=== RUN: python -m pytest -q ===\n=== END RUN ===",
        """=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    if minimum > maximum:
        raise ValueError("invalid range")
    return max(minimum, min(value, maximum))
=== END WRITE ===""",
        "=== RUN: python -m pytest -q ===\n=== END RUN ===",
    ])
    result = run_agentic_benchmark(client, cases=(_fixture_cases()[0],), max_steps=4)[0]
    assert result.passed is True

def test_agentic_benchmark_stops_repeated_no_progress_after_three_turns():
    from kardecagent.llm.benchmark import _fixture_cases

    client = FakeAgenticClient([
        "=== READ: src/cart.py ===\n=== END READ ===",
        "=== READ: src/cart.py ===\n=== END READ ===",
        "=== READ: src/cart.py ===\n=== END READ ===",
    ])
    result = run_agentic_benchmark(client, cases=(_fixture_cases()[1],), max_steps=8)[0]

    assert result.passed is False
    assert result.error == "Agent stalled without making project progress."
    assert len(result.action_trace) == 3
    assert result.invalid_actions == 0


def test_agentic_benchmark_rejects_writes_outside_expected_files():
    from kardecagent.llm.benchmark import _fixture_cases

    client = FakeAgenticClient([
        """=== WRITE: tests/unexpected.py ===
def test_unexpected():
    assert True
=== END WRITE ===
=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    if minimum > maximum:
        raise ValueError("invalid range")
    return max(minimum, min(value, maximum))
=== END WRITE ===
""",
    ])
    result = run_agentic_benchmark(client, cases=(_fixture_cases()[0],), max_steps=1)[0]

    assert result.passed is True
    assert result.invalid_actions == 1
    assert result.writes == 1
    assert result.files_changed == ("src/math_utils.py",)
    assert any("REJECTED_UNEXPECTED_FILE" in item for item in result.action_trace)


def test_agentic_benchmark_captures_recovery_feedback_separately_from_protocol_metrics():
    from kardecagent.llm.benchmark import _fixture_cases

    client = FakeAgenticClient([
        """=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    return value
=== END WRITE ===""",
        "=== RUN: python -m pytest -q ===\n=== END RUN ===",
        """=== WRITE: src/math_utils.py ===
def clamp(value, minimum, maximum):
    if minimum > maximum:
        raise ValueError("invalid range")
    return max(minimum, min(value, maximum))
=== END WRITE ===""",
    ])
    result = run_agentic_benchmark(client, cases=(_fixture_cases()[0],), max_steps=3)[0]

    assert result.passed is True
    assert result.recovery_attempts == 1
    assert result.invalid_actions == 0
    assert len(result.recovery_feedback) == 1
    assert "pytest failure" in result.recovery_feedback[0]
    assert "CURRENT src/math_utils.py" in result.recovery_feedback[0]
