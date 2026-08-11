from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .knowledge_bases import validate_knowledge_base_id

_CONVERSATION_ID_PATTERN = re.compile(r"^conv_[a-f0-9]{16}$")
_ANSWER_RECORD_ID_PATTERN = re.compile(r"^answer_[a-f0-9]{16}$")


class ConversationRepository:
    """追加保存会话和回答记录；仅存来源快照，不依赖当前 Chroma 内容。"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()
        self._ensure_store()

    def _ensure_store(self) -> None:
        with self._lock:
            if self.path.exists():
                self._load()
                return
            self._save({"version": 1, "conversations": [], "answers": []})

    def resolve_conversation(
        self,
        knowledge_base_id: str,
        question: str,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        validate_knowledge_base_id(knowledge_base_id)
        with self._lock:
            payload = self._load()
            if conversation_id is not None:
                self._validate_conversation_id(conversation_id)
                conversation = next(
                    (
                        item
                        for item in payload["conversations"]
                        if item["conversation_id"] == conversation_id
                    ),
                    None,
                )
                if conversation is None:
                    raise LookupError("conversation not found")
                if conversation["knowledge_base_id"] != knowledge_base_id:
                    raise PermissionError("conversation belongs to another knowledge base")
                return dict(conversation)

            now = datetime.now(UTC).isoformat()
            conversation = {
                "conversation_id": f"conv_{uuid4().hex[:16]}",
                "knowledge_base_id": knowledge_base_id,
                "title": question[:80],
                "created_at": now,
                "updated_at": now,
            }
            payload["conversations"].append(conversation)
            self._save(payload)
            return dict(conversation)

    def record(
        self,
        *,
        conversation_id: str,
        knowledge_base_id: str,
        question: str,
        status: str,
        answer: str | None,
        sources: list[dict[str, Any]],
        latency_ms: dict[str, float],
        models: dict[str, str],
        model_metadata: dict[str, str | int | float | bool],
        prompt_version: str | None,
        prompt_hash: str | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"success", "failed"}:
            raise ValueError("answer status is invalid")
        validate_knowledge_base_id(knowledge_base_id)
        self._validate_conversation_id(conversation_id)
        with self._lock:
            payload = self._load()
            conversation = next(
                (
                    item
                    for item in payload["conversations"]
                    if item["conversation_id"] == conversation_id
                ),
                None,
            )
            if conversation is None or conversation["knowledge_base_id"] != knowledge_base_id:
                raise LookupError("conversation not found in knowledge base")
            now = datetime.now(UTC).isoformat()
            record = {
                "record_id": f"answer_{uuid4().hex[:16]}",
                "conversation_id": conversation_id,
                "knowledge_base_id": knowledge_base_id,
                "question": question,
                "status": status,
                "answer": answer,
                "sources": sources,
                "latency_ms": latency_ms,
                "models": models,
                "model_metadata": model_metadata,
                "prompt_version": prompt_version,
                "prompt_hash": prompt_hash,
                "error_code": error_code,
                "error_message": error_message,
                "created_at": now,
            }
            payload["answers"].append(record)
            conversation["updated_at"] = now
            self._save(payload)
            return dict(record)

    def list_conversations(self, knowledge_base_id: str) -> list[dict[str, Any]]:
        validate_knowledge_base_id(knowledge_base_id)
        with self._lock:
            payload = self._load()
        summaries = []
        for conversation in payload["conversations"]:
            if conversation["knowledge_base_id"] != knowledge_base_id:
                continue
            records = [
                item
                for item in payload["answers"]
                if item["conversation_id"] == conversation["conversation_id"]
            ]
            summaries.append(
                {
                    **conversation,
                    "turn_count": len(records),
                    "last_status": records[-1]["status"] if records else None,
                }
            )
        return sorted(summaries, key=lambda item: item["updated_at"], reverse=True)

    def get_conversation(
        self,
        knowledge_base_id: str,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        validate_knowledge_base_id(knowledge_base_id)
        self._validate_conversation_id(conversation_id)
        with self._lock:
            payload = self._load()
        conversation = next(
            (
                item
                for item in payload["conversations"]
                if item["conversation_id"] == conversation_id
                and item["knowledge_base_id"] == knowledge_base_id
            ),
            None,
        )
        if conversation is None:
            return None
        records = [
            item
            for item in payload["answers"]
            if item["conversation_id"] == conversation_id
            and item["knowledge_base_id"] == knowledge_base_id
        ]
        return {**conversation, "records": records}

    def get_answer(self, knowledge_base_id: str, record_id: str) -> dict[str, Any] | None:
        validate_knowledge_base_id(knowledge_base_id)
        if not _ANSWER_RECORD_ID_PATTERN.fullmatch(record_id):
            raise ValueError("answer record id is invalid")
        with self._lock:
            payload = self._load()
        record = next(
            (
                item
                for item in payload["answers"]
                if item["record_id"] == record_id
                and item["knowledge_base_id"] == knowledge_base_id
            ),
            None,
        )
        return dict(record) if record is not None else None

    def delete_conversation(self, knowledge_base_id: str, conversation_id: str) -> bool:
        validate_knowledge_base_id(knowledge_base_id)
        self._validate_conversation_id(conversation_id)
        with self._lock:
            payload = self._load()
            remaining_conversations = [
                item
                for item in payload["conversations"]
                if not (
                    item["conversation_id"] == conversation_id
                    and item["knowledge_base_id"] == knowledge_base_id
                )
            ]
            if len(remaining_conversations) == len(payload["conversations"]):
                return False
            payload["conversations"] = remaining_conversations
            payload["answers"] = [
                item
                for item in payload["answers"]
                if not (
                    item["conversation_id"] == conversation_id
                    and item["knowledge_base_id"] == knowledge_base_id
                )
            ]
            self._save(payload)
            return True

    def _load(self) -> dict[str, Any]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            payload.get("version") != 1
            or not isinstance(payload.get("conversations"), list)
            or not isinstance(payload.get("answers"), list)
        ):
            raise ValueError("conversation store format is invalid")
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self.path)

    @staticmethod
    def _validate_conversation_id(conversation_id: str) -> None:
        if not _CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
            raise ValueError("conversation id is invalid")
