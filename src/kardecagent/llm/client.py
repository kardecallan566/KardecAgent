from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import httpx

@dataclass
class LLMResponse:
    content: str
    raw: dict[str, Any]

class LocalLLMClient:
    """Minimal OpenAI-compatible client for a local inference server."""
    def __init__(self, base_url: str, model: str, api_key: str = "local", timeout: float = 120.0) -> None:
        self.base_url, self.model, self.api_key, self.timeout = base_url.rstrip("/"), model, api_key, timeout

    def chat(self, messages: list[dict[str, Any]], *, temperature: float = 0.2) -> LLMResponse:
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "messages": messages, "temperature": temperature},
            headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if not choices: raise RuntimeError("Local LLM returned no choices.")
        content = (choices[0].get("message") or {}).get("content", "")
        if not isinstance(content, str): raise RuntimeError("Local LLM returned non-text content.")
        return LLMResponse(content=content, raw=data)
