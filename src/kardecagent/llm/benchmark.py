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
    return ("qwen2.5-coder:1.5b", "qwen2.5-coder:3b", "qwen2.5-coder:7b", "qwen3.5:4b", "deepseek-coder:1.3b-instruct", "deepseek-coder:6.7b")


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


# --- Agentic benchmark -----------------------------------------------------

from pathlib import Path
import re
import subprocess
import tempfile


@dataclass(frozen=True)
class AgenticCase:
    name: str
    prompt: str
    expected_files: tuple[str, ...]
    test_command: tuple[str, ...]
    verify: Callable[[Path], bool]


@dataclass(frozen=True)
class AgenticResult:
    model: str
    case: str
    passed: bool
    elapsed_seconds: float
    eval_tokens: int
    tokens_per_second: float
    files_changed: tuple[str, ...]
    test_output: str
    error: str = ""
    attempts: int = 0
    tool_calls: int = 0
    recovery_attempts: int = 0
    first_attempt_passed: bool = False
    reads: int = 0
    writes: int = 0
    runs: int = 0
    dones: int = 0
    invalid_actions: int = 0
    action_trace: tuple[str, ...] = ()


_FILE_RE = re.compile(
    r"(?ms)^===\s*FILE:\s*([^\r\n]+)\s*===\s*\n(.*?)^===\s*END FILE\s*===\s*$"
)

_ACTION_RE = re.compile(
    r"(?ms)^===\s*(READ|WRITE|RUN):\s*([^\r\n]+?)\s*===\s*\n(.*?)^===\s*END \1\s*===\s*$"
)


def _parse_files(text: str) -> dict[str, str]:
    files: dict[str, str] = {}
    for match in _FILE_RE.finditer(text):
        path = match.group(1).strip().replace("\\", "/")
        if not path or path.startswith("/") or ":" in path or ".." in Path(path).parts:
            raise ValueError(f"Unsafe benchmark file path: {path!r}")
        files[path] = match.group(2)
    if not files:
        raise ValueError("Model returned no FILE blocks.")
    return files


def _safe_relative_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or ":" in normalized or ".." in Path(normalized).parts:
        raise ValueError(f"Unsafe benchmark path: {path!r}")
    return normalized


