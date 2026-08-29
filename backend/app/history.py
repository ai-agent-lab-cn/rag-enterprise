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


def _owned_by(conversation: dict[str, Any], owner_id: str) -> bool:
    """判断会话是否属于该用户。

    V5 之前保存的会话没有 ``owner_id``，一律视为无主：既然无法确定它属于谁，
    就不对任何人展示，而不是退回到"同知识库可见"那种越权行为。
    """

    return conversation.get("owner_id") == owner_id


class ConversationRepository:
    """追加保存会话和回答记录；仅存来源快照，不依赖当前索引内容。

    会话按发起人隔离：知识库授权决定能否提问，不代表能读别人的问答正文。
    """

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
        owner_id: str,
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
                if not _owned_by(conversation, owner_id):
                    raise PermissionError("conversation belongs to another user")
                return dict(conversation)

            now = datetime.now(UTC).isoformat()
            conversation = {
                "conversation_id": f"conv_{uuid4().hex[:16]}",
                "knowledge_base_id": knowledge_base_id,
                "owner_id": owner_id,
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
        answer_status: str | None = None,
        generation_governance: dict[str, Any] | None = None,
        query_metadata: dict[str, Any] | None = None,
        bad_case_category: str | None = None,
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
                "answer_status": answer_status,
                "generation_governance": generation_governance,
                "query_metadata": query_metadata,
                "bad_case_category": bad_case_category,
                "error_code": error_code,
                "error_message": error_message,
                "created_at": now,
            }
            payload["answers"].append(record)
            conversation["updated_at"] = now
            self._save(payload)
            return dict(record)

    def list_bad_cases(
        self,
        knowledge_base_id: str,
        owner_id: str,
        category: str | None = None,
        error_code: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """只返回当前用户自己的失败记录，不因知识库授权扩大到他人会话。"""

        validate_knowledge_base_id(knowledge_base_id)
        with self._lock:
            payload = self._load()
        owned_conversations = {
            item["conversation_id"]
            for item in payload["conversations"]
            if item["knowledge_base_id"] == knowledge_base_id and _owned_by(item, owner_id)
        }
        results: list[dict[str, Any]] = []
        for record in payload["answers"]:
            if record["conversation_id"] not in owned_conversations or record["status"] != "failed":
                continue
            record_category = record.get("bad_case_category") or "unclassified"
            if category and record_category != category:
                continue
            if error_code and record.get("error_code") != error_code:
                continue
            created_at = datetime.fromisoformat(str(record["created_at"]).replace("Z", "+00:00"))
            if created_from and created_at < created_from:
                continue
            if created_to and created_at > created_to:
                continue
            results.append({**record, "bad_case_category": record_category})
        return sorted(results, key=lambda item: item["created_at"], reverse=True)

    def list_conversations(self, knowledge_base_id: str, owner_id: str) -> list[dict[str, Any]]:
        validate_knowledge_base_id(knowledge_base_id)
        with self._lock:
            payload = self._load()
        summaries = []
        for conversation in payload["conversations"]:
            if conversation["knowledge_base_id"] != knowledge_base_id:
                continue
            if not _owned_by(conversation, owner_id):
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

    def count_conversations(self, knowledge_base_id: str) -> int:
        """统计知识库下所有人的会话数，不做归属过滤。

        仅用于健康探测和"知识库是否为空"的判定：删除知识库必须考虑他人的会话，
        否则会留下无法访问的孤儿记录。不得用于面向用户的读取路径。
        """

        validate_knowledge_base_id(knowledge_base_id)
        with self._lock:
            payload = self._load()
        return sum(
            1
            for item in payload["conversations"]
            if item["knowledge_base_id"] == knowledge_base_id
        )

    def get_conversation(
        self,
        knowledge_base_id: str,
        conversation_id: str,
        owner_id: str,
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
        if conversation is None or not _owned_by(conversation, owner_id):
            return None
        records = [
            item
            for item in payload["answers"]
            if item["conversation_id"] == conversation_id
            and item["knowledge_base_id"] == knowledge_base_id
        ]
        return {**conversation, "records": records}

    def get_answer(
        self,
        knowledge_base_id: str,
        record_id: str,
        owner_id: str,
    ) -> dict[str, Any] | None:
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
        if record is None:
            return None
        # 回答记录本身不带归属，必须回到所属会话判断，否则可凭 record_id 绕过隔离。
        conversation = next(
            (
                item
                for item in payload["conversations"]
                if item["conversation_id"] == record["conversation_id"]
            ),
            None,
        )
        if conversation is None or not _owned_by(conversation, owner_id):
            return None
        return dict(record)

    def delete_conversation(
        self,
        knowledge_base_id: str,
        conversation_id: str,
        owner_id: str,
    ) -> bool:
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
                    and _owned_by(item, owner_id)
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
