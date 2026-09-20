from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .knowledge_bases import validate_knowledge_base_id
from .modular_rag import DEFAULT_PROFILE_VERSIONS, RAGPolicy

_CONVERSATION_ID_PATTERN = re.compile(r"^conv_[a-f0-9]{16}$")
_ANSWER_RECORD_ID_PATTERN = re.compile(r"^answer_[a-f0-9]{16}$")


@dataclass(frozen=True)
class NormalizedLegacyPayload:
    conversations: list[dict[str, Any]]
    answers: list[dict[str, Any]]
    sha256: str


def normalize_legacy_payload(payload: dict[str, Any]) -> NormalizedLegacyPayload:
    if (
        payload.get("version") != 1
        or not isinstance(payload.get("conversations"), list)
        or not isinstance(payload.get("answers"), list)
    ):
        raise ValueError("conversation store format is invalid")
    conversations = [dict(item) for item in payload["conversations"]]
    answers = [dict(item) for item in payload["answers"]]
    conversation_ids: set[str] = set()
    for item in conversations:
        conversation_id = str(item.get("conversation_id") or "")
        if not _CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
            raise ValueError(f"invalid conversation id: {conversation_id}")
        if conversation_id in conversation_ids:
            raise ValueError(f"duplicate conversation id: {conversation_id}")
        if not str(item.get("owner_id") or "").strip():
            raise ValueError(
                f"conversation has no owner and cannot be migrated safely: {conversation_id}"
            )
        conversation_ids.add(conversation_id)
    record_ids: set[str] = set()
    for item in answers:
        record_id = str(item.get("record_id") or "")
        if not _ANSWER_RECORD_ID_PATTERN.fullmatch(record_id):
            raise ValueError(f"invalid answer record id: {record_id}")
        if record_id in record_ids:
            raise ValueError(f"duplicate answer record id: {record_id}")
        if item.get("conversation_id") not in conversation_ids:
            raise ValueError(f"orphan answer record: {record_id}")
        record_ids.add(record_id)
    canonical = json.dumps(
        {"version": 1, "conversations": conversations, "answers": answers},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return NormalizedLegacyPayload(
        conversations,
        answers,
        hashlib.sha256(canonical.encode()).hexdigest(),
    )


class PostgresConversationRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def ensure_cutover_ready(self, legacy_path: Path) -> None:
        """旧文件存在时，必须先完成显式导入和哈希核对，禁止静默切到空表。"""

        if not legacy_path.exists():
            return
        payload = normalize_legacy_payload(json.loads(legacy_path.read_text(encoding="utf-8")))
        with psycopg.connect(self.database_url) as connection:
            migrated = connection.execute(
                "SELECT 1 FROM conversation_migration_runs WHERE source_sha256=%s",
                (payload.sha256,),
            ).fetchone()
        if migrated is None:
            raise RuntimeError(
                "检测到尚未迁移的 data/conversations/records.json；请先执行 "
                "uv run python -m scripts.migrate_conversations_to_postgres --apply"
            )

    def resolve_conversation(
        self,
        knowledge_base_id: str,
        question: str,
        conversation_id: str | None,
        owner_id: str,
    ) -> dict[str, Any]:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            if conversation_id is not None:
                self._validate_conversation_id(conversation_id)
                row = connection.execute(
                    "SELECT * FROM conversations WHERE conversation_id=%s",
                    (conversation_id,),
                ).fetchone()
                if row is None:
                    raise LookupError("conversation not found")
                if row["knowledge_base_id"] != knowledge_base_id:
                    raise PermissionError("conversation belongs to another knowledge base")
                if row["owner_id"] != owner_id:
                    raise PermissionError("conversation belongs to another user")
                return dict(row)
            conversation_id = f"conv_{uuid4().hex[:16]}"
            row = connection.execute(
                """INSERT INTO conversations
                   (conversation_id, knowledge_base_id, owner_id, title)
                   VALUES (%s,%s,%s,%s) RETURNING *""",
                (conversation_id, knowledge_base_id, owner_id, question[:80]),
            ).fetchone()
            return dict(row)

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
        execution_id: str | None = None,
        routing: dict[str, Any] | None = None,
        pipeline_profile: str | None = None,
        profile_version: str | None = None,
        policy_snapshot: dict[str, Any] | None = None,
        active_index_version_id: str | None = None,
        module_executions: list[dict[str, Any]] | None = None,
        execution_started_at: datetime | None = None,
    ) -> dict[str, Any]:
        if status not in {"success", "failed"}:
            raise ValueError("answer status is invalid")
        record_id = f"answer_{uuid4().hex[:16]}"
        now = datetime.now(UTC)
        modules = module_executions or []
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                owner = connection.execute(
                    "SELECT owner_id FROM conversations WHERE conversation_id=%s AND knowledge_base_id=%s",
                    (conversation_id, knowledge_base_id),
                ).fetchone()
                if owner is None:
                    raise LookupError("conversation not found in knowledge base")
                if execution_id:
                    route = routing or {}
                    connection.execute(
                        """INSERT INTO query_executions
                           (execution_id, conversation_id, knowledge_base_id, owner_id, intent,
                            intent_confidence, routing_reason, original_question, effective_question,
                            follow_up_rewritten, requires_web, classifier_model,
                            control_outcome, pipeline_profile,
                            profile_version, policy_snapshot, active_index_version_id, status,
                            fallback_used, total_latency_ms, error_code, error_message,
                            started_at, finished_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                   %s,%s,%s,%s,%s)""",
                        (
                            execution_id,
                            conversation_id,
                            knowledge_base_id,
                            owner["owner_id"],
                            route.get("intent"),
                            float(route.get("confidence") or 0),
                            route.get("reason"),
                            route.get("original_question") or question,
                            route.get("effective_question") or question,
                            bool(route.get("follow_up_rewritten")),
                            bool(route.get("requires_web")),
                            route.get("classifier_model"),
                            route.get("control_outcome") or "route",
                            pipeline_profile,
                            profile_version,
                            Jsonb(policy_snapshot or {}),
                            active_index_version_id,
                            "succeeded" if status == "success" else "failed",
                            bool(route.get("fallback_used")),
                            float(latency_ms.get("total", 0)),
                            error_code,
                            error_message,
                            execution_started_at or now,
                            now,
                        ),
                    )
                    for module in modules:
                        connection.execute(
                            """INSERT INTO module_executions
                               (module_execution_id, execution_id, sequence, module_key,
                                module_version, status, attempt, input_hash, output_hash,
                                metrics, error_code, error_message, fallback_reason,
                                duration_ms, started_at, finished_at)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                       to_timestamp(%s),to_timestamp(%s))""",
                            (
                                module["module_execution_id"], execution_id, module["sequence"],
                                module["module_key"], module["module_version"], module["status"],
                                module.get("attempt", 1), module["input_hash"], module.get("output_hash"),
                                Jsonb(module.get("metrics") or {}), module.get("error_code"),
                                module.get("error_message"), module.get("fallback_reason"),
                                float(module.get("duration_ms") or 0), module["started_at"],
                                module["finished_at"],
                            ),
                        )
                    self._insert_evidence(connection, execution_id, sources)
                row = connection.execute(
                    """INSERT INTO answer_records
                       (record_id, conversation_id, knowledge_base_id, execution_id, question,
                        status, answer, sources, latency_ms, models, model_metadata,
                        prompt_version, prompt_hash, answer_status, generation_governance,
                        query_metadata, routing, pipeline_profile, profile_version, module_summary,
                        bad_case_category, error_code, error_message, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               %s,%s,%s,%s,%s) RETURNING *""",
                    (
                        record_id, conversation_id, knowledge_base_id, execution_id, question,
                        status, answer, Jsonb(sources), Jsonb(latency_ms), Jsonb(models),
                        Jsonb(model_metadata), prompt_version, prompt_hash, answer_status,
                        Jsonb(generation_governance) if generation_governance is not None else None,
                        Jsonb(query_metadata) if query_metadata is not None else None,
                        Jsonb(routing) if routing is not None else None,
                        pipeline_profile, profile_version, Jsonb(modules), bad_case_category,
                        error_code, error_message, now,
                    ),
                ).fetchone()
                connection.execute(
                    "UPDATE conversations SET updated_at=%s WHERE conversation_id=%s",
                    (now, conversation_id),
                )
        return self._answer(dict(row))

    def list_conversations(self, knowledge_base_id: str, owner_id: str) -> list[dict[str, Any]]:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT c.*, count(a.record_id)::integer AS turn_count,
                          (array_agg(a.status ORDER BY a.created_at DESC)
                           FILTER (WHERE a.record_id IS NOT NULL))[1] AS last_status
                   FROM conversations c
                   LEFT JOIN answer_records a ON a.conversation_id=c.conversation_id
                   WHERE c.knowledge_base_id=%s AND c.owner_id=%s
                   GROUP BY c.conversation_id ORDER BY c.updated_at DESC""",
                (knowledge_base_id, owner_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_conversations(self, knowledge_base_id: str) -> int:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT count(*) FROM conversations WHERE knowledge_base_id=%s",
                (knowledge_base_id,),
            ).fetchone()
        return int(row[0])

    def get_conversation(
        self, knowledge_base_id: str, conversation_id: str, owner_id: str
    ) -> dict[str, Any] | None:
        validate_knowledge_base_id(knowledge_base_id)
        self._validate_conversation_id(conversation_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            conversation = connection.execute(
                """SELECT * FROM conversations
                   WHERE conversation_id=%s AND knowledge_base_id=%s AND owner_id=%s""",
                (conversation_id, knowledge_base_id, owner_id),
            ).fetchone()
            if conversation is None:
                return None
            records = connection.execute(
                """SELECT a.*, q.policy_snapshot, q.active_index_version_id
                   FROM answer_records a
                   LEFT JOIN query_executions q ON q.execution_id=a.execution_id
                   WHERE a.conversation_id=%s ORDER BY a.created_at""",
                (conversation_id,),
            ).fetchall()
        return {**dict(conversation), "records": [self._answer(dict(row)) for row in records]}

    def get_answer(self, knowledge_base_id: str, record_id: str, owner_id: str) -> dict[str, Any] | None:
        validate_knowledge_base_id(knowledge_base_id)
        if not _ANSWER_RECORD_ID_PATTERN.fullmatch(record_id):
            raise ValueError("answer record id is invalid")
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT a.*, q.policy_snapshot, q.active_index_version_id
                   FROM answer_records a
                   JOIN conversations c ON c.conversation_id=a.conversation_id
                   LEFT JOIN query_executions q ON q.execution_id=a.execution_id
                   WHERE a.record_id=%s AND a.knowledge_base_id=%s AND c.owner_id=%s""",
                (record_id, knowledge_base_id, owner_id),
            ).fetchone()
        return self._answer(dict(row)) if row else None

    def delete_conversation(self, knowledge_base_id: str, conversation_id: str, owner_id: str) -> bool:
        validate_knowledge_base_id(knowledge_base_id)
        self._validate_conversation_id(conversation_id)
        with psycopg.connect(self.database_url) as connection:
            result = connection.execute(
                """DELETE FROM conversations
                   WHERE conversation_id=%s AND knowledge_base_id=%s AND owner_id=%s""",
                (conversation_id, knowledge_base_id, owner_id),
            )
        return result.rowcount > 0

    def list_bad_cases(
        self,
        knowledge_base_id: str,
        owner_id: str,
        category: str | None = None,
        error_code: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["a.knowledge_base_id=%s", "c.owner_id=%s", "a.status='failed'"]
        parameters: list[object] = [knowledge_base_id, owner_id]
        if category:
            clauses.append("COALESCE(a.bad_case_category,'unclassified')=%s")
            parameters.append(category)
        if error_code:
            clauses.append("a.error_code=%s")
            parameters.append(error_code)
        if created_from:
            clauses.append("a.created_at >= %s")
            parameters.append(created_from)
        if created_to:
            clauses.append("a.created_at <= %s")
            parameters.append(created_to)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                f"""SELECT a.* FROM answer_records a
                    JOIN conversations c ON c.conversation_id=a.conversation_id
                    WHERE {' AND '.join(clauses)} ORDER BY a.created_at DESC""",
                parameters,
            ).fetchall()
        return [self._answer(dict(row)) for row in rows]

    def get_execution(
        self,
        execution_id: str,
        owner_id: str,
    ) -> dict[str, Any] | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            execution = connection.execute(
                """SELECT q.*, a.routing AS stored_routing
                   FROM query_executions q
                   LEFT JOIN answer_records a ON a.execution_id=q.execution_id
                   WHERE q.execution_id=%s""",
                (execution_id,),
            ).fetchone()
            if execution is None or execution["owner_id"] != owner_id:
                return None
            modules = connection.execute(
                """SELECT * FROM module_executions
                   WHERE execution_id=%s ORDER BY sequence""",
                (execution_id,),
            ).fetchall()
        route = dict(execution["stored_routing"] or {}) or {
            "intent": execution["intent"],
            "confidence": execution["intent_confidence"],
            "reason": execution["routing_reason"] or "",
            "control_outcome": execution["control_outcome"],
            "original_question": execution["original_question"],
            "effective_question": execution["effective_question"],
            "follow_up_rewritten": execution["follow_up_rewritten"],
            "requires_web": execution["requires_web"],
            "classifier_model": execution["classifier_model"],
            "fallback_used": execution["fallback_used"],
        }
        return {
            **dict(execution),
            "routing": route,
            "modules": [dict(item) for item in modules],
        }

    def import_legacy(self, payload: NormalizedLegacyPayload) -> tuple[int, int]:
        eligible_conversation_ids = {
            str(item["conversation_id"]) for item in payload.conversations
        }
        eligible_answer_ids = {
            str(item["record_id"])
            for item in payload.answers
            if item.get("conversation_id") in eligible_conversation_ids
        }
        with psycopg.connect(self.database_url) as connection:
            with connection.transaction():
                for item in payload.conversations:
                    connection.execute(
                        """INSERT INTO conversations
                           (conversation_id,knowledge_base_id,owner_id,title,legacy_content_sha256,
                            created_at,updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (conversation_id) DO NOTHING""",
                        (
                            item["conversation_id"], item["knowledge_base_id"], item["owner_id"],
                            item.get("title") or "历史会话", _legacy_item_hash("conversation", item),
                            item["created_at"], item["updated_at"],
                        ),
                    )
                for item in payload.answers:
                    connection.execute(
                        """INSERT INTO answer_records
                           (record_id,conversation_id,knowledge_base_id,question,status,answer,
                            sources,latency_ms,models,model_metadata,prompt_version,prompt_hash,
                            answer_status,generation_governance,query_metadata,bad_case_category,
                            error_code,error_message,legacy_content_sha256,created_at)
                           SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                           WHERE EXISTS (SELECT 1 FROM conversations WHERE conversation_id=%s)
                           ON CONFLICT (record_id) DO NOTHING""",
                        (
                            item["record_id"], item["conversation_id"], item["knowledge_base_id"],
                            item.get("question") or "", item.get("status") or "failed", item.get("answer"),
                            Jsonb(item.get("sources") or []), Jsonb(item.get("latency_ms") or {}),
                            Jsonb(item.get("models") or {}), Jsonb(item.get("model_metadata") or {}),
                            item.get("prompt_version"), item.get("prompt_hash"), item.get("answer_status"),
                            Jsonb(item.get("generation_governance")) if item.get("generation_governance") is not None else None,
                            Jsonb(item.get("query_metadata")) if item.get("query_metadata") is not None else None,
                            item.get("bad_case_category"), item.get("error_code"), item.get("error_message"),
                            _legacy_item_hash("answer", item), item["created_at"], item["conversation_id"],
                        ),
                    )
                conversation_receipts = (
                    dict(connection.execute(
                        "SELECT conversation_id,legacy_content_sha256 FROM conversations WHERE conversation_id=ANY(%s)",
                        (list(eligible_conversation_ids),),
                    ).fetchall()) if eligible_conversation_ids else {}
                )
                answer_receipts = (
                    dict(connection.execute(
                        "SELECT record_id,legacy_content_sha256 FROM answer_records WHERE record_id=ANY(%s)",
                        (list(eligible_answer_ids),),
                    ).fetchall()) if eligible_answer_ids else {}
                )
                expected_conversation_receipts = {
                    str(item["conversation_id"]): _legacy_item_hash("conversation", item)
                    for item in payload.conversations
                }
                expected_answer_receipts = {
                    str(item["record_id"]): _legacy_item_hash("answer", item)
                    for item in payload.answers if item.get("conversation_id") in eligible_conversation_ids
                }
                imported_conversations = len(conversation_receipts)
                imported_answers = len(answer_receipts)
                if imported_conversations != len(eligible_conversation_ids) or imported_answers != len(eligible_answer_ids):
                    raise RuntimeError("旧会话导入数量核对失败，事务已回滚")
                if conversation_receipts != expected_conversation_receipts or answer_receipts != expected_answer_receipts:
                    raise RuntimeError("旧会话导入内容哈希核对失败，事务已回滚")
                connection.execute(
                    """INSERT INTO conversation_migration_runs
                       (source_sha256,conversation_count,answer_count)
                       VALUES (%s,%s,%s)
                       ON CONFLICT (source_sha256) DO UPDATE SET
                         conversation_count=EXCLUDED.conversation_count,
                         answer_count=EXCLUDED.answer_count,
                         imported_at=now()""",
                    (payload.sha256, imported_conversations, imported_answers),
                )
        return imported_conversations, imported_answers

    @staticmethod
    def _insert_evidence(connection, execution_id: str, sources: list[dict[str, Any]]) -> None:
        for index, source in enumerate(sources, start=1):
            source_type = str(source.get("evidence_source_type") or "knowledge_base")
            evidence_id = str(source.get("chunk_id") or f"web_{index}")
            connection.execute(
                """INSERT INTO query_evidence
                   (execution_id,evidence_id,source_type,chunk_id,source_url,title,locator,
                    content_excerpt,content_sha256,retrieval_score,rerank_score,selected,
                    citation_index,retrieved_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s)""",
                (
                    execution_id, evidence_id, source_type,
                    source.get("chunk_id") if source_type == "knowledge_base" else None,
                    source.get("source_url"), source.get("filename") or source.get("source_url") or "来源",
                    Jsonb({
                        "page": source.get("page"), "paragraph": source.get("paragraph"),
                        "heading_path": source.get("heading_path") or [],
                    }), str(source.get("text") or "")[:20_000], source.get("content_sha256"),
                    float(source.get("retrieval_score") or 0), float(source.get("rerank_score") or 0),
                    index, source.get("retrieved_at"),
                ),
            )

    @staticmethod
    def _answer(row: dict[str, Any]) -> dict[str, Any]:
        for key in (
            "sources", "latency_ms", "models", "model_metadata", "generation_governance",
            "query_metadata", "routing", "module_summary",
        ):
            if key in row and row[key] is None and key in {"sources", "latency_ms", "models", "model_metadata", "module_summary"}:
                row[key] = [] if key in {"sources", "module_summary"} else {}
        return row

    @staticmethod
    def _validate_conversation_id(conversation_id: str) -> None:
        if not _CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
            raise ValueError("conversation id is invalid")


class PostgresRAGPolicyRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def get(self, knowledge_base_id: str) -> RAGPolicy:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_base_rag_policies WHERE knowledge_base_id=%s",
                (knowledge_base_id,),
            ).fetchone()
        if row is None:
            return RAGPolicy()
        return RAGPolicy(
            rollout_stage=str(row["rollout_stage"]),
            web_search_enabled=bool(row["web_search_enabled"]),
            allowed_domains=tuple(row["allowed_domains"] or []),
            intent_confidence_threshold=float(row["intent_confidence_threshold"]),
            minimum_evidence_count=int(row["minimum_evidence_count"]),
            max_web_results=int(row["max_web_results"]),
            profile_versions={
                **DEFAULT_PROFILE_VERSIONS,
                **dict(row["profile_versions"] or {}),
            },
        )

    def update(self, knowledge_base_id: str, policy: RAGPolicy, updated_by: str) -> RAGPolicy:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url) as connection:
            with connection.transaction():
                current = connection.execute(
                    """SELECT rollout_stage,stage_started_at,web_search_enabled,allowed_domains,
                              intent_confidence_threshold,minimum_evidence_count,max_web_results,
                              profile_versions
                       FROM knowledge_base_rag_policies
                       WHERE knowledge_base_id=%s FOR UPDATE""",
                    (knowledge_base_id,),
                ).fetchone()
                if current is not None:
                    if str(current[0]) == "canary" and policy.rollout_stage == "full":
                        current_signature = (
                            bool(current[2]),
                            tuple(current[3] or []),
                            float(current[4]),
                            int(current[5]),
                            int(current[6]),
                            dict(current[7] or {}),
                        )
                        requested_signature = (
                            policy.web_search_enabled,
                            policy.allowed_domains,
                            policy.intent_confidence_threshold,
                            policy.minimum_evidence_count,
                            policy.max_web_results,
                            policy.profile_versions,
                        )
                        if current_signature != requested_signature:
                            raise ValueError(
                                "从 canary 发布到 full 时不能同时修改 RAG 策略；"
                                "请先保存策略并重新完成灰度门禁"
                            )
                    self._validate_rollout_transition(
                        connection,
                        knowledge_base_id,
                        str(current[0]),
                        policy.rollout_stage,
                        current[1],
                    )
                connection.execute(
                    """INSERT INTO knowledge_base_rag_policies
                       (knowledge_base_id,rollout_stage,web_search_enabled,allowed_domains,
                        intent_confidence_threshold,minimum_evidence_count,max_web_results,
                        profile_versions,updated_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (knowledge_base_id) DO UPDATE SET
                         rollout_stage=EXCLUDED.rollout_stage,
                         web_search_enabled=EXCLUDED.web_search_enabled,
                         allowed_domains=EXCLUDED.allowed_domains,
                         intent_confidence_threshold=EXCLUDED.intent_confidence_threshold,
                         minimum_evidence_count=EXCLUDED.minimum_evidence_count,
                         max_web_results=EXCLUDED.max_web_results,
                         profile_versions=EXCLUDED.profile_versions,
                         updated_by=EXCLUDED.updated_by,
                         stage_started_at=CASE
                           WHEN ROW(
                             knowledge_base_rag_policies.rollout_stage,
                             knowledge_base_rag_policies.web_search_enabled,
                             knowledge_base_rag_policies.allowed_domains,
                             knowledge_base_rag_policies.intent_confidence_threshold,
                             knowledge_base_rag_policies.minimum_evidence_count,
                             knowledge_base_rag_policies.max_web_results,
                             knowledge_base_rag_policies.profile_versions
                           ) IS DISTINCT FROM ROW(
                             EXCLUDED.rollout_stage,
                             EXCLUDED.web_search_enabled,
                             EXCLUDED.allowed_domains,
                             EXCLUDED.intent_confidence_threshold,
                             EXCLUDED.minimum_evidence_count,
                             EXCLUDED.max_web_results,
                             EXCLUDED.profile_versions
                           ) THEN now()
                           ELSE knowledge_base_rag_policies.stage_started_at
                         END,
                         updated_at=now()""",
                    (
                        knowledge_base_id, policy.rollout_stage, policy.web_search_enabled,
                        list(policy.allowed_domains), policy.intent_confidence_threshold,
                        policy.minimum_evidence_count, policy.max_web_results,
                        Jsonb(policy.profile_versions), updated_by,
                    ),
                )
        return self.get(knowledge_base_id)

    @staticmethod
    def _validate_rollout_transition(
        connection,
        knowledge_base_id: str,
        current_stage: str,
        requested_stage: str,
        stage_started_at: datetime,
    ) -> None:
        if current_stage == requested_stage:
            return
        if current_stage == "shadow" and requested_stage == "full":
            raise ValueError("RAG 发布阶段必须先从 shadow 进入 canary")
        if current_stage != "canary" or requested_stage != "full":
            return
        row = connection.execute(
            """SELECT count(*) FILTER (
                        WHERE q.status='succeeded'
                          AND a.answer_status IN ('answered','source_conflict')
                      )::integer AS successes,
                      min(q.created_at) FILTER (
                        WHERE q.status='succeeded'
                          AND a.answer_status IN ('answered','source_conflict')
                      ) AS first_success_at,
                      count(*) FILTER (
                        WHERE q.status='succeeded'
                          AND a.answer_status IN ('answered','source_conflict')
                          AND (
                            COALESCE((a.generation_governance->>'citation_valid')::boolean,false)=false
                            OR COALESCE((a.generation_governance->>'claim_citation_coverage')::boolean,false)=false
                          )
                      )::integer AS citation_failures
               FROM query_executions q
               LEFT JOIN answer_records a ON a.execution_id=q.execution_id
               WHERE q.knowledge_base_id=%s
                 AND q.created_at >= %s
                 AND q.policy_snapshot->>'rollout_stage'='canary'""",
            (knowledge_base_id, stage_started_at),
        ).fetchone()
        successes = int(row[0] or 0)
        first_success_at = row[1]
        citation_failures = int(row[2] or 0)
        intent_report = connection.execute(
            """SELECT passed FROM evaluation_runs
               WHERE evaluation_type='intent_routing' AND status='succeeded' AND official
                 AND run_at >= %s
               ORDER BY run_at DESC LIMIT 1""",
            (stage_started_at,),
        ).fetchone()
        acceptance = connection.execute(
            """SELECT status FROM acceptance_runs
               WHERE knowledge_base_id=%s AND created_at >= %s
               ORDER BY created_at DESC LIMIT 1""",
            (knowledge_base_id, stage_started_at),
        ).fetchone()
        intent_rows = connection.execute(
            """SELECT q.intent, q.policy_snapshot->>'rollout_stage' AS stage,
                      count(*)::integer AS total,
                      count(*) FILTER (
                        WHERE a.answer_status IN ('answered','source_conflict')
                      )::integer AS successful
               FROM query_executions q
               LEFT JOIN answer_records a ON a.execution_id=q.execution_id
               WHERE q.knowledge_base_id=%s
                 AND q.intent IS NOT NULL
                 AND (
                   (q.created_at >= %s AND q.policy_snapshot->>'rollout_stage'='canary')
                   OR (q.created_at < %s AND q.policy_snapshot->>'rollout_stage'='shadow')
                 )
               GROUP BY q.intent,q.policy_snapshot->>'rollout_stage'""",
            (knowledge_base_id, stage_started_at, stage_started_at),
        ).fetchall()
        intent_rates = {
            (str(item[0]), str(item[1])): (int(item[2]), int(item[3]) / int(item[2]))
            for item in intent_rows
            if int(item[2]) > 0
        }
        latency_row = connection.execute(
            """SELECT
                 percentile_cont(0.95) WITHIN GROUP (ORDER BY q.total_latency_ms)
                   FILTER (
                     WHERE q.created_at >= %s
                       AND q.policy_snapshot->>'rollout_stage'='canary'
                       AND NOT EXISTS (
                         SELECT 1 FROM query_evidence e
                         WHERE e.execution_id=q.execution_id AND e.source_type='web'
                       )
                   ) AS canary_kb_p95,
                 percentile_cont(0.95) WITHIN GROUP (ORDER BY q.total_latency_ms)
                   FILTER (
                     WHERE q.created_at < %s
                       AND q.policy_snapshot->>'rollout_stage'='shadow'
                       AND NOT EXISTS (
                         SELECT 1 FROM query_evidence e
                         WHERE e.execution_id=q.execution_id AND e.source_type='web'
                       )
                   ) AS shadow_kb_p95,
                 percentile_cont(0.95) WITHIN GROUP (ORDER BY q.total_latency_ms)
                   FILTER (
                     WHERE q.created_at >= %s
                       AND q.policy_snapshot->>'rollout_stage'='canary'
                       AND EXISTS (
                         SELECT 1 FROM query_evidence e
                         WHERE e.execution_id=q.execution_id AND e.source_type='web'
                       )
                   ) AS canary_web_p95
               FROM query_executions q WHERE q.knowledge_base_id=%s""",
            (stage_started_at, stage_started_at, stage_started_at, knowledge_base_id),
        ).fetchone()
        elapsed_days = (
            (datetime.now(UTC) - first_success_at).total_seconds() / 86400
            if first_success_at is not None
            else 0
        )
        reasons: list[str] = []
        if successes < 100:
            reasons.append(f"成功灰度执行 {successes}/100 次")
        if elapsed_days < 3:
            reasons.append(f"灰度持续 {elapsed_days:.1f}/3 天")
        if citation_failures:
            reasons.append(f"存在 {citation_failures} 次引用校验失败")
        if intent_report is None or intent_report[0] is not True:
            reasons.append("缺少 canary 阶段通过的正式意图路由评测（Macro-F1 ≥ 0.90）")
        if acceptance is None or acceptance[0] != "passed":
            reasons.append("缺少 canary 阶段通过的知识库验收报告（含 ACL 泄漏为 0）")
        for intent in ("fact_lookup", "summarize", "compare", "procedure"):
            shadow = intent_rates.get((intent, "shadow"))
            canary = intent_rates.get((intent, "canary"))
            if shadow and canary and shadow[0] >= 10 and canary[0] >= 10:
                if canary[1] < shadow[1] - 0.02:
                    reasons.append(
                        f"{intent} 任务成功率较 shadow 下降超过 2 个百分点"
                    )
        canary_kb_p95 = float(latency_row[0]) if latency_row and latency_row[0] is not None else None
        shadow_kb_p95 = float(latency_row[1]) if latency_row and latency_row[1] is not None else None
        canary_web_p95 = float(latency_row[2]) if latency_row and latency_row[2] is not None else None
        if canary_kb_p95 is not None and shadow_kb_p95 and canary_kb_p95 > shadow_kb_p95 * 1.3:
            reasons.append("KB-only P95 延迟相对 shadow 基线上升超过 30%")
        if canary_web_p95 is not None and canary_web_p95 > 15_000:
            reasons.append("Web 管线 P95 总耗时超过 15 秒")
        if reasons:
            raise ValueError("Full 发布门禁未通过：" + "；".join(reasons))

    def active_capabilities(self, knowledge_base_id: str) -> tuple[str | None, dict[str, object]]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT index_version_id, component_manifest FROM index_versions
                   WHERE knowledge_base_id=%s AND status='active'""",
                (knowledge_base_id,),
            ).fetchone()
        return (str(row["index_version_id"]), dict(row["component_manifest"] or {})) if row else (None, {})


def _legacy_item_hash(kind: str, item: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"kind": kind, "value": item},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
