from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProjectMemoryError(ValueError):
    """Raised when project memory is invalid or cannot be persisted."""


class ProjectMemoryStore:
    """Atomic, versioned project memory stored inside the workspace.

    Only approved plans are promoted into durable project decisions. Model
    suggestions and transient execution output must not be written as facts.
    """

    VERSION = 1
    DIRECTORY = ".kardecagent"
    FILENAME = "project-memory.json"

    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()
        self.directory = self.root / self.DIRECTORY
        self.path = self.directory / self.FILENAME

    @staticmethod
    def _canonical(payload: dict[str, Any]) -> bytes:
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @classmethod
    def _checksum(cls, payload: dict[str, Any]) -> str:
        unsigned = dict(payload)
        unsigned.pop("checksum", None)
        return hashlib.sha256(cls._canonical(unsigned)).hexdigest()

    def empty(self) -> dict[str, Any]:
        return {
            "version": self.VERSION,
            "project": {"name": self.root.name, "root": str(self.root)},
            "requirements": [],
            "decisions": [],
            "acceptance_criteria": [],
            "milestones": [],
            "pending_questions": [],
            "updated_at": None,
        }

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.empty()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectMemoryError("project memory cannot be read") from exc
        if not isinstance(payload, dict) or payload.get("version") != self.VERSION:
            raise ProjectMemoryError("unsupported or invalid project memory format")
        supplied = payload.get("checksum")
        if not isinstance(supplied, str) or supplied != self._checksum(payload):
            raise ProjectMemoryError("project memory checksum mismatch")
        if payload.get("project", {}).get("root") != str(self.root):
            raise ProjectMemoryError("project memory belongs to a different workspace")
        for key in (
            "requirements", "decisions", "acceptance_criteria",
            "milestones", "pending_questions",
        ):
            if not isinstance(payload.get(key), list):
                raise ProjectMemoryError(f"invalid project memory field: {key}")
        return payload

    def save(self, payload: dict[str, Any]) -> Path:
        """Validate and atomically replace memory; caller owns approval policy."""
        if not isinstance(payload, dict):
            raise ProjectMemoryError("project memory must be an object")
        candidate = dict(payload)
        candidate["version"] = self.VERSION
        candidate["project"] = {
            "name": self.root.name,
            "root": str(self.root),
        }
        candidate["updated_at"] = datetime.now(timezone.utc).isoformat()
        candidate.pop("checksum", None)
        for key in (
            "requirements", "decisions", "acceptance_criteria",
            "milestones", "pending_questions",
        ):
            if not isinstance(candidate.get(key), list):
                raise ProjectMemoryError(f"invalid project memory field: {key}")
        candidate["checksum"] = self._checksum(candidate)

        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=self.FILENAME + ".", suffix=".tmp", dir=self.directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(candidate, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            self._fsync_directory()
        except OSError as exc:
            raise ProjectMemoryError("failed to persist project memory") from exc
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return self.path

    def record_approved_plan(self, task: str, brief: dict[str, Any], plan: Any) -> Path:
        """Promote approved requirements and acceptance criteria to project memory."""
        memory = self.load()
        requirements = [
            item for item in brief.get("delivery_requirements", [])
            if isinstance(item, str) and item.strip()
        ]
        criteria = list(getattr(plan, "completion_criteria", ()) or ())
        steps = list(getattr(plan, "steps", ()) or ())

        def merge_unique(existing: list, additions: list) -> list:
            seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in existing}
            result = list(existing)
            for item in additions:
                key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    result.append(item)
                    seen.add(key)
            return result

        memory["requirements"] = merge_unique(
            memory["requirements"],
            [{"text": item, "source_task": task, "status": "approved"} for item in requirements],
        )
        memory["acceptance_criteria"] = merge_unique(
            memory["acceptance_criteria"],
            [{"text": str(item), "source_task": task, "status": "pending"} for item in criteria],
        )
        memory["milestones"] = merge_unique(
            memory["milestones"],
            [{"title": str(step), "source_task": task, "status": "pending"} for step in steps],
        )
        return self.save(memory)

    def _fsync_directory(self) -> None:
        if os.name == "nt":
            return
        fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