def _write_files(root: Path, files: dict[str, str]) -> None:
    root = root.resolve()
    for relative, content in files.items():
        target = (root / _safe_relative_path(relative)).resolve()
        if root not in target.parents:
            raise ValueError(f"Benchmark attempted to escape fixture: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _fixture_cases() -> tuple[AgenticCase, ...]:
    return (
        AgenticCase(
            "implement_existing_function",
            """Inspect the project and implement the task. Use READ/WRITE/RUN actions to modify the project and verify the result.
Format:
=== FILE: path ===
<complete file>
=== END FILE ===

src/math_utils.py:
def clamp(value, minimum, maximum):
    pass

tests/test_math_utils.py:
from math_utils import clamp
def test_clamp():
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(20, 0, 10) == 10

Task: implement clamp and raise ValueError when minimum > maximum.""",
            ("src/math_utils.py",),
            ("python", "-m", "pytest", "-q"),
            lambda root: "raise ValueError" in (root / "src/math_utils.py").read_text(),
        ),
        AgenticCase(
            "debug_existing_code",
            """Fix the bug. Return ONLY complete modified files as FILE blocks.

src/cart.py:
def total(items):
    total = 0
    for item in items:
        total += item["price"] * item.get("quantity", 1)
    return total / len(items)

tests/test_cart.py:
from cart import total
def test_total_empty_cart():
    assert total([]) == 0
def test_total():
    assert total([{"price": 10, "quantity": 2}, {"price": 5}]) == 25

Task: make total([]) return 0 without changing non-empty behavior.""",
            ("src/cart.py",),
            ("python", "-m", "pytest", "-q"),
            lambda root: "if not items" in (root / "src/cart.py").read_text(),
        ),
        AgenticCase(
            "multi_file_feature",
            """Implement the feature and tests. Return ONLY complete modified files as FILE blocks.

src/config.py:
DEFAULTS = {"retries": 3}

src/service.py:
from config import DEFAULTS
def retry_count(config=None):
    config = config or DEFAULTS
    return config["retries"]

tests/test_service.py:
from service import retry_count
def test_default():
    assert retry_count() == 3

Task: accept only integer retries >= 0; invalid values raise ValueError. Preserve the public API and default behavior. Add regression tests.""",
            ("src/service.py", "tests/test_service.py"),
            ("python", "-m", "pytest", "-q"),
            lambda root: "ValueError" in (root / "src/service.py").read_text(),
        ),
        AgenticCase(
            "create_tests",
            """Create useful pytest tests. Use READ/WRITE/RUN actions to create the test file and verify the result.

src/parser.py:
def parse_port(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("invalid port")
    return port

Task: test a valid port, a non-numeric value, zero, and 65536. Do not modify parser.py.""",
            ("tests/test_parser.py",),
            ("python", "-m", "pytest", "-q"),
            lambda root: (root / "tests/test_parser.py").exists()
            and all(x in (root / "tests/test_parser.py").read_text() for x in ("65536", "0", "parse_port")),
        ),
        AgenticCase(
            "refactor_without_regression",
            """Refactor without changing behavior. Return ONLY complete modified files as FILE blocks.

src/names.py:
def normalize_names(names):
    result = []
    for name in names:
        cleaned = name.strip().lower()
        if cleaned:
            result.append(cleaned)
    return result

tests/test_names.py:
from names import normalize_names
def test_names():
    assert normalize_names([" Alice ", "", "BOB"]) == ["alice", "bob"]

Task: improve readability while preserving behavior. Do not add dependencies.""",
            ("src/names.py",),
            ("python", "-m", "pytest", "-q"),
            lambda root: (root / "src/names.py").exists(),
        ),
        AgenticCase(
            "traceback_repair",
            """Repair the failing project. Return ONLY complete modified files as FILE blocks.

src/report.py:
def average(values):
    return sum(values) / len(values)

tests/test_report.py:
from report import average
def test_empty():
    assert average([]) == 0
def test_values():
    assert average([2, 4, 6]) == 4

Task: fix the ZeroDivisionError for an empty list while preserving normal averages.""",
            ("src/report.py",),
            ("python", "-m", "pytest", "-q"),
            lambda root: "if not values" in (root / "src/report.py").read_text(),
        ),
        AgenticCase(
            "feature_with_tests",
            """Implement the feature and tests. Return ONLY complete modified files as FILE blocks.

src/todo.py:
def add_todo(items, title):
    items.append({"title": title, "done": False})
    return items[-1]

tests/test_todo.py:
from todo import add_todo
def test_add():
    items = []
    assert add_todo(items, "Study") == {"title": "Study", "done": False}

Task: add complete_todo(items, index), mark one item done, return it, and raise IndexError for invalid index. Add tests.""",
            ("src/todo.py", "tests/test_todo.py"),
            ("python", "-m", "pytest", "-q"),
            lambda root: "def complete_todo" in (root / "src/todo.py").read_text(),
        ),
        AgenticCase(
            "review_and_fix",
            """Review and fix the implementation. Return ONLY complete modified files as FILE blocks.

src/auth.py:
def is_admin(user):
    return user.get("role") == "admin" or user.get("is_admin") == True

tests/test_auth.py:
from auth import is_admin
def test_role():
    assert is_admin({"role": "admin"}) is True
def test_false_flag():
    assert is_admin({"role": "user", "is_admin": False}) is False
def test_none():
    assert is_admin(None) is False

Task: preserve behavior, use an idiomatic boolean check, and safely handle user=None.""",
            ("src/auth.py",),
            ("python", "-m", "pytest", "-q"),
            lambda root: "user is None" in (root / "src/auth.py").read_text()
            or "not user" in (root / "src/auth.py").read_text(),
        ),
        AgenticCase(
            "api_contract_feature",
            """Implement the feature and regression tests. Return ONLY complete modified files as FILE blocks.

src/users.py:
def get_user(users, user_id):
    for user in users:
        if user["id"] == user_id:
            return user
    return None

tests/test_users.py:
from users import get_user
def test_existing():
    assert get_user([{"id": 1, "name": "Ada"}], 1)["name"] == "Ada"

Task: add find_users(users, query) that performs case-insensitive substring matching against the name field, returns a new list, and does not mutate the input. Add tests for case-insensitivity, multiple matches, no matches, and input immutability.""",
            ("src/users.py", "tests/test_users.py"),
            ("python", "-m", "pytest", "-q"),
            lambda root: "def find_users" in (root / "src/users.py").read_text()
            and "lower()" in (root / "src/users.py").read_text(),
        ),
        AgenticCase(
            "state_machine_bug",
            """Implement the fix and tests. Return ONLY complete modified files as FILE blocks.

src/workflow.py:
STATES = ("pending", "running", "done")

def transition(state, event):
    if event == "start":
        return "running"
    if event == "finish":
        return "done"
    return state

tests/test_workflow.py:
from workflow import transition
def test_start():
    assert transition("pending", "start") == "running"
def test_finish():
    assert transition("running", "finish") == "done"

Task: reject invalid transitions with ValueError: pending may only receive start; running may only receive finish; done cannot transition. Preserve valid behavior and add regression tests.""",
            ("src/workflow.py", "tests/test_workflow.py"),
            ("python", "-m", "pytest", "-q"),
            lambda root: "ValueError" in (root / "src/workflow.py").read_text(),
        ),
        AgenticCase(
            "security_regression",
            """Review the code for the requested security behavior and add tests. Return ONLY complete modified files as FILE blocks.

src/redirect.py:
from urllib.parse import urlparse

def is_safe_redirect(url, allowed_host):
    parsed = urlparse(url)
    return parsed.netloc == allowed_host

tests/test_redirect.py:
from redirect import is_safe_redirect
def test_allowed():
    assert is_safe_redirect("https://example.com/dashboard", "example.com") is True

Task: prevent host confusion attacks. The function must accept only an exact hostname match, reject username/password URLs such as https://example.com@evil.com, reject empty hosts, and reject non-http/https schemes. Add regression tests. Do not add dependencies.""",
            ("src/redirect.py", "tests/test_redirect.py"),
            ("python", "-m", "pytest", "-q"),
            lambda root: "http" in (root / "src/redirect.py").read_text()
            and "username" in (root / "src/redirect.py").read_text(),
        ),
        AgenticCase(
            "cross_module_refactor",
            """Perform the refactor while preserving the public API. Return ONLY complete modified files as FILE blocks.

src/pricing.py:
def subtotal(items):
    return sum(item["price"] * item.get("quantity", 1) for item in items)

def discount(total, percent):
    return total * (1 - percent / 100)

def final_price(items, percent):
    return discount(subtotal(items), percent)

tests/test_pricing.py:
from pricing import subtotal, discount, final_price
def test_pricing():
    items = [{"price": 10, "quantity": 2}, {"price": 5}]
    assert subtotal(items) == 25
    assert discount(25, 20) == 20
    assert final_price(items, 20) == 20

Task: validate that prices and quantities are non-negative and that discount percent is between 0 and 100 inclusive. Raise ValueError for invalid input. Keep the three public functions and add tests covering boundary and invalid cases.""",
            ("src/pricing.py", "tests/test_pricing.py"),
            ("python", "-m", "pytest", "-q"),
            lambda root: "ValueError" in (root / "src/pricing.py").read_text()
            and "def subtotal" in (root / "src/pricing.py").read_text()
            and "def final_price" in (root / "src/pricing.py").read_text(),
        ),
    )


def _prepare_fixture(root: Path, case: AgenticCase) -> None:
    fixtures: dict[str, dict[str, str]] = {
        "implement_existing_function": {
            "src/math_utils.py": "def clamp(value, minimum, maximum):\n    pass\n",
            "tests/test_math_utils.py": "from math_utils import clamp\n\ndef test_clamp():\n    assert clamp(5, 0, 10) == 5\n    assert clamp(-1, 0, 10) == 0\n    assert clamp(20, 0, 10) == 10\n    import pytest\n    with pytest.raises(ValueError):\n        clamp(1, 10, 0)\n",
        },
        "debug_existing_code": {
            "src/cart.py": "def total(items):\n    total = 0\n    for item in items:\n        total += item['price'] * item.get('quantity', 1)\n    return total / len(items)\n",
            "tests/test_cart.py": "from cart import total\n\ndef test_total_empty_cart():\n    assert total([]) == 0\n\ndef test_total():\n    assert total([{'price': 10, 'quantity': 2}, {'price': 5}]) == 25\n",
        },
        "multi_file_feature": {
            "src/config.py": 'DEFAULTS = {"retries": 3}\n',
            "src/service.py": "from config import DEFAULTS\n\ndef retry_count(config=None):\n    config = config or DEFAULTS\n    return config['retries']\n",
            "tests/test_service.py": "from service import retry_count\n\ndef test_default():\n    assert retry_count() == 3\n\ndef test_invalid():\n    import pytest\n    with pytest.raises(ValueError):\n        retry_count({'retries': -1})\n",
        },
        "create_tests": {
            "src/parser.py": "def parse_port(value):\n    port = int(value)\n    if not 1 <= port <= 65535:\n        raise ValueError('invalid port')\n    return port\n",
        },
        "refactor_without_regression": {
            "src/names.py": "def normalize_names(names):\n    result = []\n    for name in names:\n        cleaned = name.strip().lower()\n        if cleaned:\n            result.append(cleaned)\n    return result\n",
            "tests/test_names.py": "from names import normalize_names\n\ndef test_names():\n    assert normalize_names([' Alice ', '', 'BOB']) == ['alice', 'bob']\n",
        },
        "traceback_repair": {
            "src/report.py": "def average(values):\n    return sum(values) / len(values)\n",
            "tests/test_report.py": "from report import average\n\ndef test_empty():\n    assert average([]) == 0\n\ndef test_values():\n    assert average([2, 4, 6]) == 4\n",
        },
        "feature_with_tests": {
            "src/todo.py": "def add_todo(items, title):\n    items.append({'title': title, 'done': False})\n    return items[-1]\n",
            "tests/test_todo.py": "from todo import add_todo\n\ndef test_add():\n    items = []\n    assert add_todo(items, 'Study') == {'title': 'Study', 'done': False}\n\ndef test_complete():\n    from todo import complete_todo\n    items = [{'title': 'Study', 'done': False}]\n    assert complete_todo(items, 0)['done'] is True\n\ndef test_invalid():\n    import pytest\n    with pytest.raises(IndexError):\n        complete_todo([], 0)\n",
        },
        "review_and_fix": {
            "src/auth.py": "def is_admin(user):\n    return user.get('role') == 'admin' or user.get('is_admin') == True\n",
            "tests/test_auth.py": "from auth import is_admin\n\ndef test_role():\n    assert is_admin({'role': 'admin'}) is True\n\ndef test_false_flag():\n    assert is_admin({'role': 'user', 'is_admin': False}) is False\n\ndef test_none():\n    assert is_admin(None) is False\n",
        },        "api_contract_feature": {
            "src/users.py": 'def get_user(users, user_id):\n    for user in users:\n        if user["id"] == user_id:\n            return user\n    return None\n',
            "tests/test_users.py": "from users import get_user\n\ndef test_existing():\n    assert get_user([{'id': 1, 'name': 'Ada'}], 1)['name'] == 'Ada'\n\ndef test_search():\n    from users import find_users\n    users = [{'id': 1, 'name': 'Ada'}, {'id': 2, 'name': 'Grace'}, {'id': 3, 'name': 'ADAM'}]\n    original = [dict(x) for x in users]\n    assert [u['id'] for u in find_users(users, 'ada')] == [1, 3]\n    assert find_users(users, 'xyz') == []\n    assert users == original\n",
        },
        "state_machine_bug": {
            "src/workflow.py": 'STATES = ("pending", "running", "done")\n\ndef transition(state, event):\n    if event == "start":\n        return "running"\n    if event == "finish":\n        return "done"\n    return state\n',
            "tests/test_workflow.py": "from workflow import transition\n\ndef test_start():\n    assert transition('pending', 'start') == 'running'\n\ndef test_finish():\n    assert transition('running', 'finish') == 'done'\n\ndef test_invalid():\n    import pytest\n    with pytest.raises(ValueError):\n        transition('pending', 'finish')\n    with pytest.raises(ValueError):\n        transition('done', 'start')\n",
        },
        "security_regression": {
            "src/redirect.py": 'from urllib.parse import urlparse\n\ndef is_safe_redirect(url, allowed_host):\n    parsed = urlparse(url)\n    return parsed.netloc == allowed_host\n',
            "tests/test_redirect.py": "from redirect import is_safe_redirect\n\ndef test_allowed():\n    assert is_safe_redirect('https://example.com/dashboard', 'example.com') is True\n\ndef test_host_confusion():\n    assert is_safe_redirect('https://example.com@evil.com', 'example.com') is False\n    assert is_safe_redirect('', 'example.com') is False\n    assert is_safe_redirect('javascript:alert(1)', 'example.com') is False\n",
        },
        "cross_module_refactor": {
            "src/pricing.py": 'def subtotal(items):\n    return sum(item["price"] * item.get("quantity", 1) for item in items)\n\ndef discount(total, percent):\n    return total * (1 - percent / 100)\n\ndef final_price(items, percent):\n    return discount(subtotal(items), percent)\n',
            "tests/test_pricing.py": "from pricing import subtotal, discount, final_price\n\ndef test_pricing():\n    items = [{'price': 10, 'quantity': 2}, {'price': 5}]\n    assert subtotal(items) == 25\n    assert discount(25, 20) == 20\n    assert final_price(items, 20) == 20\n\ndef test_boundaries():\n    assert discount(25, 0) == 25\n    assert discount(25, 100) == 0\n\ndef test_invalid():\n    import pytest\n    with pytest.raises(ValueError):\n        subtotal([{'price': -1}])\n    with pytest.raises(ValueError):\n        subtotal([{'price': 1, 'quantity': -1}])\n    with pytest.raises(ValueError):\n        discount(10, 101)\n",
        },

    }
    for relative, content in fixtures[case.name].items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _project_snapshot(root: Path) -> str:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            files.append(path.relative_to(root).as_posix())
    return "\n".join(files)


def _read_project_file(root: Path, relative: str) -> str:
    target = (root / _safe_relative_path(relative)).resolve()
    if root.resolve() not in target.parents:
        raise ValueError(f"Read attempted to escape fixture: {relative}")
    if not target.is_file():
        raise FileNotFoundError(f"Project file not found: {relative}")
    return target.read_text(encoding="utf-8")


def _run_benchmark_test(root: Path, case: AgenticCase) -> tuple[int, str]:
    import os
    env = dict(os.environ)
    src = root / "src"
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        case.test_command,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = (proc.stdout + "\\n" + proc.stderr).strip()[-6000:]
    return proc.returncode, output


def _parse_agent_actions(text: str) -> list[tuple[str, str, str]]:
    actions: list[tuple[int, str, str, str]] = []
    for match in _ACTION_RE.finditer(text):
        actions.append(
            (match.start(), match.group(1).upper(), match.group(2).strip(), match.group(3))
        )
    done_re = re.compile(r"(?m)^===\s*DONE:\s*([^\r\n]*)\s*===\s*$")
    for match in done_re.finditer(text):
        actions.append((match.start(), "DONE", match.group(1).strip(), ""))
    actions.sort(key=lambda item: item[0])
    if not actions:
        raise ValueError(
            "Model returned no tool actions. Expected READ, WRITE, RUN, or DONE blocks."
        )
    return [(kind, target, body) for _, kind, target, body in actions]


def _normalize_agentic_prompt(prompt: str) -> str:
    """Remove legacy FILE-block instructions that conflict with the action protocol."""
    normalized = re.sub(
        r"Return ONLY complete modified files as FILE blocks\.?",
        "Use READ/WRITE/RUN actions and finish with DONE only after tests pass.",
        prompt,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"Format:\s*=== FILE: path ===.*?=== END FILE ===",
        "Use the READ/WRITE/RUN action protocol shown by the system message.",
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return normalized.strip()


def run_agentic_benchmark(
    client: OllamaClient,
    *,
    cases: tuple[AgenticCase, ...] | None = None,
    max_steps: int = 8,
) -> list[AgenticResult]:
    """Run a real tool-feedback coding loop in an isolated fixture.

    The model must explicitly inspect files, write changes, run the fixture tests,
    and recover from failures using the returned test output.
    """
    results: list[AgenticResult] = []
    for case in cases or _fixture_cases():
        started = perf_counter()
        with tempfile.TemporaryDirectory(prefix="kardecagent-bench-") as temp:
            root = Path(temp)
            _prepare_fixture(root, case)
            total_tokens = 0
            weighted_tps = 0.0
            tps_samples = 0
            tool_calls = 0
            attempts = 0
            recovery_attempts = 0
            first_attempt_passed = False
            reads = 0
            writes = 0
            runs = 0
            dones = 0
            invalid_actions = 0
            action_trace: list[str] = []
            changed: set[str] = set()
            read_history: set[str] = set()
            needs_write_after_failure = False
            last_written_contents: dict[str, str] = {}
            output = ""
            error = ""
            passed = False
            messages: list[dict[str, str]] = [
                {
                    "role": "system",
                    "content": (
                        "You are an autonomous coding benchmark agent. You have a small "
                        "tool protocol and MUST use it. Never output markdown or explanations. "
                        "Inspect before editing. Available actions:\n"
                        "=== READ: relative/path ===\n=== END READ ===\n"
                        "=== WRITE: relative/path ===\n<complete file contents>\n=== END WRITE ===\n"
                        "=== RUN: python -m pytest -q ===\n=== END RUN ===\n"
                        "=== DONE: success ===\n"
                        "Only use the exact test command shown. Do not access files outside the project. "
                        "READ each relevant file at most once. Once the relevant files are read, move to WRITE. "
                        "After a failed test, the next productive action MUST be WRITE; do not run the same failing "
                        "test again until you have changed a file. Use the pytest output as debugging feedback. "
                        "Do not repeat the same WRITE unless you are changing the implementation. "
                        "Exactly ONE action block is allowed in each response. If you need another action, wait for the next turn. "
                        "After a successful test run, you may finish; DONE is accepted only after tests pass."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Task: {_normalize_agentic_prompt(case.prompt)}\n\n"
                        "Project files:\n"
                        f"{_project_snapshot(root)}\n\n"
                        "Start by reading the files relevant to the task."
                    ),
                },
            ]
            try:
                for step in range(max_steps):
                    response = client.chat(messages, temperature=0.0)
                    raw = response.raw
                    count = int(raw.get("eval_count") or 0)
                    duration_ns = int(raw.get("eval_duration") or 0)
                    total_tokens += count
                    if count and duration_ns:
                        sample_tps = count / (duration_ns / 1_000_000_000)
                        weighted_tps += sample_tps
                        tps_samples += 1

                    try:
                        actions = _parse_agent_actions(response.content)
                    except ValueError as exc:
                        invalid_actions += 1
                        action_trace.append(f"step={step + 1} INVALID {exc}")
                        messages.append({"role": "assistant", "content": response.content})
                        messages.append({"role": "user", "content": "Your response contained no recognized tool action. Use exactly READ, WRITE, RUN, or DONE blocks from the protocol. Do not use markdown fences or explanations."})
                        continue
                    messages.append({"role": "assistant", "content": response.content})

                    step_had_test = False
                    step_test_passed = False
                    feedback: list[str] = []

                    # Execute exactly one protocol action per model turn.
                    # If the model emits several actions, execute only the first
                    # and report the rest as invalid so the next turn can continue.
                    if len(actions) > 1:
                        invalid_actions += len(actions) - 1
                        action_trace.append(
                            f"step={step + 1} EXTRA_ACTIONS_IGNORED={len(actions) - 1}"
                        )

                    kind, target, body = actions[0]
                    tool_calls += 1
                    if len(actions) > 1:
                        feedback.append(
                            f"You emitted {len(actions)} actions, but only the first was executed. "
                            "The remaining actions were discarded. Continue with exactly ONE action in the next turn."
                        )
                    action_trace.append(f"step={step + 1} {kind} {target}".rstrip())

                    if kind == "READ":
                        relative = _safe_relative_path(target)
                        if relative in read_history:
                            invalid_actions += 1
                            action_trace[-1] += " DUPLICATE"
                            feedback.append(
                                f"READ {relative}: already read. Do not read it again; "
                                "move to WRITE or RUN."
                            )
                        else:
                            reads += 1
                            read_history.add(relative)
                            content = _read_project_file(root, relative)
                            feedback.append(f"READ {relative}:\n{content}")
                    elif kind == "WRITE":
                        writes += 1
                        relative = _safe_relative_path(target)
                        previous = last_written_contents.get(relative)
                        if previous is not None and previous == body:
                            invalid_actions += 1
                            action_trace[-1] += " NOOP"
                            feedback.append(
                                f"WRITE {relative}: no change from the previous WRITE. "
                                "Change the implementation based on the test failure before running again."
                            )
                        else:
                            _write_files(root, {relative: body})
                            changed.add(relative)
                            last_written_contents[relative] = body
                            needs_write_after_failure = False
                            feedback.append(f"WRITE {relative}: OK")
                            feedback.append(
                                "WRITE accepted. This turn is complete. Choose exactly ONE next action. "
                                "If the required implementation is complete, the next action should be RUN."
                            )
                    elif kind == "RUN":
                        if needs_write_after_failure:
                            invalid_actions += 1
                            action_trace[-1] += " BLOCKED_AFTER_FAIL"
                            feedback.append(
                                "RUN blocked: the previous pytest run failed and no new code has been "
                                "written since that failure. Use WRITE to change the implementation first."
                            )
                        else:
                            runs += 1
                            if target.strip() != "python -m pytest -q":
                                raise ValueError(f"Unsupported benchmark command: {target}")
                            attempts += 1
                            step_had_test = True
                            code, output = _run_benchmark_test(root, case)
                            step_test_passed = code == 0 and case.verify(root)
                            action_trace[-1] += f" {'PASS' if step_test_passed else 'FAIL'}"
                            feedback.append(
                                f"RUN {target}: {'PASS' if step_test_passed else 'FAIL'}\n{output}"
                            )
                            if step_test_passed:
                                if attempts == 1:
                                    first_attempt_passed = True
                                passed = True
                            else:
                                recovery_attempts += 1
                                needs_write_after_failure = True
                                current_files = []
                                for relative in sorted(changed):
                                    try:
                                        current_files.append(
                                            f"CURRENT {relative}:\n{_read_project_file(root, relative)}"
                                        )
                                    except (FileNotFoundError, ValueError):
                                        pass
                                feedback.extend(current_files)
                    elif kind == "DONE":
                        dones += 1
                        if not passed:
                            invalid_actions += 1
                            action_trace[-1] += " INVALID_BEFORE_PASS"
                            feedback.append(
                                "DONE rejected: tests have not passed. Continue working; "
                                "use WRITE and then RUN pytest."
                            )
                        else:
                            passed = True
                    else:
                        raise ValueError(f"Unsupported action: {kind}")

                    if passed:
                        break

                    if not step_had_test:
                        if needs_write_after_failure:
                            feedback.append(
                                "A previous test failed and no corrective WRITE was accepted. "
                                "Do not READ or RUN again. Change the implementation with WRITE first."
                            )
                        feedback.append(
                            "No test was run. If you have already READ the relevant files, "
                            "do not reread them. Make the required change with WRITE, then "
                            "run the exact pytest command."
                        )
                    elif not step_test_passed:
                        feedback.append(
                            "Tests failed. Treat the output above as the debugging feedback, "
                            "inspect the relevant files, apply a fix, and run pytest again."
                        )
                    messages.append({"role": "user", "content": "\n\n".join(feedback)})
                    # Keep the task/system context plus only the recent tool exchange.
                    # Replaying many full-file WRITE responses quickly overwhelms small models.
                    if len(messages) > 8:
                        messages = [messages[0], messages[1], *messages[-6:]]

                if not passed:
                    error = f"Agent did not reach a passing test state within {max_steps} steps."
            except Exception as exc:
                error = str(exc)

            results.append(
                AgenticResult(
                    model=client.model,
                    case=case.name,
                    passed=passed,
                    elapsed_seconds=perf_counter() - started,
                    eval_tokens=total_tokens,
                    tokens_per_second=(weighted_tps / tps_samples if tps_samples else 0.0),
                    files_changed=tuple(sorted(changed)),
                    test_output=output,
                    error=error,
                    attempts=attempts,
                    tool_calls=tool_calls,
                    recovery_attempts=recovery_attempts,
                    first_attempt_passed=first_attempt_passed,
                    reads=reads,
                    writes=writes,
                    runs=runs,
                    dones=dones,
                    invalid_actions=invalid_actions,
                    action_trace=tuple(action_trace),
                )
            )
    return results

