from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .observability import current_request_id, hash_identifier
from .security import write_private_file

_ACTION_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")
_RESULTS = {"success", "denied", "failed"}
_GENESIS_HASH = "0" * 64
_SAFE_METADATA_KEYS = {
    "active",
    "answer_status",
    "error_code",
    "model",
    "previous_provider",
    "provider",
    "role",
    "target_actor_hash",
}


class AuditRepository:
    """追加式审计链；不保存问题、答案、Prompt、令牌、密钥或文件正文。"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()
        self._ensure_store()

    def record(
        self,
        action: str,
        *,
        actor_id: str | None,
        actor_role: str | None,
        resource_type: str,
        resource_id: str | None,
        result: str,
        metadata: dict[str, str | bool | int | float] | None = None,
    ) -> dict[str, Any]:
        if not _ACTION_PATTERN.fullmatch(action):
            raise ValueError("audit action is invalid")
        if result not in _RESULTS:
            raise ValueError("audit result is invalid")
        safe_metadata = {
            key: value
            for key, value in (metadata or {}).items()
            if key in _SAFE_METADATA_KEYS and isinstance(value, (str, bool, int, float))
        }
        with self._lock:
            payload = self._load()
            previous_hash = payload["events"][-1]["event_hash"] if payload["events"] else _GENESIS_HASH
            event = {
                "event_id": f"audit_{uuid4().hex[:16]}",
                "occurred_at": datetime.now(UTC).isoformat(),
                "action": action,
                "actor_hash": hash_identifier(actor_id) if actor_id else None,
                "actor_role": actor_role,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "result": result,
                "request_id": current_request_id(),
                "metadata": safe_metadata,
                "previous_hash": previous_hash,
            }
            event["event_hash"] = _event_hash(event)
            payload["events"].append(event)
            self._save(payload)
            return dict(event)

    def list(
        self,
        *,
        offset: int,
        limit: int,
        action: str | None = None,
        result: str | None = None,
    ) -> list[dict[str, Any]]:
        if action is not None and not _ACTION_PATTERN.fullmatch(action):
            raise ValueError("audit action is invalid")
        if result is not None and result not in _RESULTS:
            raise ValueError("audit result is invalid")
        with self._lock:
            events = list(reversed(self._load()["events"]))
        if action is not None:
            events = [event for event in events if event["action"] == action]
        if result is not None:
            events = [event for event in events if event["result"] == result]
        return [dict(event) for event in events[offset : offset + limit]]

    def verify(self) -> bool:
        with self._lock:
            self._load()
        return True

    def _ensure_store(self) -> None:
        with self._lock:
            if self.path.exists():
                self._load()
                return
            self._save({"version": 1, "events": []})

    def _load(self) -> dict[str, Any]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or not isinstance(payload.get("events"), list):
            raise ValueError("audit store format is invalid")
        previous_hash = _GENESIS_HASH
        for event in payload["events"]:
            if not isinstance(event, dict) or event.get("previous_hash") != previous_hash:
                raise ValueError("audit chain is invalid")
            if event.get("event_hash") != _event_hash(event):
                raise ValueError("audit event hash is invalid")
            previous_hash = str(event["event_hash"])
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        write_private_file(self.path, content)


def _event_hash(event: dict[str, Any]) -> str:
    canonical = {key: value for key, value in event.items() if key != "event_hash"}
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
