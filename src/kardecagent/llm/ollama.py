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
        model: str = "qwen3-coder:30b",
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
                "options": {"temperature": temperature},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        content = message.get("content", "")
        if not isinstance(content, str) or not content:
            raise RuntimeError("Ollama returned no text content.")
        return OllamaResponse(content=content, raw=data)

    def list_models(self) -> list[dict[str, Any]]:
        response = httpx.get(f"{self.base_url}/api/tags", timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        models = data.get("models")
        if not isinstance(models, list):
            raise RuntimeError("Ollama returned an invalid model list.")
        return models

    def health_check(self) -> OllamaResponse:
        return self.chat(
            [
                {"role": "system", "content": "Reply with exactly: KARDECAGENT_OK"},
                {"role": "user", "content": "Health check."},
            ],
            temperature=0.0,
        )
