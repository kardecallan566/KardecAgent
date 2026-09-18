from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _csv_env(name: str) -> tuple[str, ...]:
    return tuple(x.strip().lower() for x in os.getenv(name, "").split(",") if x.strip())


def _env(primary: str, legacy: str, default: str) -> str:
    return os.getenv(primary, os.getenv(legacy, default))


@dataclass(frozen=True)
class Settings:
    # Ollama is the default local brain. The OpenAI-compatible endpoint remains
    # available through the existing LocalLLMClient for future providers.
    llm_provider: str = "ollama"
    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_model: str = "qwen2.5-coder:3b"
    llm_api_key: str = "local"
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_timeout_seconds: float = 120.0
    max_iterations: int = 20
    command_timeout_seconds: float = 120.0
    max_command_output_chars: int = 20_000
    web_allow_domains: tuple[str, ...] = ()
    web_deny_domains: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            llm_provider=_env("KARDECAGENT_LLM_PROVIDER", "KARDEC_LLM_PROVIDER", cls.llm_provider).lower(),
            llm_base_url=_env("KARDEC_LLM_BASE_URL", "KARDEC_LLM_BASE_URL", cls.llm_base_url).rstrip("/"),
            llm_model=_env("KARDECAGENT_LLM_MODEL", "KARDEC_LLM_MODEL", cls.llm_model),
            llm_api_key=_env("KARDECAGENT_LLM_API_KEY", "KARDEC_LLM_API_KEY", cls.llm_api_key),
            ollama_base_url=_env("KARDECAGENT_OLLAMA_BASE_URL", "KARDEC_OLLAMA_BASE_URL", cls.ollama_base_url).rstrip("/"),
            llm_timeout_seconds=float(_env("KARDECAGENT_LLM_TIMEOUT", "KARDEC_LLM_TIMEOUT", str(cls.llm_timeout_seconds))),
            max_iterations=int(_env("KARDECAGENT_MAX_ITERATIONS", "KARDEC_MAX_ITERATIONS", str(cls.max_iterations))),
            command_timeout_seconds=float(_env("KARDECAGENT_COMMAND_TIMEOUT", "KARDEC_COMMAND_TIMEOUT", str(cls.command_timeout_seconds))),
            max_command_output_chars=int(_env("KARDECAGENT_MAX_COMMAND_OUTPUT", "KARDEC_MAX_COMMAND_OUTPUT", str(cls.max_command_output_chars))),
            web_allow_domains=_csv_env("KARDECAGENT_WEB_ALLOW_DOMAINS") or _csv_env("KARDEC_WEB_ALLOW_DOMAINS"),
            web_deny_domains=_csv_env("KARDECAGENT_WEB_DENY_DOMAINS") or _csv_env("KARDEC_WEB_DENY_DOMAINS"),
        )


def resolve_project_root(path: str | Path) -> Path:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"Project path does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Project path is not a directory: {root}")
    return root
