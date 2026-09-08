from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .chunking import chunking_version, parse_chunking_version, split_sections
from .config import Settings
from .connectors import validate_object_key
from .data_source_sync import run_sync
from .document_snapshots import current_document_set
from .document_classifier import DocumentClassifier
from .errors import AppError
from .index_versions import (
    Actor,
    CREATION_REASONS,
    FORCED_CREATION_REASONS,
    active_index_version_id,
    active_or_bootstrap_version,
    component_manifest,
    config_fingerprint,
    create_building_version,
    create_building_version_in_transaction,
    finalize_building_version,
    list_versions,
    record_lifecycle_event,
    release_fingerprint,
)
from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID, validate_knowledge_base_id
from .lexical import LexicalIndexCache
from .models import AnswerGenerator, EmbeddingModel, Reranker, get_generator
from .parsers import parse_structured_document
from .pipeline_governance import (
    aggregate_index_build,
    create_operation,
    ensure_index_build,
    update_index_build_for_job,
    update_index_stage,
    update_sync_resource_for_job,
    upsert_document_index_state,
)
from .retrieval_access import RetrievalAccessContext
from .schemas import DocumentInfo, QueryMetadataFilter
from .security import write_private_file
from .service import RAGService
from .store import RetrievedChunk

# 任务失败后的退避间隔。索引与分类共用同一个值：两者的失败原因同源（外部依赖
# 暂时不可用），没有理由给它们两套节奏。
RETRY_BACKOFF_SECONDS = 5


class _RetryableClassification(RuntimeError):
    """分类遇到了值得重试的外部故障，交回队列按既有退避重来。"""


def _stable_id(prefix: str, *parts: str) -> str:
    value = hashlib.sha256("\0".join(parts).encode()).hexdigest()[:20]
    return f"{prefix}_{value}"


