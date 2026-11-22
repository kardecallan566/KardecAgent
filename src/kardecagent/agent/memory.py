from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_MEMORY_ITEMS = 200
MAX_MEMORY_TEXT = 700
MAX_CONVERSATION_TURNS = 40
_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password|senha|secret)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/-]+=*"),
    re.compile(r"(?i)gh[pousr]_[a-z0-9]{20,}"),
)


def _safe_text(value: str) -> str:
    text = value.strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:MAX_MEMORY_TEXT]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """Local, editable project memory and cross-project user preferences."""

    def __init__(self, project_root: Path, *, user_path: Path | None = None) -> None:
        self.project_root = project_root.resolve()
        self.directory = self.project_root / ".kardecagent"
        self.project_path = self.directory / "memory.json"
        self.user_path = user_path or (Path.home() / ".kardecagent" / "memory.json")
        self.conversation_path = self.directory / "conversation.json"

    @staticmethod
    def _read(path: Path) -> list[dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            items = payload.get("items", []) if isinstance(payload, dict) else []
            return [item for item in items if isinstance(item, dict)]
        except (OSError, ValueError, TypeError):
            return []

    @staticmethod
    def _write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _identifier(scope: str, category: str, content: str) -> str:
        raw = f"{scope}\0{category}\0{content.casefold()}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def remember(self, *, scope: str, category: str, content: str) -> dict[str, Any] | None:
        if scope not in {"project", "user"} or category not in {"preference", "decision", "fact", "workflow"}:
            return None
        safe = _safe_text(content)
        if len(safe) < 8 or "[redacted]" in safe:
            return None
        path = self.project_path if scope == "project" else self.user_path
        items = self._read(path)
        identifier = self._identifier(scope, category, safe)
        existing = next((item for item in items if item.get("id") == identifier), None)
        if existing:
            existing["updated_at"] = _now()
            self._write(path, {"version": 1, "items": items[-MAX_MEMORY_ITEMS:]})
            return existing
        item = {
            "id": identifier,
            "scope": scope,
            "category": category,
            "content": safe,
            "created_at": _now(),
            "updated_at": _now(),
        }
        items.append(item)
        self._write(path, {"version": 1, "items": items[-MAX_MEMORY_ITEMS:]})
        return item

    def all_items(self) -> list[dict[str, Any]]:
        items = self._read(self.user_path) + self._read(self.project_path)
        return sorted(items, key=lambda item: str(item.get("updated_at", "")), reverse=True)

    def relevant(self, query: str, *, limit: int = 12) -> list[dict[str, Any]]:
        terms = {term for term in re.findall(r"[a-zA-Z0-9À-ÿ_]{3,}", query.casefold())}
        scored = []
        for item in self.all_items():
            text = str(item.get("content", "")).casefold()
            overlap = sum(1 for term in terms if term in text)
            if overlap:
                scored.append((overlap, str(item.get("updated_at", "")), item))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        if scored:
            return [row[2] for row in scored[:limit]]
        return self.all_items()[:min(4, limit)]

    def forget(self, identifier: str) -> bool:
        removed = False
        for path in (self.user_path, self.project_path):
            items = self._read(path)
            kept = [item for item in items if item.get("id") != identifier]
            if len(kept) != len(items):
                self._write(path, {"version": 1, "items": kept})
                removed = True
        return removed

    def recent_conversation(self, *, limit: int = 12) -> list[dict[str, str]]:
        try:
            payload = json.loads(self.conversation_path.read_text(encoding="utf-8"))
            turns = payload.get("turns", []) if isinstance(payload, dict) else []
            return [
                {"role": turn["role"], "content": turn["content"]}
                for turn in turns[-limit:]
                if isinstance(turn, dict)
                and turn.get("role") in {"user", "assistant"}
                and isinstance(turn.get("content"), str)
            ]
        except (OSError, ValueError, TypeError, KeyError):
            return []

    def append_conversation(self, user_message: str, assistant_message: str) -> None:
        turns = self.recent_conversation(limit=MAX_CONVERSATION_TURNS)
        for role, content in (("user", user_message), ("assistant", assistant_message)):
            safe = _safe_text(content)
            if safe:
                turns.append({"role": role, "content": safe[:4000], "timestamp": _now()})
        self._write(
            self.conversation_path,
            {"version": 1, "turns": turns[-MAX_CONVERSATION_TURNS:]},
        )
