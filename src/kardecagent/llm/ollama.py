from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class OllamaResponse:
    content: str
    raw: dict[str, Any]


class OllamaClient:
    """Client for Ollama's native local HTTP API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5-coder:3b",
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
    ) -> OllamaResponse:
        response = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": 2048},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        content = message.get("content", "")
        if not isinstance(content, str):
            raise RuntimeError("Ollama returned non-text content.")
        return OllamaResponse(content=content, raw=data)

    def list_models(self) -> list[dict[str, Any]]:
        response = httpx.get(f"{self.base_url}/api/tags", timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        models = data.get("models")
        if not isinstance(models, list):
            raise RuntimeError("Ollama returned an invalid model list.")
        return models

    def show_model(self, model: str | None = None) -> dict[str, Any]:
        """Return Ollama metadata for a model (family, quantization, context, capabilities)."""
        response = httpx.post(
            f"{self.base_url}/api/show",
            json={"model": model or self.model},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Ollama returned invalid model metadata.")
        return data

    def server_version(self) -> str:
        """Return the Ollama server version without loading a model."""
        response = httpx.get(f"{self.base_url}/api/version", timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        version = data.get("version") if isinstance(data, dict) else None
        if not isinstance(version, str) or not version:
            raise RuntimeError("Ollama returned an invalid server version.")
        return version

    def health_check(self) -> OllamaResponse:
        return self.chat(
            [
                {"role": "system", "content": "Reply with exactly: KARDECAGENT_OK"},
                {"role": "user", "content": "Health check."},
            ],
            temperature=0.0,
        )