class PostgresVectorStore:
    def __init__(self, database_url: str, upload_root: Path):
        self.database_url = database_url
        self.upload_root = upload_root

    def resolve_active_version(self, knowledge_base_id: str) -> str | None:
        """解析当前生效的索引版本；读路径全部以它为界，未放行的版本对用户不存在。

        返回 None 而不是抛错：尚未索引过的知识库本就没有可检索内容，让 SQL 的
        ``= NULL`` 自然匹配不到，空知识库、处理中、无权限等状态仍由 service 层区分。

        **一次检索只应调用一次，解析结果向下传给全部读方法。** 此前 ``query``、
        ``load_current_chunks``、``score_by_ids``、``chunk_fingerprint`` 各自解析，
        一次混合检索最多解析六次；期间若发生索引切换，向量与词法两路就会读到不同版本，
        混合检索结果跨版本。现在版本由 ``retrieve_candidates`` 在入口解析一次并贯穿全程。
        """

        return active_index_version_id(self.database_url, knowledge_base_id)

    def query(
        self,
        embedding: list[float],
        limit: int,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        query_text: str | None = None,
        filters: QueryMetadataFilter | None = None,
        access: RetrievalAccessContext | None = None,
        *,
        index_version_id: str | None,
    ) -> list[RetrievedChunk]:
        validate_knowledge_base_id(knowledge_base_id)
        clauses = [
            "c.knowledge_base_id = %s",
            "c.index_version_id = %s",
            "s.retrieval_enabled = true",
        ]
        parameters: list[Any] = [knowledge_base_id, index_version_id]
        if filters:
            if filters.category_ids:
                clauses.append("c.metadata->>'category_id' = ANY(%s)")
                parameters.append(filters.category_ids)
            if filters.categories:
                clauses.append("c.metadata->>'category' = ANY(%s)")
                parameters.append(filters.categories)
            if filters.tags:
                clauses.append("c.metadata->'tags' ?| %s")
                parameters.append(filters.tags)
            if filters.source_types:
                clauses.append("c.metadata->>'source_type' = ANY(%s)")
                parameters.append(filters.source_types)
            if filters.created_from:
                clauses.append("c.created_at >= %s")
                parameters.append(filters.created_from)
            if filters.created_to:
                clauses.append("c.created_at <= %s")
                parameters.append(filters.created_to)
        clauses.extend(
            [
                "COALESCE(c.metadata->>'retrieval_status', 'searchable') = 'searchable'",
                "(c.metadata->>'valid_from' IS NULL OR (c.metadata->>'valid_from')::timestamptz <= now())",
                "(c.metadata->>'valid_to' IS NULL OR (c.metadata->>'valid_to')::timestamptz >= now())",
            ]
        )
        if access:
            clauses.extend(
                [
                    "NOT (COALESCE(c.metadata->'deny_user_ids', '[]'::jsonb) ? %s)",
                    "(jsonb_array_length(COALESCE(c.metadata->'allow_user_ids', "
                    "'[]'::jsonb)) = 0 OR COALESCE(c.metadata->'allow_user_ids', "
                    "'[]'::jsonb) ? %s)",
                    "NOT (COALESCE(s.acl->'deny_user_ids', '[]'::jsonb) ? %s)",
                    "(jsonb_array_length(COALESCE(s.acl->'allow_user_ids', "
                    "'[]'::jsonb)) = 0 OR COALESCE(s.acl->'allow_user_ids', "
                    "'[]'::jsonb) ? %s)",
                ]
            )
            parameters.extend([access.user_id] * 4)
        where_clause = " AND ".join(clauses)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            register_vector(connection)
            rows = connection.execute(
                f"""SELECT c.chunk_id, c.content,
                          c.metadata || jsonb_build_object(
                            'document_version_id', c.document_version_id,
                            'content_sha256', v.content_sha256
                          ) AS metadata,
                          1 - (c.embedding <=> %s::vector) AS retrieval_score
                   FROM chunks c
                   JOIN documents d
                     ON d.knowledge_base_id = c.knowledge_base_id
                    AND d.document_id = (c.metadata->>'document_id')
                    AND d.current_version_id = c.document_version_id
                   JOIN data_sources s ON s.data_source_id = d.data_source_id
                   JOIN document_versions v ON v.document_version_id = c.document_version_id
                   WHERE {where_clause}
                   ORDER BY c.embedding <=> %s::vector
                   LIMIT %s""",
                (embedding, *parameters, embedding, limit),
            ).fetchall()
        return [
            RetrievedChunk(
                chunk_id=str(row["chunk_id"]),
                text=str(row["content"]),
                metadata=dict(row["metadata"]),
                retrieval_score=round(float(row["retrieval_score"]), 6),
            )
            for row in rows
        ]

    def load_current_chunks(
        self,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        access: RetrievalAccessContext | None = None,
        *,
        index_version_id: str | None,
    ) -> list[RetrievedChunk]:
        """读回当前版本的全部分块，供词法索引构建与融合阶段复原候选。

        过滤条件与 ``query`` 完全一致，两路召回因此始终看到同一批分块；
        ``retrieval_score`` 留 0，由调用方在需要时补算。
        """

        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            access_sql = ""
            parameters: list[Any] = [knowledge_base_id, index_version_id]
            if access:
                access_sql = """AND NOT (COALESCE(c.metadata->'deny_user_ids', '[]'::jsonb) ? %s)
                    AND (jsonb_array_length(COALESCE(c.metadata->'allow_user_ids', '[]'::jsonb)) = 0
                         OR COALESCE(c.metadata->'allow_user_ids', '[]'::jsonb) ? %s)
                    AND NOT (COALESCE(s.acl->'deny_user_ids', '[]'::jsonb) ? %s)
                    AND (jsonb_array_length(COALESCE(s.acl->'allow_user_ids', '[]'::jsonb)) = 0
                         OR COALESCE(s.acl->'allow_user_ids', '[]'::jsonb) ? %s)"""
                parameters.extend([access.user_id] * 4)
            rows = connection.execute(
                f"""SELECT c.chunk_id, c.content,
                          c.metadata || jsonb_build_object(
                            'document_version_id', c.document_version_id,
                            'content_sha256', v.content_sha256
                          ) AS metadata
                   FROM chunks c
                   JOIN documents d
                     ON d.knowledge_base_id = c.knowledge_base_id
                    AND d.document_id = (c.metadata->>'document_id')
                    AND d.current_version_id = c.document_version_id
                   JOIN data_sources s ON s.data_source_id = d.data_source_id
                   JOIN document_versions v ON v.document_version_id = c.document_version_id
                   WHERE c.knowledge_base_id = %s
                     AND c.index_version_id = %s
                     AND s.retrieval_enabled = true
                     AND COALESCE(c.metadata->>'retrieval_status', 'searchable') = 'searchable'
                     AND (c.metadata->>'valid_from' IS NULL
                          OR (c.metadata->>'valid_from')::timestamptz <= now())
                     AND (c.metadata->>'valid_to' IS NULL OR (c.metadata->>'valid_to')::timestamptz >= now())
                     {access_sql}
                   ORDER BY c.chunk_id""",
                parameters,
            ).fetchall()
        return [
            RetrievedChunk(
                chunk_id=str(row["chunk_id"]),
                text=str(row["content"]),
                metadata=dict(row["metadata"]),
                retrieval_score=0.0,
            )
            for row in rows
        ]

    def chunk_fingerprint(
        self,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        *,
        index_version_id: str | None,
    ) -> str:
        """当前版本分块集合的廉价指纹，用于跨进程判断词法索引是否已经过期。

        新增与删除会改变计数，索引重建会改变最新写入时间；ACL 版本变化也会立即失效缓存。
        active 索引版本必须计入：切换索引版本时分块集合本身不变，只有指针动了，
        指纹若不含它，API 进程会继续用旧版本的倒排，混合检索会命中已被切走的分块。
        """

        validate_knowledge_base_id(knowledge_base_id)
        active = index_version_id
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                """SELECT count(*), COALESCE(max(c.created_at), to_timestamp(0)),
                          COALESCE(max((c.metadata->>'acl_version')::integer), 1),
                          COALESCE(max((c.metadata->'data_source_acl'->>'version')::integer), 1),
                          COALESCE(max(c.metadata->>'classified_at'), ''),
                          COALESCE(max(s.updated_at), to_timestamp(0))
                   FROM chunks c
                   JOIN documents d
                     ON d.knowledge_base_id = c.knowledge_base_id
                    AND d.document_id = (c.metadata->>'document_id')
                    AND d.current_version_id = c.document_version_id
                   JOIN data_sources s ON s.data_source_id=d.data_source_id
                   WHERE c.knowledge_base_id = %s AND c.index_version_id = %s""",
                (knowledge_base_id, active),
            ).fetchone()
        return f"{active}:{int(row[0])}:{row[1].isoformat()}:{int(row[2])}:{int(row[3])}:{row[4]}:{row[5].isoformat()}"

    def score_by_ids(
        self,
        chunk_ids: list[str],
        embedding: list[float],
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        *,
        index_version_id: str | None,
    ) -> dict[str, float]:
        """为指定分块补算向量相似度。

        词法独有的候选若把 ``retrieval_score`` 留在 0，会被 ``rank_candidates``
        的归一化压到最低，等于无理由给词法召回降权，页面上的相关度也会显示为 0。
        """

        validate_knowledge_base_id(knowledge_base_id)
        if not chunk_ids:
            return {}
        with psycopg.connect(self.database_url) as connection:
            register_vector(connection)
            rows = connection.execute(
                """SELECT chunk_id, 1 - (embedding <=> %s::vector)
                   FROM chunks
                   WHERE knowledge_base_id = %s AND index_version_id = %s
                     AND chunk_id = ANY(%s)""",
                (embedding, knowledge_base_id, index_version_id, chunk_ids),
            ).fetchall()
        return {str(row[0]): round(float(row[1]), 6) for row in rows}

    def list_documents(self, knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID) -> list[dict[str, Any]]:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT d.document_id, d.data_source_id, d.filename, d.current_version_id, d.metadata,
                          d.created_at, s.source_type, s.acl AS data_source_acl,
                          current_version.status AS current_status,
                          count(c.chunk_id) AS chunk_count,
                          pending.status AS pending_status,
                          pending.failure_reason AS pending_failure_reason
                   FROM documents d
                   JOIN data_sources s ON s.data_source_id = d.data_source_id
                   LEFT JOIN document_versions current_version
                     ON current_version.document_version_id = d.current_version_id
                   -- 只数 active 索引版本的分块：重建期间两个版本并存，不过滤会让分块数翻倍。
                   LEFT JOIN chunks c ON c.document_version_id = d.current_version_id
                                     AND c.index_version_id = %s
                   LEFT JOIN LATERAL (
                       SELECT dv.status, dv.failure_reason FROM document_versions dv
                       WHERE dv.knowledge_base_id = d.knowledge_base_id
                         AND dv.document_id = d.document_id
                         AND dv.status IN ('pending', 'indexing', 'failed')
                       ORDER BY dv.version_number DESC LIMIT 1
                   ) pending ON true
                   WHERE d.knowledge_base_id = %s
                   GROUP BY d.document_id, d.data_source_id, d.filename, d.current_version_id, d.metadata,
                            d.created_at, s.source_type, s.acl, current_version.status, pending.status,
                            pending.failure_reason
                   ORDER BY lower(d.filename)""",
                (self.resolve_active_version(knowledge_base_id), knowledge_base_id),
            ).fetchall()
        return [
            {
                "knowledge_base_id": knowledge_base_id,
                "document_id": row["document_id"],
                "data_source_id": row["data_source_id"],
                "filename": row["filename"],
                "chunk_count": int(row["chunk_count"]),
                "status": row["pending_status"] or row["current_status"] or "pending",
                "index_failure_reason": row["pending_failure_reason"],
                # 没有分类就是 None。默认「未分类」会把一份没跑成分类的资料显示成
                # 「已归入某分类」，正是这次要消灭的混淆。
                "category": dict(row["metadata"] or {}).get("category"),
                "category_id": dict(row["metadata"] or {}).get("category_id"),
                "tags": dict(row["metadata"] or {}).get("tags", []),
                "source_type": row["source_type"],
                "created_at": row["created_at"],
                "source_system": dict(row["metadata"] or {}).get("source_system", "upload"),
                "external_resource_id": dict(row["metadata"] or {}).get("external_resource_id"),
                "owner_user_id": dict(row["metadata"] or {}).get("owner_user_id"),
                "department": dict(row["metadata"] or {}).get("department"),
                "sensitivity": dict(row["metadata"] or {}).get("sensitivity", "internal"),
                "valid_from": dict(row["metadata"] or {}).get("valid_from"),
                "valid_to": dict(row["metadata"] or {}).get("valid_to"),
                "retrieval_status": dict(row["metadata"] or {}).get("retrieval_status", "searchable"),
                # 供 service.list_documents 按检索侧同一套判据过滤。DocumentInfo 的
                # extra 策略是默认的 ignore，所以它不会进 API 响应——数据源 ACL 本身
                # 也是不该外泄的东西。
                "data_source_acl": dict(row["data_source_acl"] or {}),
                "acl_version": dict(row["metadata"] or {}).get("acl_version", 1),
                "allow_user_ids": dict(row["metadata"] or {}).get("allow_user_ids", []),
                "deny_user_ids": dict(row["metadata"] or {}).get("deny_user_ids", []),
                "classification_status": dict(row["metadata"] or {}).get("classification_status", "pending"),
                "classification_confidence": dict(row["metadata"] or {}).get("classification_confidence"),
                "suggested_category_id": dict(row["metadata"] or {}).get("suggested_category_id"),
                "classification_model": dict(row["metadata"] or {}).get("classification_model"),
                "classified_at": dict(row["metadata"] or {}).get("classified_at"),
                "classification_failure_code": dict(row["metadata"] or {}).get(
                    "classification_failure_code"
                ),
                "classification_failure_reason": dict(row["metadata"] or {}).get(
                    "classification_failure_reason"
                ),
                "classification_failed_at": dict(row["metadata"] or {}).get(
                    "classification_failed_at"
                ),
                "classification_retry_count": dict(row["metadata"] or {}).get(
                    "classification_retry_count"
                ) or 0,
                "classification_next_retry_at": dict(row["metadata"] or {}).get(
                    "classification_next_retry_at"
                ),
            }
            for row in rows
        ]

    def update_document_metadata(
        self,
        document_id: str,
        metadata: dict[str, object],
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> bool:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url) as connection, connection.transaction():
            row = connection.execute(
                """UPDATE documents SET metadata = metadata || %s, updated_at = %s
                   WHERE knowledge_base_id = %s AND document_id = %s
                   RETURNING current_version_id""",
                (Jsonb(metadata), datetime.now(UTC), knowledge_base_id, document_id),
            ).fetchone()
            if row is None:
                return False
            if row[0]:
                connection.execute(
                    """UPDATE chunks SET metadata = metadata || %s
                       WHERE knowledge_base_id = %s AND document_version_id = %s""",
                    (Jsonb(metadata), knowledge_base_id, row[0]),
                )
        return True

    def update_document_acl(
        self,
        document_id: str,
        allow_user_ids: list[str],
        deny_user_ids: list[str],
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> int | None:
        validate_knowledge_base_id(knowledge_base_id)
        now = datetime.now(UTC)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                document = connection.execute(
                    """SELECT metadata, current_version_id FROM documents
                       WHERE knowledge_base_id = %s AND document_id = %s FOR UPDATE""",
                    (knowledge_base_id, document_id),
                ).fetchone()
                if document is None:
                    return None
                version = int(dict(document["metadata"] or {}).get("acl_version", 1)) + 1
                policy = {
                    "acl_version": version,
                    "allow_user_ids": allow_user_ids,
                    "deny_user_ids": deny_user_ids,
                }
                connection.execute(
                    """UPDATE documents SET metadata = metadata || %s, updated_at = %s
                       WHERE knowledge_base_id = %s AND document_id = %s""",
                    (Jsonb(policy), now, knowledge_base_id, document_id),
                )
                if document["current_version_id"]:
                    connection.execute(
                        """UPDATE chunks SET metadata = metadata || %s
                           WHERE knowledge_base_id = %s AND document_version_id = %s""",
                        (Jsonb(policy), knowledge_base_id, document["current_version_id"]),
                    )
        return version

    def delete_document(
        self,
        document_id: str,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> bool:
        validate_knowledge_base_id(knowledge_base_id)
        source_paths: list[str] = []
        with psycopg.connect(self.database_url) as connection, connection.transaction():
            active = connection.execute(
                """SELECT EXISTS (
                    SELECT 1 FROM index_jobs j
                    JOIN document_versions v ON v.document_version_id = j.document_version_id
                    WHERE v.knowledge_base_id = %s AND v.document_id = %s
                      AND j.status IN ('queued', 'running'))""",
                (knowledge_base_id, document_id),
            ).fetchone()[0]
            if active:
                raise AppError("INDEX_JOB_ACTIVE", "文档正在处理，暂时不能删除。", 409)
            source = connection.execute(
                """SELECT data_source_id FROM documents
                   WHERE knowledge_base_id = %s AND document_id = %s FOR UPDATE""",
                (knowledge_base_id, document_id),
            ).fetchone()
            if source is None:
                return False
            source_paths = [
                str(row[0])
                for row in connection.execute(
                    """SELECT source_path FROM document_versions
                       WHERE knowledge_base_id = %s AND document_id = %s""",
                    (knowledge_base_id, document_id),
                ).fetchall()
            ]
            connection.execute(
                """UPDATE documents SET current_version_id = NULL
                   WHERE knowledge_base_id = %s AND document_id = %s""",
                (knowledge_base_id, document_id),
            )
            connection.execute(
                """DELETE FROM index_jobs WHERE document_version_id IN (
                    SELECT document_version_id FROM document_versions
                    WHERE knowledge_base_id = %s AND document_id = %s)""",
                (knowledge_base_id, document_id),
            )
            connection.execute(
                "DELETE FROM document_versions WHERE knowledge_base_id = %s AND document_id = %s",
                (knowledge_base_id, document_id),
            )
            connection.execute(
                "DELETE FROM documents WHERE knowledge_base_id = %s AND document_id = %s",
                (knowledge_base_id, document_id),
            )
            # 只清理上传自建的那种「一份资料独占一个」的伪数据源。
            #
            # 上传路径在 index_document 里按文件名自建一条 source_type='file' 的数据源
            # （见那里 `if data_source_id is None` 的分支），一份资料一个，删资料时把它
            # 一起清掉是应有的收尾。而同步来的资料挂在真实数据源上
            # （local_directory / object_storage / web / connector），那是用户配置的实体，
            # 承载着同步游标、凭据引用与墓碑，删一份资料绝不该把它带走。
            #
            # 原先这里是无条件删除。对同步来的资料，实测直接抛
            # ForeignKeyViolation（index_jobs_data_source_id_fkey 仍引用它），
            # 整个删除事务回滚——用户点删除只看到一个外键错误，而资料删不掉；
            # 即便绕过那个引用，documents 的 ON DELETE RESTRICT 也会在「还有别的资料」
            # 时拒绝，而在「这是最后一份」时放行，让数据源连配置一起静默消失。
            connection.execute(
                """DELETE FROM data_sources
                   WHERE data_source_id = %s AND source_type = 'file'
                     AND NOT EXISTS (
                       SELECT 1 FROM documents d WHERE d.data_source_id = data_sources.data_source_id
                     )""",
                (source[0],),
            )
        upload_root = self.upload_root.resolve()
        for relative in source_paths:
            path = (upload_root / relative).resolve()
            if path.is_relative_to(upload_root):
                path.unlink(missing_ok=True)
        return True

    def count(self, knowledge_base_id: str | None = None) -> int:
        with psycopg.connect(self.database_url) as connection:
            if knowledge_base_id is None:
                return int(connection.execute("SELECT count(*) FROM chunks").fetchone()[0])
            validate_knowledge_base_id(knowledge_base_id)
            return int(
                connection.execute(
                    "SELECT count(*) FROM chunks WHERE knowledge_base_id = %s",
                    (knowledge_base_id,),
                ).fetchone()[0]
            )


class PostgresAsyncRAGService(RAGService):

    def __init__(
        self,
        settings: Settings,
        embedder: EmbeddingModel,
        reranker: Reranker,
        generator: AnswerGenerator,
    ):
        if not settings.database_url:
            raise ValueError("DATABASE_URL is required")
        self.database_url = settings.database_url
        store = PostgresVectorStore(settings.database_url, settings.upload_path)
        super().__init__(
            settings,
            store,
            embedder,
            reranker,
            generator,
            # 词法倒排按知识库懒加载，并在每次取用时比对分块指纹，
            # 因此独立 Worker 进程写入的新分块无需显式通知即可被感知。
            LexicalIndexCache(
                lambda knowledge_base_id, index_version_id: [
                    (item.chunk_id, item.text)
                    for item in store.load_current_chunks(
                        knowledge_base_id, index_version_id=index_version_id
                    )
                ],
                lambda knowledge_base_id, index_version_id: store.chunk_fingerprint(
                    knowledge_base_id, index_version_id=index_version_id
                ),
            ),
        )

    def _resolve_category_names(
        self, knowledge_base_id: str, filters: QueryMetadataFilter | None
    ) -> QueryMetadataFilter | None:
        validate_knowledge_base_id(knowledge_base_id)
        if filters is None or not filters.categories:
            return filters
        with psycopg.connect(self.database_url) as connection:
            resolved = [
                row[0]
                for row in connection.execute(
                    """SELECT category_id FROM document_categories
                       WHERE knowledge_base_id = %s AND normalized_name = ANY(%s)""",
                    (knowledge_base_id, [name.strip().casefold() for name in filters.categories]),
                ).fetchall()
            ]
        # 一个名字都解析不出来时给一个不可能命中的 ID，而不是放行。放行等于把「按不存在
        # 的分类过滤」变成「不过滤」，用户划定的范围会被悄悄取消。状态文案走的正是这条路。
        merged = [*filters.category_ids, *resolved] or ["cat_" + "0" * 16]
        return filters.model_copy(update={"categories": [], "category_ids": merged})

    def list_index_versions(
        self,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> list[dict[str, object]]:
        validate_knowledge_base_id(knowledge_base_id)
        return list_versions(self.database_url, knowledge_base_id)

    def index_document(
        self,
        filename: str,
        content: bytes,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        metadata: dict[str, object] | None = None,
        data_source_id: str | None = None,
        relative_path: str | None = None,
        sync_run_id: str | None = None,
    ) -> DocumentInfo:
        """索引一份文档。

        ``data_source_id`` 与 ``relative_path`` 供数据源同步使用，都不传时行为与
        API 上传路径完全一致：

        - ``relative_path`` 保留目录结构。只取 ``Path(filename).name`` 会让同步来的
          ``a/x.md`` 与 ``b/x.md`` 算出同一个 document_id 互相覆盖。
        - ``data_source_id`` 指定归属。不传时按文件名自建一个数据源，那是上传场景的
          语义；同步场景下所有对象都属于同一个数据源，不能各自新建。
        """

        validate_knowledge_base_id(knowledge_base_id)
        if relative_path is None:
            safe_name = Path(filename).name
        else:
            safe_name = validate_object_key(relative_path)
        content_hash = hashlib.sha256(content).hexdigest()
        document_id = _stable_id("doc", knowledge_base_id, safe_name.casefold())
        source_id = data_source_id or _stable_id("src", knowledge_base_id, safe_name.casefold())
        now = datetime.now(UTC)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                if not connection.execute(
                    "SELECT 1 FROM knowledge_bases WHERE knowledge_base_id = %s",
                    (knowledge_base_id,),
                ).fetchone():
                    raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404)
                # 按数据源名匹配是 V2→V3 的迁移兼容逻辑。同步场景已显式给定归属，
                # 再按名字去猜会匹配到别的数据源。
                migrated_identity = None if data_source_id else connection.execute(
                    """SELECT s.data_source_id, d.document_id
                       FROM data_sources s
                       LEFT JOIN documents d ON d.data_source_id = s.data_source_id
                       WHERE s.knowledge_base_id = %s AND s.name = %s""",
                    (knowledge_base_id, safe_name),
                ).fetchone()
                if migrated_identity:
                    source_id = str(migrated_identity["data_source_id"])
                    if migrated_identity["document_id"]:
                        document_id = str(migrated_identity["document_id"])
                if data_source_id is not None:
                    # 大小写碰撞：document_id 由 safe_name.casefold() 折算，因此
                    # `Docs/A.md` 与 `docs/a.md` 会算出同一个 id。S3 与 Linux 目录都允许
                    # 两者并存——data_source_objects 里是两行，documents 里只有一行，
                    # 于是软删其中一个会让另一个也退出检索，两条资源行还可能共享同一个
                    # document_version_id 而被一次更新掉。
                    #
                    # 这里选择明确报错而不是改 id 推导：去掉 casefold 会让所有含大写字母的
                    # 存量文档在下次同步时算出新 id，旧记录全部变成孤儿。把静默的数据
                    # 相互覆盖换成一个可诊断的失败，是这个约束下能做到的最好结果。
                    collision = connection.execute(
                        """SELECT filename FROM documents
                           WHERE knowledge_base_id = %s AND document_id = %s
                             AND filename <> %s""",
                        (knowledge_base_id, document_id, safe_name),
                    ).fetchone()
                    if collision:
                        raise AppError(
                            "SOURCE_OBJECT_KEY_COLLISION",
                            f"对象键 {safe_name} 与既有资料 {collision['filename']} "
                            "仅大小写不同，会被合并成同一份文档。请重命名其中一个。",
                            409,
                        )
                    # 显式给定归属（同步路径）时先认领该文档，且必须在下面的幂等短路**之前**：
                    # 内容一字未改时短路会直接返回，后面的 documents upsert 根本跑不到，
                    # 归属就永远停在最初创建它的那个数据源上。
                    #
                    # 归属分叉的后果是静默的：_known_objects(only_indexed=True) 的 EXISTS
                    # 要求 documents 与 data_source_objects 的 data_source_id 相等，不等则该
                    # 对象永远进不了 indexed，每次同步都被归入 retry、全量重解析加重嵌入，
                    # 而批次照常收口，没有任何状态位显示异常。
                    #
                    # 撞上 UNIQUE (knowledge_base_id, data_source_id, filename) 时放弃认领：
                    # 目标数据源下已有同名文档，硬改会破坏那一条记录。宁可维持现状。
                    try:
                        with connection.transaction():
                            connection.execute(
                                """UPDATE documents SET data_source_id = %s, updated_at = %s
                                   WHERE knowledge_base_id = %s AND document_id = %s
                                     AND data_source_id <> %s""",
                                (source_id, now, knowledge_base_id, document_id, source_id),
                            )
                    except psycopg.errors.UniqueViolation:
                        pass
                existing = connection.execute(
                    """SELECT dv.document_version_id, dv.status,
                              COALESCE((SELECT count(*) FROM chunks c
                                        WHERE c.document_version_id = dv.document_version_id), 0) chunks
                       FROM document_versions dv
                       WHERE dv.knowledge_base_id = %s AND dv.document_id = %s
                         AND dv.content_sha256 = %s""",
                    (knowledge_base_id, document_id, content_hash),
                ).fetchone()
                if existing and str(existing["status"]) != "superseded":
                    return DocumentInfo(
                        knowledge_base_id=knowledge_base_id,
                        document_id=document_id,
                        filename=safe_name,
                        chunk_count=int(existing["chunks"]),
                        status=str(existing["status"]),
                    )
                if existing:
                    # 内容回退：这份内容曾经存在过，但已被后来的版本取代。短路返回是错的
                    # ——它不入队、不移动 current_version_id，于是远端已经回退、检索侧
                    # 却永久返回被取代的那一版，而且下次同步会判定 unchanged，不会自愈。
                    #
                    # 也不能插一条同哈希的新版本：documents_versions 上有
                    # UNIQUE (knowledge_base_id, document_id, content_sha256)。
                    # 正确做法是把这个既有版本重新入队，让它重新成为 current。
                    revived = str(existing["document_version_id"])
                    connection.execute(
                        """UPDATE document_versions
                           SET status = 'pending', failure_reason = NULL, indexed_at = NULL
                           WHERE document_version_id = %s""",
                        (revived,),
                    )
                    connection.execute(
                        """INSERT INTO index_jobs
                           (index_job_id, knowledge_base_id, data_source_id, document_version_id,
                            idempotency_key, status, max_attempts, job_type, sync_run_id)
                           VALUES (%s, %s, %s, %s, %s, 'queued', %s, 'index', %s)
                           ON CONFLICT (document_version_id)
                             WHERE document_version_id IS NOT NULL
                               AND status IN ('queued', 'running')
                           DO NOTHING""",
                        (
                            f"job_{uuid4().hex[:20]}",
                            knowledge_base_id,
                            source_id,
                            revived,
                            # 键必须逐次唯一：index_jobs.idempotency_key 是全表唯一约束，
                            # 而同一份内容可以被回退多次（A→B→A→B→A）。用固定的
                            # revive:{version_id} 的话第二次就撞键，UniqueViolation 未被
                            # ON CONFLICT (document_version_id) 覆盖，该对象直接进 dead_letter。
                            # 「不重复入队」由下面那条 ON CONFLICT 保证，不靠这个键。
                            f"revive:{revived}:{uuid4().hex[:12]}",
                            self.settings.index_job_max_attempts,
                            sync_run_id,
                        ),
                    )
                    return DocumentInfo(
                        knowledge_base_id=knowledge_base_id,
                        document_id=document_id,
                        filename=safe_name,
                        chunk_count=int(existing["chunks"]),
                        status="pending",
                    )
                if data_source_id is None:
                    # 上传场景按文件名自建数据源；同步场景的数据源由同步流程预先创建，
                    # 再插一条会把 local_directory 覆盖成 file。
                    connection.execute(
                        """INSERT INTO data_sources
                           (data_source_id, knowledge_base_id, source_type, name, configuration,
                            created_at, updated_at)
                           VALUES (%s, %s, 'file', %s, '{}'::jsonb, %s, %s)
                           ON CONFLICT (data_source_id)
                           DO UPDATE SET updated_at = EXCLUDED.updated_at""",
                        (source_id, knowledge_base_id, safe_name, now, now),
                    )
                connection.execute(
                    """INSERT INTO documents
                       (document_id, knowledge_base_id, data_source_id, filename,
                        metadata, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (knowledge_base_id, document_id)
                       DO UPDATE SET filename = EXCLUDED.filename,
                                     -- 合并而不是替换，并且**治理字段以库里的为准**。
                                     -- ACL 与人工下架状态都存在这份 metadata 里
                                     -- （update_document_acl / update_document_metadata 都写
                                     -- `metadata || {...}`），而本次调用方给的 metadata 是
                                     -- 上传表单或 _build_metadata() 拼出来的，压根不含这些键：
                                     -- 原先 `metadata = EXCLUDED.metadata` 是整体替换，于是
                                     -- 重新上传同名文件、或同步更新一个对象，都会把
                                     -- allow_user_ids/deny_user_ids/acl_version/retrieval_status
                                     -- 一起清空。检索侧把空 allow 列表当作「不限人」
                                     -- （见本文件 retrieve 的 jsonb_array_length 判据），
                                     -- 一份限定给某人的资料就此对全知识库可见；
                                     -- 人工下架也会被悄悄恢复上架。
                                     -- 也不能反过来无脑 `documents.metadata || EXCLUDED.metadata`
                                     -- ——那样调用方仍能覆盖这四个键。所以先合并，再把治理字段
                                     -- 用库里的值盖回去。
                                     metadata = (documents.metadata || EXCLUDED.metadata)
                                         || COALESCE(
                                              jsonb_strip_nulls(jsonb_build_object(
                                                'acl_version', documents.metadata -> 'acl_version',
                                                'allow_user_ids', documents.metadata -> 'allow_user_ids',
                                                'deny_user_ids', documents.metadata -> 'deny_user_ids',
                                                'retrieval_status', documents.metadata -> 'retrieval_status'
                                              )),
                                              '{}'::jsonb
                                            ),
                                     -- 显式给定归属时（同步路径）由本次调用认领该文档。
                                     -- 此前 data_source_id 只在 INSERT 时写、之后永不更新：
                                     -- 同一份资料先经上传（自建 'file' 源）再进同步目录时，
                                     -- documents 与 data_source_objects 的归属就此分叉，而
                                     -- _known_objects(only_indexed=True) 的 EXISTS 要求两者相等，
                                     -- 于是该对象永远进不了 indexed，每次同步都被判成 retry，
                                     -- 每次都全量重解析加重嵌入——批次却正常收口，毫无异常迹象。
                                     data_source_id = CASE WHEN %s
                                         THEN EXCLUDED.data_source_id
                                         ELSE documents.data_source_id END,
                                     updated_at = EXCLUDED.updated_at""",
                    (document_id, knowledge_base_id, source_id, safe_name, Jsonb(metadata or {}),
                     now, now, data_source_id is not None),
                )
                version_number = int(
                    connection.execute(
                        """SELECT COALESCE(max(version_number), 0) + 1 AS next_version
                           FROM document_versions
                           WHERE knowledge_base_id = %s AND document_id = %s""",
                        (knowledge_base_id, document_id),
                    ).fetchone()["next_version"]
                )
                version_id = _stable_id("ver", knowledge_base_id, document_id, content_hash)
                extension = Path(safe_name).suffix.lower()
                relative_path = f"{knowledge_base_id}/{document_id}/{content_hash}{extension}"
                write_private_file(self.settings.upload_path / relative_path, content)
                connection.execute(
                    """INSERT INTO document_versions
                       (document_version_id, knowledge_base_id, document_id, version_number,
                        content_sha256, source_file_bytes, source_path, status, created_at,
                        source_uri, source_etag, source_modified_at, sync_run_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s)""",
                    (
                        version_id,
                        knowledge_base_id,
                        document_id,
                        version_number,
                        content_hash,
                        len(content),
                        relative_path,
                        now,
                        # 来源三列只有同步路径填得出：API 上传没有远端 URI 与 etag，
                        # 留空本身就是「这份资料是人传的」这一事实。
                        (metadata or {}).get("source_uri"),
                        (metadata or {}).get("source_etag"),
                        (metadata or {}).get("source_modified_at"),
                        sync_run_id or None,
                    ),
                )
                operation_id = None
                if sync_run_id is None:
                    operation_type = "file_upload" if version_number == 1 else "file_update"
                    operation_id = create_operation(
                        connection, operation_type=operation_type, knowledge_base_id=knowledge_base_id,
                        data_source_id=source_id, document_id=document_id,
                        document_version_id=version_id,
                        idempotency_key=f"{operation_type.replace('_', '-')}:{version_id}", progress_mode="stages",
                    )
                    connection.execute(
                        """INSERT INTO document_processing_runs
                           (processing_run_id, operation_id, document_id, document_version_id,
                            processing_type, status, uploaded_bytes, total_bytes)
                           VALUES (%s,%s,%s,%s,%s,'parsing',%s,%s)""",
                        (f"dpr_{uuid4().hex[:20]}", operation_id, document_id, version_id,
                         operation_type, len(content), len(content)),
                    )
                    connection.execute(
                        """UPDATE operations SET status='running', current_stage='parse',
                                  progress_percent=20, total_count=5, completed_count=1,
                                  started_at=now(), updated_at=now() WHERE operation_id=%s""",
                        (operation_id,),
                    )
                connection.execute(
                    """INSERT INTO index_jobs
                       (index_job_id, knowledge_base_id, data_source_id, document_version_id,
                        idempotency_key, status, max_attempts, job_type, target_chunking_version,
                        sync_run_id, operation_id)
                       VALUES (%s, %s, %s, %s, %s, 'queued', %s, 'index', %s, %s, %s)""",
                    (
                        f"job_{uuid4().hex[:20]}",
                        knowledge_base_id,
                        source_id,
                        version_id,
                        f"index:{version_id}",
                        self.settings.index_job_max_attempts,
                        chunking_version(self.settings.chunk_size, self.settings.chunk_overlap),
                        sync_run_id,
                        operation_id,
                    ),
                )
        return DocumentInfo(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            filename=safe_name,
            chunk_count=0,
            status="pending",
        )


def register_embedding_model(database_url: str, model_name: str, dimension: int) -> None:
    """首次写入分块时登记向量模型，之后拒绝任何不一致的写入。

    没有这道校验，换模型后新旧维度会混存在同一张 chunks 表里，直到检索执行 ``<=>``
    才报错——那时索引已经被污染，只能全量重建。
    """

    with psycopg.connect(database_url) as connection, connection.transaction():
        row = connection.execute(
            "SELECT embedding_model, embedding_dimension FROM index_settings WHERE singleton"
        ).fetchone()
        if row is None:
            connection.execute(
                """INSERT INTO index_settings (embedding_model, embedding_dimension)
                   VALUES (%s, %s)""",
                (model_name, dimension),
            )
            return
        if str(row[0]) != model_name or int(row[1]) != dimension:
            raise AppError(
                "EMBEDDING_MODEL_MISMATCH",
                f"索引使用 {row[0]}（{row[1]} 维），当前配置为 {model_name}（{dimension} 维）。"
                "请先清空索引或执行全量重建。",
                409,
            )


def check_embedding_model(database_url: str, model_name: str) -> None:
    """启动时校验配置的模型与已有索引一致；尚未索引过任何内容时跳过。

    启动阶段不加载模型，因此只能比对名称，维度由写入路径的 register 负责。
    """

    with psycopg.connect(database_url) as connection:
        row = connection.execute("SELECT embedding_model FROM index_settings WHERE singleton").fetchone()
    if row is not None and str(row[0]) != model_name:
        raise RuntimeError(f"索引使用 {row[0]}，当前配置为 {model_name}；请先执行全量重建或恢复原模型配置")


def _building_version_for_rebuild(
    database_url: str,
    knowledge_base_id: str,
    target_chunking_version: str,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[str, str] | None:
    """取得或创建本次重建使用的 building 索引版本，返回 (索引版本 id, 批次 id)。

    没有任何可重建文档时返回 None，调用方据此直接结束：此时不该留下索引版本记录。

    并发发起时由 building 唯一约束裁决：抢输的一方重新读取，若目标配置相同就跟着
    补齐同一个版本，不同则明确报错，而不是悄悄产生两套配置混合的索引。
    """

    def _existing(connection: Any) -> dict[str, Any] | None:
        return connection.execute(
            """SELECT index_version_id, chunking_version, rebuild_batch_id
               FROM index_versions
               WHERE knowledge_base_id = %s AND status = 'building'""",
            (knowledge_base_id,),
        ).fetchone()

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        if not connection.execute(
            "SELECT 1 FROM knowledge_bases WHERE knowledge_base_id = %s",
            (knowledge_base_id,),
        ).fetchone():
            raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404)
        settings_row = connection.execute(
            "SELECT embedding_model, embedding_dimension FROM index_settings WHERE singleton"
        ).fetchone()
        current = _existing(connection)
        # 解析器版本按实际涵盖的全部格式聚合：知识库混用 Markdown 与 DOCX 时，
        # 单取一个值会掩盖另一种解析器的版本。
        parser_row = connection.execute(
            """SELECT string_agg(DISTINCT v.parser_version, ',') AS parser_version,
                      count(*) AS candidate_count
               FROM documents d
               JOIN document_versions v ON v.document_version_id = d.current_version_id
               WHERE d.knowledge_base_id = %s
                 AND NOT EXISTS (
                     SELECT 1 FROM index_jobs j
                     WHERE j.document_version_id = v.document_version_id
                       AND j.status IN ('queued', 'running'))""",
            (knowledge_base_id,),
        ).fetchone()
    if current is None and int(parser_row["candidate_count"]) == 0:
        return None
    if current is not None:
        if str(current["chunking_version"]) != target_chunking_version:
            raise AppError(
                "REBUILD_IN_PROGRESS",
                f"已有目标配置为 {current['chunking_version']} 的重建进行中，"
                "请先完成或清理该批次。",
                409,
            )
        return str(current["index_version_id"]), str(current["rebuild_batch_id"])
    if settings_row is None:
        raise AppError(
            "INDEX_NOT_INITIALIZED", "索引尚未登记向量模型，请先完成一次索引。", 409
        )
    batch_id = f"rbd_{uuid4().hex[:16]}"
    try:
        index_version_id, _ = create_building_version(
            database_url,
            knowledge_base_id,
            chunking_version=target_chunking_version,
            parser_version=str(parser_row["parser_version"] or "legacy"),
            embedding_model=str(settings_row["embedding_model"]),
            embedding_dimension=int(settings_row["embedding_dimension"]),
            processing_options={"chunk_size": chunk_size, "chunk_overlap": chunk_overlap},
            rebuild_batch_id=batch_id,
        )
    except psycopg.errors.UniqueViolation:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            current = _existing(connection)
        if current is None:
            raise
        if str(current["chunking_version"]) != target_chunking_version:
            raise AppError(
                "REBUILD_IN_PROGRESS",
                f"已有目标配置为 {current['chunking_version']} 的重建进行中，"
                "请先完成或清理该批次。",
                409,
            ) from None
        return str(current["index_version_id"]), str(current["rebuild_batch_id"])
    return index_version_id, batch_id


def enqueue_rebuild(
    database_url: str,
    knowledge_base_id: str,
    target_chunking_version: str,
    max_attempts: int = 3,
) -> dict[str, object]:
    """为知识库建立一个 building 索引版本，并把当前版本批量排队重建到该版本。

    只覆盖 ``current_version_id`` 指向的版本：历史版本不参与检索，重切它们没有收益。
    与 V5-4 之前不同，不再按"切分配置是否已一致"跳过文档——全库级切换要求新索引版本
    覆盖全量文档，漏一篇即新版本不完整、不能放行。续跑改由"该版本是否已覆盖该文档"
    判定：已有分块或有活动任务的文档会被跳过，重复调用因此仍然安全。

    同一知识库同时只允许一个 building 版本（数据库 partial unique index 保证）。
    重复调用同一目标配置会复用它继续补齐；目标配置不同则拒绝，避免半成品索引混入
    两套配置的分块。
    """

    validate_knowledge_base_id(knowledge_base_id)
    _, chunk_size, chunk_overlap = parse_chunking_version(target_chunking_version)
    prepared = _building_version_for_rebuild(
        database_url, knowledge_base_id, target_chunking_version, chunk_size, chunk_overlap
    )
    if prepared is None:
        # 没有可重建的文档就不建索引版本：空知识库或首次索引尚未完成时，
        # 建出来的版本会是一个永远无法覆盖全量的空壳。
        return {
            "batch_id": None,
            "index_version_id": None,
            "knowledge_base_id": knowledge_base_id,
            "target_chunking_version": target_chunking_version,
            "queued": 0,
        }
    index_version_id, batch_id = prepared
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            index_build_id = ensure_index_build(
                connection, knowledge_base_id=knowledge_base_id,
                index_version_id=index_version_id,
            )
            # 任务必须带上 operation_id，否则 mark_stage 里那段
            # `if job.get("operation_id")` 永远不成立：parsing/chunking/vector/keyword/
            # metadata/validating 六个阶段照常执行，却一个都不会被记录，进度条只能靠
            # progress_percent 猜位置。
            build_operation_id = connection.execute(
                "SELECT operation_id FROM index_builds WHERE index_build_id=%s",
                (index_build_id,),
            ).fetchone()["operation_id"]
            # 清单来自该版本冻结的文档快照，不再现查 documents——现查的话，建版本之后
            # 上传的资料会混进本次构建，而 finalize 的分母是快照，两边对不上。
            inventory = connection.execute(
                """SELECT m.document_id, m.document_version_id,
                          EXISTS (SELECT 1 FROM chunks c
                                  WHERE c.document_version_id=m.document_version_id
                                    AND c.index_version_id=%s) AS covered
                   FROM document_snapshot_members m
                   JOIN index_versions iv
                     ON iv.document_snapshot_id=m.document_snapshot_id
                   WHERE iv.index_version_id=%s AND m.inclusion_status='included'
                   ORDER BY m.document_id""",
                (index_version_id, index_version_id),
            ).fetchall()
            for item in inventory:
                upsert_document_index_state(
                    connection, index_build_id=index_build_id, index_version_id=index_version_id,
                    document_id=str(item["document_id"]),
                    document_version_id=str(item["document_version_id"]),
                    status="ready" if item["covered"] else "pending",
                )
            candidates = connection.execute(
                """SELECT m.document_version_id, m.document_id, d.data_source_id
                   FROM document_snapshot_members m
                   JOIN index_versions iv
                     ON iv.document_snapshot_id = m.document_snapshot_id
                   JOIN documents d
                     ON d.knowledge_base_id = iv.knowledge_base_id
                    AND d.document_id = m.document_id
                   WHERE iv.index_version_id = %s AND m.inclusion_status = 'included'
                     AND NOT EXISTS (
                         SELECT 1 FROM chunks c
                         WHERE c.document_version_id = m.document_version_id
                           AND c.index_version_id = %s)
                     AND NOT EXISTS (
                         SELECT 1 FROM index_jobs j
                         WHERE j.document_version_id = m.document_version_id
                           AND j.status IN ('queued', 'running'))
                   ORDER BY m.document_id""",
                (index_version_id, index_version_id),
            ).fetchall()
            queued = 0
            for candidate in candidates:
                upsert_document_index_state(
                    connection, index_build_id=index_build_id, index_version_id=index_version_id,
                    document_id=str(candidate["document_id"]),
                    document_version_id=str(candidate["document_version_id"]),
                )
                result = connection.execute(
                    """INSERT INTO index_jobs
                       (index_job_id, knowledge_base_id, data_source_id, document_version_id,
                        idempotency_key, status, max_attempts, job_type, rebuild_batch_id,
                        target_chunking_version, operation_id)
                       VALUES (%s, %s, %s, %s, %s, 'queued', %s, 'rebuild', %s, %s, %s)
                       ON CONFLICT (document_version_id)
                         WHERE document_version_id IS NOT NULL
                           AND status IN ('queued', 'running')
                       DO NOTHING""",
                    (
                        f"job_{uuid4().hex[:20]}",
                        knowledge_base_id,
                        candidate["data_source_id"],
                        candidate["document_version_id"],
                        f"rebuild:{batch_id}:{candidate['document_version_id']}",
                        max_attempts,
                        batch_id,
                        target_chunking_version,
                        build_operation_id,
                    ),
                )
                queued += result.rowcount
    aggregate_index_build(database_url, batch_id)
    return {
        "batch_id": batch_id,
        "index_version_id": index_version_id,
        "knowledge_base_id": knowledge_base_id,
        "target_chunking_version": target_chunking_version,
        "queued": queued,
        "index_build_id": index_build_id,
    }


def create_index_version_candidate(
    database_url: str,
    knowledge_base_id: str,
    *,
    reason: str,
    chunk_size: int,
    chunk_overlap: int,
    force: bool,
    force_reason: str | None,
    expected_config_fingerprint: str,
    expected_document_set_fingerprint: str,
    expected_release_fingerprint: str,
    requested_by: str,
    idempotency_key: str,
    reranker_model: str,
    max_concurrent_builds: int = 2,
    max_documents: int = 10000,
    max_attempts: int = 3,
) -> dict[str, object]:
    """按 Preview 证据原子创建 Snapshot、Version、Build、Operation 与 Jobs。

    Worker 的耗时处理在提交后执行；这里的事务只冻结事实并排队。任一步失败时，数据库
    不会留下没有 Build 的 Version 或没有 Jobs 的 Build。
    """

    validate_knowledge_base_id(knowledge_base_id)
    if reason not in CREATION_REASONS:
        raise AppError("INDEX_CREATION_REASON_INVALID", "索引版本创建原因无效。", 400)
    if reason in FORCED_CREATION_REASONS and not force:
        raise AppError(
            "INDEX_FORCE_CONFIRMATION_REQUIRED",
            "修复性或主动重建必须显式确认强制创建。",
            400,
        )
    if chunk_overlap >= chunk_size:
        raise AppError("CHUNKING_POLICY_INVALID", "切片重叠必须小于切片大小。", 400)
    target_chunking_version = chunking_version(chunk_size, chunk_overlap)
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        if connection.execute(
            "SELECT 1 FROM knowledge_bases WHERE knowledge_base_id=%s FOR UPDATE",
            (knowledge_base_id,),
        ).fetchone() is None:
            raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404)
        existing = connection.execute(
            """SELECT iv.index_version_id, iv.rebuild_batch_id, ib.index_build_id
               FROM index_versions iv
               JOIN index_builds ib ON ib.index_version_id=iv.index_version_id
               WHERE iv.knowledge_base_id=%s AND iv.creation_idempotency_key=%s
               ORDER BY ib.attempt_no LIMIT 1""",
            (knowledge_base_id, idempotency_key),
        ).fetchone()
        if existing:
            queued = connection.execute(
                """SELECT count(*) AS total FROM index_jobs
                   WHERE rebuild_batch_id=%s AND status='queued'""",
                (existing["rebuild_batch_id"],),
            ).fetchone()
            return {
                "batch_id": str(existing["rebuild_batch_id"]),
                "index_version_id": str(existing["index_version_id"]),
                "index_build_id": str(existing["index_build_id"]),
                "knowledge_base_id": knowledge_base_id,
                "target_chunking_version": target_chunking_version,
                "queued": int(queued["total"]),
                "reused": True,
            }
        in_progress = connection.execute(
            """SELECT index_version_id, status FROM index_versions
               WHERE knowledge_base_id=%s AND status IN ('building','validating','ready')
               ORDER BY created_at DESC LIMIT 1""",
            (knowledge_base_id,),
        ).fetchone()
        if in_progress:
            raise AppError(
                "INDEX_VERSION_IN_PROGRESS",
                f"已有候选版本 {in_progress['index_version_id']} 处于 {in_progress['status']}。",
                409,
            )
        # 全局 Build 容量必须在事务级 advisory lock 下判定；单纯先 count 再 INSERT，
        # 两个知识库并发创建时都可能看到“还剩最后一个名额”。
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext('index-governance-build-capacity'))"
        )
        active_builds = int(
            connection.execute(
                "SELECT count(*) AS total FROM index_versions WHERE status='building'"
            ).fetchone()["total"]
        )
        if active_builds >= max_concurrent_builds:
            raise AppError(
                "INDEX_BUILD_CAPACITY_EXCEEDED",
                f"当前已有 {active_builds} 个全量索引构建，达到并发上限 {max_concurrent_builds}。",
                429,
            )
        registered = connection.execute(
            "SELECT embedding_model, embedding_dimension FROM index_settings WHERE singleton"
        ).fetchone()
        if registered is None:
            raise AppError("INDEX_NOT_INITIALIZED", "索引尚未登记向量模型。", 409)
        parser = connection.execute(
            """SELECT string_agg(DISTINCT v.parser_version, ',') AS parser_version
               FROM documents d JOIN document_versions v
                 ON v.document_version_id=d.current_version_id
               WHERE d.knowledge_base_id=%s""",
            (knowledge_base_id,),
        ).fetchone()
        documents = current_document_set(connection, knowledge_base_id)
        if not documents["included"]:
            raise AppError(
                "INDEX_BUILD_EMPTY_KNOWLEDGE_BASE", "知识库暂无可构建资料。", 409
            )
        if len(documents["included"]) > max_documents:
            raise AppError(
                "INDEX_BUILD_SCOPE_TOO_LARGE",
                f"本次包含 {len(documents['included'])} 份资料，超过单次上限 {max_documents}。",
                409,
            )
        options = {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap}
        components = component_manifest(reranker_model=reranker_model)
        current_config = config_fingerprint(
            target_chunking_version,
            str(registered["embedding_model"]),
            int(registered["embedding_dimension"]),
            options,
            components,
        )
        current_release = release_fingerprint(
            config_fingerprint_value=current_config,
            document_set_fingerprint=str(documents["fingerprint"]),
            components=components,
        )
        if current_config != expected_config_fingerprint:
            raise AppError(
                "INDEX_CONFIG_CHANGED_AFTER_PREVIEW",
                "索引配置在预览后发生变化，请重新确认。",
                409,
            )
        if documents["fingerprint"] != expected_document_set_fingerprint:
            raise AppError(
                "DOCUMENT_SNAPSHOT_CHANGED_AFTER_PREVIEW",
                "文档集合在预览后发生变化，请重新确认。",
                409,
            )
        if current_release != expected_release_fingerprint:
            raise AppError(
                "INDEX_COMPONENTS_CHANGED_AFTER_PREVIEW",
                "索引组件版本在预览后发生变化，请重新确认。",
                409,
            )
        active = connection.execute(
            """SELECT iv.config_fingerprint, ds.snapshot_fingerprint
               FROM index_versions iv LEFT JOIN document_snapshots ds
                 ON ds.document_snapshot_id=iv.document_snapshot_id
               WHERE iv.knowledge_base_id=%s AND iv.status='active'""",
            (knowledge_base_id,),
        ).fetchone()
        if active is None and reason != "initial_build":
            raise AppError(
                "INDEX_CREATION_REASON_INVALID",
                "首个版本必须使用“创建首个索引版本”场景。",
                409,
            )
        if active is not None and reason == "initial_build":
            raise AppError(
                "INDEX_CREATION_REASON_INVALID",
                "知识库已经存在生效版本，不能再次使用首建场景。",
                409,
            )
        unchanged = bool(
            active
            and str(active["config_fingerprint"]) == current_config
            and active["snapshot_fingerprint"]
            and str(active["snapshot_fingerprint"]) == documents["fingerprint"]
        )
        if unchanged and not force:
            raise AppError(
                "INDEX_VERSION_NO_CHANGE",
                "配置与文档集合均未变化；如需修复性重建，请填写原因。",
                409,
            )
        if force and not (force_reason or "").strip():
            raise AppError("INDEX_FORCE_REASON_REQUIRED", "强制重建必须填写原因。", 400)

        batch_id = f"rbd_{uuid4().hex[:16]}"
        config_snapshot = {
            "chunking": {
                "version": target_chunking_version,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
            },
            "parser": {"version": str(parser["parser_version"] or "legacy")},
            "embedding": {
                "model": str(registered["embedding_model"]),
                "dimension": int(registered["embedding_dimension"]),
            },
            "processing_options": options,
            "components": components,
        }
        index_version_id, _ = create_building_version_in_transaction(
            connection,
            knowledge_base_id,
            chunking_version=target_chunking_version,
            parser_version=str(parser["parser_version"] or "legacy"),
            embedding_model=str(registered["embedding_model"]),
            embedding_dimension=int(registered["embedding_dimension"]),
            processing_options=options,
            rebuild_batch_id=batch_id,
            creation_reason=reason,
            force_reason=force_reason,
            requested_by=requested_by,
            creation_idempotency_key=idempotency_key,
            config_snapshot=config_snapshot,
            components=components,
        )
        index_build_id = ensure_index_build(
            connection,
            knowledge_base_id=knowledge_base_id,
            index_version_id=index_version_id,
        )
        build_operation_id = connection.execute(
            "SELECT operation_id FROM index_builds WHERE index_build_id=%s",
            (index_build_id,),
        ).fetchone()["operation_id"]
        queued = 0
        for item in documents["included"]:
            upsert_document_index_state(
                connection,
                index_build_id=index_build_id,
                index_version_id=index_version_id,
                document_id=item["document_id"],
                document_version_id=item["document_version_id"],
            )
            data_source = connection.execute(
                """SELECT data_source_id FROM documents
                   WHERE knowledge_base_id=%s AND document_id=%s""",
                (knowledge_base_id, item["document_id"]),
            ).fetchone()
            result = connection.execute(
                """INSERT INTO index_jobs
                   (index_job_id, knowledge_base_id, data_source_id, document_version_id,
                    idempotency_key, status, max_attempts, job_type, rebuild_batch_id,
                    target_chunking_version, operation_id)
                   VALUES (%s,%s,%s,%s,%s,'queued',%s,'rebuild',%s,%s,%s)
                   ON CONFLICT (document_version_id)
                     WHERE document_version_id IS NOT NULL
                       AND status IN ('queued','running')
                   DO NOTHING""",
                (
                    f"job_{uuid4().hex[:20]}",
                    knowledge_base_id,
                    data_source["data_source_id"] if data_source else None,
                    item["document_version_id"],
                    f"rebuild:{batch_id}:{item['document_version_id']}",
                    max_attempts,
                    batch_id,
                    target_chunking_version,
                    build_operation_id,
                ),
            )
            queued += result.rowcount
        if queued != len(documents["included"]):
            raise AppError(
                "DOCUMENT_INDEX_TASK_IN_PROGRESS",
                "文档任务状态在预览后发生变化，候选版本未创建；请等待现有任务完成后重试。",
                409,
            )
    aggregate_index_build(database_url, batch_id)
    return {
        "batch_id": batch_id,
        "index_version_id": index_version_id,
        "index_build_id": index_build_id,
        "knowledge_base_id": knowledge_base_id,
        "target_chunking_version": target_chunking_version,
        "queued": queued,
        "reused": False,
    }


def retry_index_version_build(
    database_url: str,
    knowledge_base_id: str,
    index_version_id: str,
    *,
    requested_by: str,
    max_concurrent_builds: int = 2,
    max_attempts: int = 3,
) -> dict[str, object]:
    """对同一 Version 的冻结 Snapshot 发起新的 Build attempt。

    这是执行重试，不是发布版本创建：Version id、version_no、config_snapshot 和
    document_snapshot_id 均保持不变。候选版本从未承载线上流量，因此先删除上一次失败
    或待验证产物，再按冻结清单完整重建，避免“修复性重建”实际复用损坏分块。
    """

    validate_knowledge_base_id(knowledge_base_id)
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        version = connection.execute(
            """SELECT status, document_snapshot_id, chunking_version
               FROM index_versions
               WHERE knowledge_base_id=%s AND index_version_id=%s FOR UPDATE""",
            (knowledge_base_id, index_version_id),
        ).fetchone()
        if version is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该知识库的索引版本。", 404)
        if str(version["status"]) == "building":
            current = connection.execute(
                """SELECT ib.index_build_id, iv.rebuild_batch_id
                   FROM index_versions iv JOIN index_builds ib USING (index_version_id)
                   WHERE iv.index_version_id=%s
                   ORDER BY ib.attempt_no DESC LIMIT 1""",
                (index_version_id,),
            ).fetchone()
            queued = connection.execute(
                """SELECT count(*) AS total FROM index_jobs
                   WHERE rebuild_batch_id=%s AND status='queued'""",
                (current["rebuild_batch_id"],),
            ).fetchone()
            return {
                "batch_id": str(current["rebuild_batch_id"]),
                "index_version_id": index_version_id,
                "index_build_id": str(current["index_build_id"]),
                "knowledge_base_id": knowledge_base_id,
                "target_chunking_version": str(version["chunking_version"]),
                "queued": int(queued["total"]),
                "reused": True,
            }
        if str(version["status"]) not in {
            "build_failed", "validating", "validation_failed"
        }:
            raise AppError(
                "INDEX_VERSION_NOT_REBUILDABLE",
                f"索引版本状态为 {version['status']}，不能对该候选版本重新构建。",
                409,
            )
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext('index-governance-build-capacity'))"
        )
        active_builds = int(
            connection.execute(
                "SELECT count(*) AS total FROM index_versions WHERE status='building'"
            ).fetchone()["total"]
        )
        if active_builds >= max_concurrent_builds:
            raise AppError(
                "INDEX_BUILD_CAPACITY_EXCEEDED",
                f"当前已有 {active_builds} 个全量索引构建，达到并发上限 {max_concurrent_builds}。",
                429,
            )
        if not version["document_snapshot_id"]:
            raise AppError(
                "INDEX_SNAPSHOT_UNAVAILABLE",
                "该历史版本没有文档快照，无法保证同一输入重建；请创建新索引版本。",
                409,
            )
        inventory = connection.execute(
            """SELECT m.document_id, m.document_version_id, d.data_source_id
               FROM document_snapshot_members m
               JOIN documents d
                 ON d.knowledge_base_id=%s AND d.document_id=m.document_id
               WHERE m.document_snapshot_id=%s AND m.inclusion_status='included'
               ORDER BY m.document_id""",
            (knowledge_base_id, version["document_snapshot_id"]),
        ).fetchall()
        if not inventory:
            raise AppError("INDEX_BUILD_EMPTY_SNAPSHOT", "该版本快照中没有可构建资料。", 409)
        document_version_ids = [str(item["document_version_id"]) for item in inventory]
        active_jobs = connection.execute(
            """SELECT count(*) AS total FROM index_jobs
               WHERE document_version_id=ANY(%s) AND status IN ('queued','running')""",
            (document_version_ids,),
        ).fetchone()
        if int(active_jobs["total"]) > 0:
            raise AppError(
                "DOCUMENT_INDEX_TASK_IN_PROGRESS",
                "快照中的资料仍有索引任务进行中，请稍后重试。",
                409,
            )

        from_status = str(version["status"])
        batch_id = f"rbd_{uuid4().hex[:16]}"
        # 候选版本不在线，清掉旧产物后全量重建最能保证修复语义；旧 Build、报告和
        # DocumentIndexState 均保留，仍可追溯失败现场。
        connection.execute("DELETE FROM chunks WHERE index_version_id=%s", (index_version_id,))
        connection.execute(
            """UPDATE index_versions
               SET status='building', rebuild_batch_id=%s, validation_report_id=NULL
               WHERE index_version_id=%s""",
            (batch_id, index_version_id),
        )
        index_build_id = ensure_index_build(
            connection,
            knowledge_base_id=knowledge_base_id,
            index_version_id=index_version_id,
        )
        operation = connection.execute(
            "SELECT operation_id FROM index_builds WHERE index_build_id=%s",
            (index_build_id,),
        ).fetchone()
        for item in inventory:
            upsert_document_index_state(
                connection,
                index_build_id=index_build_id,
                index_version_id=index_version_id,
                document_id=str(item["document_id"]),
                document_version_id=str(item["document_version_id"]),
            )
            inserted = connection.execute(
                """INSERT INTO index_jobs
                   (index_job_id, knowledge_base_id, data_source_id, document_version_id,
                    idempotency_key, status, max_attempts, job_type, rebuild_batch_id,
                    target_chunking_version, operation_id)
                   VALUES (%s,%s,%s,%s,%s,'queued',%s,'rebuild',%s,%s,%s)
                   ON CONFLICT (document_version_id)
                     WHERE document_version_id IS NOT NULL
                       AND status IN ('queued','running')
                   DO NOTHING""",
                (
                    f"job_{uuid4().hex[:20]}",
                    knowledge_base_id,
                    item["data_source_id"],
                    item["document_version_id"],
                    f"rebuild:{batch_id}:{item['document_version_id']}",
                    max_attempts,
                    batch_id,
                    version["chunking_version"],
                    operation["operation_id"],
                ),
            )
            if inserted.rowcount != 1:
                raise AppError(
                    "DOCUMENT_INDEX_TASK_IN_PROGRESS",
                    "快照中的资料出现新的处理任务，本次 Build attempt 未创建，请稍后重试。",
                    409,
                )
        record_lifecycle_event(
            connection,
            knowledge_base_id=knowledge_base_id,
            index_version_id=index_version_id,
            event_type="build_retried",
            from_status=from_status,
            to_status="building",
            actor=Actor(requested_by, "admin"),
            reason=f"新建 Build attempt，批次 {batch_id}",
        )
    aggregate_index_build(database_url, batch_id)
    return {
        "batch_id": batch_id,
        "index_version_id": index_version_id,
        "index_build_id": index_build_id,
        "knowledge_base_id": knowledge_base_id,
        "target_chunking_version": str(version["chunking_version"]),
        "queued": len(inventory),
        "reused": False,
    }


def cancel_index_version_build(
    database_url: str,
    knowledge_base_id: str,
    index_version_id: str,
    *,
    requested_by: str,
) -> dict[str, object]:
    """取消候选版本当前 Build；迟到 Worker 不得把任务重新收口为成功。"""

    validate_knowledge_base_id(knowledge_base_id)
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        version = connection.execute(
            """SELECT status, rebuild_batch_id FROM index_versions
               WHERE knowledge_base_id=%s AND index_version_id=%s FOR UPDATE""",
            (knowledge_base_id, index_version_id),
        ).fetchone()
        if version is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该知识库的索引版本。", 404)
        if str(version["status"]) == "build_failed":
            return {
                "knowledge_base_id": knowledge_base_id,
                "index_version_id": index_version_id,
                "status": "build_failed",
                "cancelled_jobs": 0,
            }
        if str(version["status"]) != "building":
            raise AppError(
                "INDEX_VERSION_BUILD_NOT_CANCELLABLE",
                f"索引版本状态为 {version['status']}，没有可取消的构建。",
                409,
            )
        build = connection.execute(
            """SELECT index_build_id, operation_id FROM index_builds
               WHERE index_version_id=%s ORDER BY attempt_no DESC LIMIT 1 FOR UPDATE""",
            (index_version_id,),
        ).fetchone()
        cancelled = connection.execute(
            """UPDATE index_jobs SET status='cancelled', finished_at=now(),
                      locked_at=NULL, locked_by=NULL, updated_at=now()
               WHERE rebuild_batch_id=%s AND status IN ('queued','running')""",
            (version["rebuild_batch_id"],),
        ).rowcount
        if build:
            connection.execute(
                """UPDATE document_index_states SET overall_status='cancelled', updated_at=now()
                   WHERE index_build_id=%s AND overall_status IN ('pending','building','validating')""",
                (build["index_build_id"],),
            )
            connection.execute(
                """UPDATE index_builds SET status='cancelled',
                          failure_code='INDEX_BUILD_CANCELLED',
                          failure_reason='管理员取消构建', finished_at=now(), updated_at=now()
                   WHERE index_build_id=%s""",
                (build["index_build_id"],),
            )
            connection.execute(
                """UPDATE operations SET status='cancelled', current_stage='cancelled',
                          error_code='INDEX_BUILD_CANCELLED', error_message='管理员取消构建',
                          finished_at=now(), updated_at=now()
                   WHERE operation_id=%s""",
                (build["operation_id"],),
            )
        connection.execute(
            "UPDATE index_versions SET status='build_failed' WHERE index_version_id=%s",
            (index_version_id,),
        )
        record_lifecycle_event(
            connection,
            knowledge_base_id=knowledge_base_id,
            index_version_id=index_version_id,
            event_type="build_failed",
            from_status="building",
            to_status="build_failed",
            actor=Actor(requested_by, "admin"),
            reason="操作者取消构建",
        )
    return {
        "knowledge_base_id": knowledge_base_id,
        "index_version_id": index_version_id,
        "status": "build_failed",
        "cancelled_jobs": int(cancelled),
    }


def rebuild_status(database_url: str, batch_id: str) -> dict[str, object]:
    """汇总一个重建批次的任务状态，并顺带推进索引版本状态机。

    状态查询是操作者唯一会反复执行的命令，把 building → validating / build_failed 的判定挂在
    这里，避免"任务都跑完了但版本还停在 building、无法切换"这种需要额外命令的中间态。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        version_row = connection.execute(
            "SELECT index_version_id FROM index_versions WHERE rebuild_batch_id = %s",
            (batch_id,),
        ).fetchone()
    index_version_id = str(version_row["index_version_id"]) if version_row else None
    index_version_status = (
        finalize_building_version(database_url, index_version_id) if index_version_id else None
    )
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """SELECT status, count(*) AS total FROM index_jobs
               WHERE rebuild_batch_id = %s GROUP BY status""",
            (batch_id,),
        ).fetchall()
        failures = connection.execute(
            """SELECT document_version_id, failure_reason FROM index_jobs
               WHERE rebuild_batch_id = %s AND status = 'failed'
               ORDER BY document_version_id""",
            (batch_id,),
        ).fetchall()
    counts = {str(row["status"]): int(row["total"]) for row in rows}
    return {
        "batch_id": batch_id,
        "index_version_id": index_version_id,
        "index_version_status": index_version_status,
        "counts": counts,
        "pending": counts.get("queued", 0) + counts.get("running", 0),
        "failures": [dict(row) for row in failures],
    }


