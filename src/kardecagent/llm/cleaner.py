from __future__ import annotations

import json
import re
from typing import Any


def strip_thinking(content: str) -> str:
    """Remove reasoning blocks like <think>...</think> from model responses."""
    if not isinstance(content, str):
        return ""
    cleaned = re.sub(r"(?s)<think>.*?</think>", "", content)
    if "<think>" in cleaned:
        cleaned = re.sub(r"(?s)<think>.*$", "", cleaned)
    return cleaned.strip()


def extract_json_payload(content: str) -> Any:
    """Robustly extract and parse JSON from a model response.

    Handles reasoning tags, markdown code blocks, conversational prefixes/suffixes.
    """
    cleaned = strip_thinking(content).strip()
    if not cleaned:
        raise ValueError("Empty LLM response content.")

    # 1. Direct JSON parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 2. Markdown code block extraction (```json ... ``` or ``` ...)
    fence_pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL | re.IGNORECASE)
    for match in fence_pattern.finditer(cleaned):
        block = match.group(1).strip()
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue

    # 3. Find outermost JSON object {...} or array [...]
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = cleaned[first_brace : last_brace + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    first_bracket = cleaned.find("[")
    last_bracket = cleaned.rfind("]")
    if first_bracket != -1 and last_bracket > first_bracket:
        candidate = cleaned[first_bracket : last_bracket + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    return json.loads(cleaned)
