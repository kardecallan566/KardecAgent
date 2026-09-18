from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _csv_env(name: str) -> tuple[str, ...]:
    return tuple(x.strip().lower() for x in os.getenv(name, "").split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_model: str = "qwen3.8-27b"
    llm_api_key: str = "local"
    llm_timeout_seconds: float = 120.0
    max_iterations: int = 20
    command_timeout_seconds: float = 120.0
    max_command_output_chars: int = 20_000
    web_allow_domains: tuple[str, ...] = ()
    web_deny_domains: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            llm_base_url=os.getenv("KARDEC_LLM_BASE_URL", cls.llm_base_url).rstrip("/"),
            llm_model=os.getenv("KARDEC_LLM_MODEL", cls.llm_model),
            llm_api_key=os.getenv("KARDEC_LLM_API_KEY", cls.llm_api_key),
            llm_timeout_seconds=float(os.getenv("KARDEC_LLM_TIMEOUT", cls.llm_timeout_seconds)),
            max_iterations=int(os.getenv("KARDEC_MAX_ITERATIONS", cls.max_iterations)),
            command_timeout_seconds=float(os.getenv("KARDEC_COMMAND_TIMEOUT", cls.command_timeout_seconds)),
            max_command_output_chars=int(os.getenv("KARDEC_MAX_COMMAND_OUTPUT", cls.max_command_output_chars)),
            web_allow_domains=_csv_env("KARDEC_WEB_ALLOW_DOMAINS"),
            web_deny_domains=_csv_env("KARDEC_WEB_DENY_DOMAINS"),
        )


def resolve_project_root(path: str | Path) -> Path:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"Project path does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Project path is not a directory: {root}")
    return root