def chunking_inventory(database_url: str, knowledge_base_id: str) -> dict[str, int]:
    """按切分配置统计各索引版本覆盖的文档数，用于验证重建是否真正收敛。

    统计源从 ``document_versions.chunking_version`` 换成了索引版本：重建不再回写
    文档版本的切分配置，因为新分块此时还在未放行的 building 版本里，回写会让
    文档版本谎称自己已是新配置。已清理的版本不计入。
    """

    validate_knowledge_base_id(knowledge_base_id)
    with psycopg.connect(database_url) as connection:
        rows = connection.execute(
            """SELECT iv.chunking_version, count(DISTINCT c.document_version_id)
               FROM index_versions iv
               LEFT JOIN chunks c ON c.index_version_id = iv.index_version_id
               WHERE iv.knowledge_base_id = %s
                 AND iv.status IN ('active', 'building', 'validating', 'ready', 'previous')
               GROUP BY iv.chunking_version
               ORDER BY iv.chunking_version""",
            (knowledge_base_id,),
        ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


class IndexWorker:
    def __init__(
        self,
        settings: Settings,
        embedder: EmbeddingModel,
        generator: AnswerGenerator | None = None,
    ):
        if not settings.database_url:
            raise ValueError("DATABASE_URL is required")
        self.settings = settings
        self.database_url = settings.database_url
        self.embedder = embedder
        self.generator = generator or get_generator()
        # 同步任务要把变化对象交给索引链路，而 IndexWorker 自己不是 DocumentIndexer——
        # 它只调度任务，索引能力在 service 上。reranker 与 generator 不参与索引，传 None。
        self._indexer = PostgresAsyncRAGService(settings, embedder, None, None)

    def recover_stale_jobs(self) -> int:
        """把租约过期的 running 任务放回队列。

        **必须周期性调用，不能只在进程启动时调一次。** worker 被 SIGKILL / OOMKill 时
        locked_at 是刚刚续过的，新进程几秒后起来算出的 cutoff 判不到它；而循环体里
        如果不再调用，这行 running 任务就此没有任何回收路径——两个部分唯一索引
        （index_jobs_one_active_sync_idx、sync_runs_one_active_source_idx）都把它算作
        活动记录，管理员再点「立即同步」永远得到 409，页面一直显示「同步中」。
        见 scripts/index_worker.py 的循环。

        同步任务要连 sync_runs 一起拉回来：index_job 回了队而 sync_runs 仍是 syncing，
        下一次 run_sync 开头的 _ensure_sync_active 读到的仍是活动状态，任务会被
        SYNC_CANCELLED 直接打死，等于没回收。
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.index_job_stale_seconds)
        with psycopg.connect(self.database_url) as connection, connection.transaction():
            stale = connection.execute(
                """UPDATE index_jobs SET status = 'queued', locked_at = NULL, locked_by = NULL,
                          available_at = now(), updated_at = now(),
                          failure_reason = 'stale worker lease recovered'
                   WHERE status = 'running' AND locked_at < %s
                   RETURNING sync_run_id""",
                (cutoff,),
            ).fetchall()
            sync_run_ids = [row[0] for row in stale if row[0]]
            if sync_run_ids:
                connection.execute(
                    """UPDATE sync_runs SET status = 'queued', updated_at = now()
                       WHERE sync_run_id = ANY(%s) AND status = 'syncing'""",
                    (sync_run_ids,),
                )
        return len(stale)

    def run_once(self) -> bool:
        job = self._claim()
        if job is None:
            return False
        try:
            self._process(job)
            if job.get("sync_run_id") and str(job.get("job_type", "index")) == "index":
                update_sync_resource_for_job(
                    self.database_url, str(job["sync_run_id"]), str(job["document_version_id"]),
                    succeeded=True, terminal=True,
                )
            if job.get("rebuild_batch_id") and str(job.get("job_type")) == "rebuild":
                update_index_build_for_job(
                    self.database_url, str(job["rebuild_batch_id"]),
                    str(job["document_version_id"]), succeeded=True, terminal=True,
                )
        except Exception as exc:
            self._fail(str(job["index_job_id"]), str(exc))
        return True

    def _claim(self) -> dict[str, Any] | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                job = connection.execute(
                    """SELECT * FROM index_jobs
                       WHERE status = 'queued' AND available_at <= now()
                       ORDER BY created_at
                       FOR UPDATE SKIP LOCKED LIMIT 1"""
                ).fetchone()
                if job is None:
                    return None
                connection.execute(
                    """UPDATE index_jobs SET status = 'running', attempt_count = attempt_count + 1,
                              locked_at = now(), locked_by = %s, started_at = COALESCE(started_at, now()),
                              updated_at = now()
                       WHERE index_job_id = %s""",
                    (self.settings.index_worker_id, job["index_job_id"]),
                )
        return dict(job)

    def _rebuild_index_version(self, rebuild_batch_id: str) -> str:
        """重建任务的分块归属入队时创建的 building 索引版本。

        用批次反查而不是读当前 building 版本：批次已被放行或清理后，残留任务必须
        明确失败，而不是把分块写进另一个正在构建的版本。
        """

        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT index_version_id FROM index_versions WHERE rebuild_batch_id = %s",
                (rebuild_batch_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"rebuild batch has no index version: {rebuild_batch_id}")
        return str(row[0])

    def _classify(self, version: dict[str, Any]) -> None:
        """跑一次自动分类并把结果写进 documents.metadata。

        只有高置信才写分类归属。可重试的失败会抛 ``_RetryableClassification``，由队列
        按既有的退避与最大次数重来；不可重试的失败原地记录，不占用重试次数——重试多少
        次都是同样的结果，让它把次数耗光只会掩盖「这需要人来处理」。
        """

        classifier = DocumentClassifier(self.generator)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            categories = [
                dict(row)
                for row in connection.execute(
                    """SELECT category_id, name, description, active
                       FROM document_categories WHERE knowledge_base_id=%s""",
                    (version["knowledge_base_id"],),
                ).fetchall()
            ]
        summary = self._classification_summary(version)
        classification = classifier.classify(str(version["filename"]), summary, categories)
        # 只有高置信才写分类归属。其余情况一律保持为空——没有伪分类可以兜底了，
        # 而这正是本次改造的目的：没有分类就诚实地表示成没有分类。
        selected = next(
            (
                item
                for item in categories
                if classification.status == "auto_assigned"
                and item["category_id"] == classification.category_id
            ),
            None,
        )
        failed = classification.status == "failed"
        now = datetime.now(UTC)
        previous = dict(version["document_metadata"] or {})
        retry_count = int(previous.get("classification_retry_count") or 0)
        retrying = failed and classification.retryable
        patch = {
            "category_id": selected["category_id"] if selected else None,
            "category": selected["name"] if selected else None,
            "classification_status": classification.status,
            "classification_confidence": classification.confidence,
            "suggested_category_id": (
                classification.category_id if classification.status == "review_required" else None
            ),
            "classification_model": self.generator.model_name if self.generator else None,
            "classification_reason": classification.reason,
            "classified_at": now.isoformat(),
            "classification_failure_code": classification.failure_code,
            "classification_failure_reason": classification.reason if failed else None,
            "classification_failed_at": now.isoformat() if failed else None,
            # 只有可重试的失败才累加计数，成功则归零。
            "classification_retry_count": retry_count + 1 if retrying else 0,
            "classification_next_retry_at": (
                (now + timedelta(seconds=RETRY_BACKOFF_SECONDS)).isoformat()
                if retrying
                else None
            ),
        }
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """UPDATE documents SET metadata=metadata || %s, updated_at=now()
                   WHERE knowledge_base_id=%s AND document_id=%s""",
                (Jsonb(patch), version["knowledge_base_id"], version["document_id"]),
            )
        version["document_metadata"] = {**previous, **patch}
        if retrying:
            raise _RetryableClassification(
                f"{classification.failure_code}: {classification.reason}"
            )

    def _enqueue_classification_retry(
        self, job: dict[str, Any], version: dict[str, Any], reason: str
    ) -> None:
        """索引流程里分类可重试地失败了，把重试拆成独立的 classify 任务。

        ``data_source_id`` 从当前任务上取：``document_versions`` 没有这一列，而
        ``index_jobs.data_source_id`` 是同步任务与配额统计的依据，留空会让这条重试
        在按数据源筛选的地方消失。
        """

        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """INSERT INTO index_jobs
                   (index_job_id, knowledge_base_id, data_source_id, document_version_id,
                    idempotency_key, status, max_attempts, job_type, available_at,
                    failure_reason)
                   VALUES (%s, %s, %s, %s, %s, 'queued', 3, 'classify',
                           now() + make_interval(secs => %s), %s)
                   ON CONFLICT DO NOTHING""",
                (
                    f"job_{uuid4().hex[:20]}",
                    version["knowledge_base_id"],
                    job.get("data_source_id"),
                    version["document_version_id"],
                    f"classify:{version['document_version_id']}:{uuid4().hex[:8]}",
                    RETRY_BACKOFF_SECONDS,
                    reason[:1000],
                ),
            )

    def _classification_summary(self, version: dict[str, Any]) -> str:
        """给分类器看的摘要。

        重新分类时不重新解析原文——那要重跑解析器、可能几十 MB 的 PDF 也要再读一遍，
        而分类只需要开头几段。直接取当前版本已经切好的分块。
        """

        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(
                """SELECT content FROM chunks
                   WHERE document_version_id = %s ORDER BY chunk_index LIMIT 4""",
                (version["document_version_id"],),
            ).fetchall()
        return "\n".join(str(row[0])[:500] for row in rows)

    def _process_classification(self, job: dict[str, Any]) -> None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            version = connection.execute(
                """SELECT v.*, d.filename, d.metadata AS document_metadata
                   FROM document_versions v
                   JOIN documents d ON d.knowledge_base_id = v.knowledge_base_id
                                   AND d.document_id = v.document_id
                   WHERE v.document_version_id = %s""",
                (job["document_version_id"],),
            ).fetchone()
        if version is None:
            raise RuntimeError("document version not found")
        self._classify(dict(version))

    def _process(self, job: dict[str, Any]) -> None:
        if str(job.get("job_type", "index")) == "classify":
            # 分类任务不碰正文、分块与 Embedding：分类失败是分类的问题，资料主体
            # 已经处理好了，没有理由再付一次索引的代价。
            self._process_classification(job)
            self._succeed(str(job["index_job_id"]))
            return
        if str(job.get("job_type", "index")) == "sync":
            # 同步任务针对整个数据源，没有 document_version_id，不能走下面的版本查询。
            # 传 service 而不是 self：IndexWorker 只负责调度，索引能力在 service 上。
            run_sync(self.settings, job, self._indexer)
            # 必须显式收尾：index 路径是在写入分块的同一事务里置 succeeded 的，
            # 同步走不到那里，不置状态的话任务永远停在 running，
            # index_jobs_one_active_sync_idx 会把后续同步全部挡住。
            self._succeed(str(job["index_job_id"]))
            return
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            version = connection.execute(
                """SELECT v.*, d.filename, d.metadata AS document_metadata,
                          d.created_at AS document_created_at, s.source_type,
                          s.acl AS data_source_acl
                   FROM document_versions v
                   JOIN documents d ON d.knowledge_base_id = v.knowledge_base_id
                                   AND d.document_id = v.document_id
                   JOIN data_sources s ON s.data_source_id = d.data_source_id
                   WHERE v.document_version_id = %s""",
                (job["document_version_id"],),
            ).fetchone()
        if version is None:
            raise RuntimeError("document version not found")
        def mark_stage(stage: str) -> None:
            if str(job.get("job_type")) == "rebuild" and job.get("rebuild_batch_id"):
                update_index_stage(
                    self.database_url, str(job["rebuild_batch_id"]),
                    str(job["document_version_id"]), stage,
                )
            if job.get("operation_id"):
                stage_percent = {
                    "parsing": 35, "chunking": 50, "vector": 70,
                    "keyword": 80, "metadata": 90, "validating": 95,
                }[stage]
                processing_status = "building" if stage in {"vector", "keyword", "metadata"} else stage
                with psycopg.connect(self.database_url) as stage_connection, stage_connection.transaction():
                    stage_connection.execute(
                        "UPDATE document_processing_runs SET status=%s, updated_at=now() WHERE operation_id=%s",
                        (processing_status, job["operation_id"]),
                    )
                    stage_connection.execute(
                        """UPDATE operations SET current_stage=%s, progress_percent=%s,
                                  completed_count=LEAST(total_count, floor(%s * total_count / 100.0)),
                                  updated_at=now() WHERE operation_id=%s""",
                        (stage, stage_percent, stage_percent, job["operation_id"]),
                    )
        # 按入队时冻结的目标配置切分，重建期间修改进程配置不会让同一批次产生混合结果。
        target_version = str(
            job.get("target_chunking_version")
            or chunking_version(self.settings.chunk_size, self.settings.chunk_overlap)
        )
        _, chunk_size, chunk_overlap = parse_chunking_version(target_version)
        content = (self.settings.upload_path / version["source_path"]).read_bytes()
        mark_stage("parsing")
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """UPDATE document_versions SET parse_status='parsing', parse_failure_code=NULL
                   WHERE document_version_id=%s""",
                (version["document_version_id"],),
            )
        parsed = parse_structured_document(str(version["filename"]), content)
        sections = parsed.sections
        # 分类抽成独立方法：重新分类任务要在不重新解析、不重新切分的情况下再跑一次。
        classification_retry: str | None = None
        try:
            self._classify(version)
        except _RetryableClassification as error:
            # 分类抖动不得连累索引。让异常冒到 run_once 的话，整个 index 任务连带重试：
            # 重新解析、重新切分、重新 Embedding 各付一遍，次数耗光后资料被标成 failed
            # 且 parse_failure_code 记成 PARSER_FAILED——解析明明成功了，页面上却显示
            # 解析失败，运维照着这个原因去查解析器永远查不到。分类模型是外部服务，它挂
            # 半小时，这半小时上传的资料就全军覆没。
            # _classify 在抛出前已经把失败码与下次重试时间写进 metadata，这里只记下原因，
            # 到本任务收尾后补一个独立的 classify 任务重试（原因见入队处）。
            classification_retry = str(error)
        mark_stage("chunking")
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "UPDATE document_versions SET parse_status='chunking' WHERE document_version_id=%s",
                (version["document_version_id"],),
            )
        chunks = split_sections(
            str(version["document_id"]),
            str(version["filename"]),
            sections,
            chunk_size,
            chunk_overlap,
            str(version["knowledge_base_id"]),
            {
                **dict(version["document_metadata"] or {}),
                "source_type": str(version["source_type"]),
                "created_at": version["document_created_at"].isoformat(),
                "data_source_acl": dict(version["data_source_acl"] or {}),
                "parser_name": parsed.parser_name,
                "parser_version": parsed.parser_version,
                "chunking_version": target_version,
                "processing_options": {
                    "chunk_size": chunk_size,
                    "chunk_overlap": chunk_overlap,
                    "preserve_heading_context": True,
                    "table_rows_per_chunk": 20,
                },
            },
        )
        mark_stage("vector")
        embeddings = self.embedder.encode([chunk.text for chunk in chunks])
        if embeddings:
            # 在写入分块之前登记/校验，避免污染后才在检索时发现维度冲突。
            register_embedding_model(self.database_url, self.embedder.model_name, len(embeddings[0]))
        # 分块必须归属一个索引版本：读路径按 active 版本过滤，无归属等同于检索不到。
        # 重建任务写入入队时冻结的 building 版本，因此重建期间用户完全看不到这批分块；
        # 普通索引任务写入 active 版本，首次索引时引导创建第一个版本。
        if str(job.get("job_type", "index")) == "rebuild":
            index_version_id = self._rebuild_index_version(str(job["rebuild_batch_id"]))
        else:
            index_version_id = active_or_bootstrap_version(
                self.database_url,
                str(version["knowledge_base_id"]),
                chunking_version=target_version,
                parser_version=parsed.parser_version,
                embedding_model=self.embedder.model_name,
                embedding_dimension=len(embeddings[0]) if embeddings else 1,
                processing_options={"chunk_size": chunk_size, "chunk_overlap": chunk_overlap},
                reranker_model=self.settings.reranker_model,
            )
        now = datetime.now(UTC)
        mark_stage("keyword")
        mark_stage("metadata")
        # 先更新治理状态，再进入写分块事务；避免在持有 chunks/documents 写锁时，
        # 通过第二条连接回写同一构建批次造成锁等待。
        mark_stage("validating")
        with psycopg.connect(self.database_url) as connection:
            register_vector(connection)
            with connection.transaction():
                if str(job.get("job_type", "index")) == "rebuild":
                    build_version = connection.execute(
                        "SELECT status FROM index_versions WHERE index_version_id=%s FOR UPDATE",
                        (index_version_id,),
                    ).fetchone()
                    if build_version is None or str(build_version[0]) != "building":
                        raise RuntimeError("index build cancelled or no longer building")
                if job.get("sync_run_id"):
                    sync_state = connection.execute(
                        "SELECT status FROM sync_runs WHERE sync_run_id=%s FOR UPDATE",
                        (job["sync_run_id"],),
                    ).fetchone()
                    if sync_state is None or str(sync_state[0]) in {"aborted", "failed"}:
                        raise RuntimeError("sync run cancelled")
                # 治理字段以**写入这一刻**库里的值为准，不用任务开头那份快照。
                #
                # 快照是在 _process 最开始读的（`d.metadata AS document_metadata`），
                # 而分块要等解析 + 向量化跑完才写，中间可能几十分钟。这期间管理员完全
                # 可能收紧 ACL 或把资料下架，一次同步也可能把远端已删的对象软删掉。
                # 而软删除/撤权走的是 `UPDATE chunks ... WHERE document_version_id = ...`
                # ——只作用于**已存在**的行，于是在这个窗口里它有两种死法：打不到还没
                # 写入的行，或者打到了也被下面的 INSERT 用旧快照覆盖。两种都会让分块
                # 带着过期的宽松 ACL / searchable 状态上线，而检索侧只看分块
                # （见本文件 retrieve 的判据），不校验 documents——页面上显示已删除、
                # 检索却照样返回原文，且不会自愈：对象已进墓碑，下一次同步不再碰它。
                current = connection.execute(
                    """SELECT metadata FROM documents
                       WHERE knowledge_base_id = %s AND document_id = %s FOR SHARE""",
                    (version["knowledge_base_id"], version["document_id"]),
                ).fetchone()
                if current is not None:
                    live = dict(current[0] or {})
                    for key in ("acl_version", "allow_user_ids", "deny_user_ids", "retrieval_status"):
                        if key in live:
                            for chunk in chunks:
                                chunk.governance_metadata[key] = live[key]
                        else:
                            for chunk in chunks:
                                chunk.governance_metadata.pop(key, None)
                # 只删本索引版本自己的分块（供任务重试幂等），其他版本必须原样保留，
                # 否则回滚无从谈起。V5-4 之前这里是无条件删除同文档版本的全部分块。
                connection.execute(
                    """DELETE FROM chunks
                       WHERE document_version_id = %s AND index_version_id = %s""",
                    (version["document_version_id"], index_version_id),
                )
                for chunk, embedding in zip(chunks, embeddings, strict=True):
                    connection.execute(
                        """INSERT INTO chunks
                           (chunk_id, document_version_id, index_version_id, knowledge_base_id,
                            chunk_index, content, metadata, embedding, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            # 用索引版本而非切分配置做前缀：只改解析器不改切分时，
                            # 两个版本的分块会撞主键。
                            f"{version['document_version_id']}:{index_version_id[3:11]}:{chunk.chunk_index:05d}",
                            version["document_version_id"],
                            index_version_id,
                            version["knowledge_base_id"],
                            chunk.chunk_index,
                            chunk.text,
                            Jsonb(chunk.metadata()),
                            embedding,
                            now,
                        ),
                    )
                if str(job.get("job_type", "index")) == "rebuild":
                    # 重建不参与版本状态机，也不移动当前版本指针。切分与向量配置现在归
                    # 索引版本记录，这里不再回写 chunking_version：回写会让 document_versions
                    # 宣称已是新配置，而新分块其实还在未放行的 building 版本里。
                    connection.execute(
                        """UPDATE document_versions SET parser_name=%s, parser_version=%s,
                                  parsed_content_hash=%s, parse_status='ready',
                                  parse_failure_code=NULL, parsed_tree=%s
                           WHERE document_version_id=%s""",
                        (
                            parsed.parser_name,
                            parsed.parser_version,
                            hashlib.sha256(content).hexdigest(),
                            Jsonb(parsed.tree_payload()),
                            version["document_version_id"],
                        ),
                    )
                else:
                    connection.execute(
                        """UPDATE document_versions SET status = 'superseded'
                           WHERE knowledge_base_id = %s AND document_id = %s AND status = 'ready'""",
                        (version["knowledge_base_id"], version["document_id"]),
                    )
                    connection.execute(
                        """UPDATE document_versions SET status='ready', indexed_at=%s,
                                  failure_reason=NULL, chunking_version=%s, parser_name=%s,
                                  parser_version=%s, processing_options=%s,
                                  parsed_content_hash=%s, parse_status='ready',
                                  parse_failure_code=NULL, parsed_tree=%s
                           WHERE document_version_id=%s""",
                        (
                            now,
                            target_version,
                            parsed.parser_name,
                            parsed.parser_version,
                            Jsonb({"chunk_size": chunk_size, "chunk_overlap": chunk_overlap}),
                            hashlib.sha256(content).hexdigest(),
                            Jsonb(parsed.tree_payload()),
                            version["document_version_id"],
                        ),
                    )
                    connection.execute(
                        """UPDATE documents SET current_version_id = %s, updated_at = %s
                           WHERE knowledge_base_id = %s AND document_id = %s""",
                        (
                            version["document_version_id"],
                            now,
                            version["knowledge_base_id"],
                            version["document_id"],
                        ),
                    )
                connection.execute(
                    """UPDATE index_jobs SET status = 'succeeded', finished_at = %s,
                              locked_at = NULL, locked_by = NULL, updated_at = %s
                       WHERE index_job_id = %s""",
                    (now, now, job["index_job_id"]),
                )
                if job.get("operation_id"):
                    connection.execute(
                        """UPDATE document_processing_runs SET status='succeeded', updated_at=%s
                           WHERE operation_id=%s""",
                        (now, job["operation_id"]),
                    )
                    connection.execute(
                        """UPDATE operations SET status='succeeded', current_stage='complete',
                                  progress_percent=100, completed_count=total_count,
                                  finished_at=%s, updated_at=%s WHERE operation_id=%s""",
                        (now, now, job["operation_id"]),
                    )
        # 分类重试要等本任务落地成 succeeded 之后再入队：index_jobs_one_active_version_idx
        # 限制同一文档版本只能有一个 queued/running 任务，无论 job_type。上面那条
        # UPDATE ... status='succeeded' 就在同一个事务里，所以放在这里才插得进去；
        # 写在 except 分支里会被 ON CONFLICT DO NOTHING 静默吞掉。
        if classification_retry is not None:
            self._enqueue_classification_retry(job, version, classification_retry)

    def _succeed(self, job_id: str) -> None:
        with psycopg.connect(self.database_url) as connection, connection.transaction():
            connection.execute(
                """UPDATE index_jobs SET status = 'succeeded', finished_at = now(),
                          locked_at = NULL, locked_by = NULL, updated_at = now()
                   WHERE index_job_id = %s""",
                (job_id,),
            )

    def _fail(self, job_id: str, reason: str) -> None:
        with psycopg.connect(self.database_url) as connection, connection.transaction():
            job = connection.execute(
                """SELECT attempt_count, max_attempts, document_version_id, job_type, sync_run_id,
                          operation_id, status
                   FROM index_jobs WHERE index_job_id = %s""",
                (job_id,),
            ).fetchone()
            if job is None or str(job[6]) == "cancelled":
                return
            terminal = int(job[0]) >= int(job[1])
            status = "failed" if terminal else "queued"
            connection.execute(
                """UPDATE index_jobs SET status = %s, failure_reason = %s,
                          available_at = now() + make_interval(secs => %s), locked_at = NULL,
                          locked_by = NULL, finished_at = CASE WHEN %s THEN now() ELSE NULL END,
                          updated_at = now() WHERE index_job_id = %s""",
                (status, reason[:1000], RETRY_BACKOFF_SECONDS, terminal, job_id),
            )
            rebuild_batch_id = None
            if str(job[3]) == "rebuild":
                batch = connection.execute(
                    "SELECT rebuild_batch_id FROM index_jobs WHERE index_job_id=%s", (job_id,)
                ).fetchone()
                rebuild_batch_id = str(batch[0]) if batch and batch[0] else None
            if str(job[3]) in {"rebuild", "classify"}:
                # 重建失败时上一批 chunks 仍然完好；分类失败更是与正文无关——两者都
                # 不得把文档版本标成 failed，文档必须保持可检索。
                pass
            else:
                connection.execute(
                    """UPDATE document_versions SET status=%s, failure_reason=%s,
                              parse_status=%s, parse_failure_code=%s
                       WHERE document_version_id=%s""",
                    (
                        "failed" if terminal else "pending",
                        reason[:1000],
                        "failed" if terminal else "pending",
                        "PARSER_FAILED" if terminal else None,
                        job[2],
                    ),
                )
            if job[5]:
                connection.execute(
                    # failure_stage 固定为 'build'：走到这里就是索引构建失败，与
                    # failure_code 同源。此前它读 operations.current_stage——那是本表的
                    # 进度投影，且紧接着的下一条语句就会把它改写成 retry_wait，于是
                    # 同一次失败记下的阶段取决于两条语句的先后，而不是失败本身。
                    """UPDATE document_processing_runs SET status=%s, failure_stage='build',
                              failure_code='INDEX_BUILD_FAILED', failure_reason=%s, updated_at=now()
                       WHERE operation_id=%s""",
                    ("failed" if terminal else "building", reason[:1000], job[5]),
                )
                connection.execute(
                    """UPDATE operations SET status=%s,
                              current_stage=CASE WHEN %s THEN current_stage ELSE 'retry_wait' END,
                              error_code='INDEX_BUILD_FAILED', error_message=%s,
                              finished_at=CASE WHEN %s THEN now() ELSE NULL END, updated_at=now()
                       WHERE operation_id=%s""",
                    ("failed" if terminal else "running", terminal, reason[:1000], terminal, job[5]),
                )
        if job[4] and str(job[3]) == "index":
            update_sync_resource_for_job(
                self.database_url, str(job[4]), str(job[2]), succeeded=False,
                terminal=terminal, failure_reason=reason[:1000],
            )
        if str(job[3]) == "rebuild" and rebuild_batch_id:
            update_index_build_for_job(
                self.database_url, rebuild_batch_id, str(job[2]), succeeded=False,
                terminal=terminal, failure_reason=reason[:1000],
            )
