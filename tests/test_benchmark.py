from kardecagent.llm.benchmark import CASES, default_models, run_benchmark


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
