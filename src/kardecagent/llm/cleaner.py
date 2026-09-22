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


def repair_json_string(text: str) -> str:
    """Repair common LLM JSON syntax mistakes: missing commas, trailing commas, etc."""
    # 1. Trailing commas before closing braces/brackets
    repaired = re.sub(r",\s*([\]}])", r"\1", text)
    # 2. Missing commas between array string elements on separate lines: "..." \n "..."
    repaired = re.sub(r'("(?:[^"\\]|\\.)*")\s*\n\s*(")', r'\1,\n\2', repaired)
    # 3. Missing commas after values when followed by another key or value on the next line
    repaired = re.sub(r'("(?:[^"\\]|\\.)*"|\d+|true|false|null|\]|\})\s*\n\s*("|\{|\[)', r'\1,\n\2', repaired)
    # 4. Clean any double commas that might have been created
    repaired = re.sub(r',\s*,', ',', repaired)
    # 5. Clean trailing commas again
    repaired = re.sub(r",\s*([\]}])", r"\1", repaired)
    return repaired


def extract_json_payload(content: str) -> Any:
    """Robustly extract and parse JSON from a model response.

    Handles reasoning tags, markdown code blocks, conversational prefixes/suffixes,
    and automatically repairs common LLM syntax errors (missing commas, trailing commas).
    """
    cleaned = strip_thinking(content).strip()
    if not cleaned:
        raise ValueError("Empty LLM response content.")

    # 1. Direct JSON parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    candidates: list[str] = []

    # 2. Markdown code block extraction (```json ... ``` or ``` ...)
    fence_pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL | re.IGNORECASE)
    for match in fence_pattern.finditer(cleaned):
        block = match.group(1).strip()
        if block:
            candidates.append(block)

    # 3. Find outermost JSON object {...} or array [...]
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.append(cleaned[first_brace : last_brace + 1])

    first_bracket = cleaned.find("[")
    last_bracket = cleaned.rfind("]")
    if first_bracket != -1 and last_bracket > first_bracket:
        candidates.append(cleaned[first_bracket : last_bracket + 1])

    # First attempt: parse extracted candidates directly
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Second attempt: parse candidates with automatic JSON repair
    for candidate in candidates:
        try:
            repaired = repair_json_string(candidate)
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # Third attempt: direct parse on repaired full text
    try:
        return json.loads(repair_json_string(cleaned))
    except json.JSONDecodeError:
        pass

    # If all failed, re-attempt on the most promising candidate to produce a localized error
    if candidates:
        return json.loads(candidates[0])
    return json.loads(cleaned)
