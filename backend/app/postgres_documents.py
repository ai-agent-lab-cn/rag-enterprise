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
from .document_classifier import DocumentClassifier
from .errors import AppError
from .index_versions import (
    active_index_version_id,
    active_or_bootstrap_version,
    create_building_version,
    finalize_building_version,
    list_versions,
)
from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID, validate_knowledge_base_id
from .lexical import LexicalIndexCache
from .models import EmbeddingModel, GeminiGenerator, Reranker, get_generator
from .parsers import parse_structured_document
from .retrieval_access import RetrievalAccessContext
from .schemas import DocumentInfo, QueryMetadataFilter
from .security import write_private_file
from .service import RAGService
from .store import RetrievedChunk


def _stable_id(prefix: str, *parts: str) -> str:
    value = hashlib.sha256("\0".join(parts).encode()).hexdigest()[:20]
    return f"{prefix}_{value}"


class PostgresVectorStore:
    def __init__(self, database_url: str, upload_root: Path):
        self.database_url = database_url
        self.upload_root = upload_root

    def _active_index_version(self, knowledge_base_id: str) -> str | None:
        """当前生效的索引版本；读路径全部以它为界，未放行的版本对用户不存在。

        返回 None 而不是抛错：尚未索引过的知识库本就没有可检索内容，让 SQL 的
        ``= NULL`` 自然匹配不到，空知识库、处理中、无权限等状态仍由 service 层区分。
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
    ) -> list[RetrievedChunk]:
        validate_knowledge_base_id(knowledge_base_id)
        clauses = ["c.knowledge_base_id = %s", "c.index_version_id = %s"]
        parameters: list[Any] = [knowledge_base_id, self._active_index_version(knowledge_base_id)]
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
                f"""SELECT c.chunk_id, c.content, c.metadata,
                          1 - (c.embedding <=> %s::vector) AS retrieval_score
                   FROM chunks c
                   JOIN documents d
                     ON d.knowledge_base_id = c.knowledge_base_id
                    AND d.document_id = (c.metadata->>'document_id')
                    AND d.current_version_id = c.document_version_id
                   JOIN data_sources s ON s.data_source_id = d.data_source_id
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
    ) -> list[RetrievedChunk]:
        """读回当前版本的全部分块，供词法索引构建与融合阶段复原候选。

        过滤条件与 ``query`` 完全一致，两路召回因此始终看到同一批分块；
        ``retrieval_score`` 留 0，由调用方在需要时补算。
        """

        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            access_sql = ""
            parameters: list[Any] = [knowledge_base_id, self._active_index_version(knowledge_base_id)]
            if access:
                access_sql = """AND NOT (COALESCE(c.metadata->'deny_user_ids', '[]'::jsonb) ? %s)
                    AND (jsonb_array_length(COALESCE(c.metadata->'allow_user_ids', '[]'::jsonb)) = 0
                         OR COALESCE(c.metadata->'allow_user_ids', '[]'::jsonb) ? %s)
                    AND NOT (COALESCE(s.acl->'deny_user_ids', '[]'::jsonb) ? %s)
                    AND (jsonb_array_length(COALESCE(s.acl->'allow_user_ids', '[]'::jsonb)) = 0
                         OR COALESCE(s.acl->'allow_user_ids', '[]'::jsonb) ? %s)"""
                parameters.extend([access.user_id] * 4)
            rows = connection.execute(
                f"""SELECT c.chunk_id, c.content, c.metadata
                   FROM chunks c
                   JOIN documents d
                     ON d.knowledge_base_id = c.knowledge_base_id
                    AND d.document_id = (c.metadata->>'document_id')
                    AND d.current_version_id = c.document_version_id
                   JOIN data_sources s ON s.data_source_id = d.data_source_id
                   WHERE c.knowledge_base_id = %s
                     AND c.index_version_id = %s
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

    def chunk_fingerprint(self, knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID) -> str:
        """当前版本分块集合的廉价指纹，用于跨进程判断词法索引是否已经过期。

        新增与删除会改变计数，索引重建会改变最新写入时间；ACL 版本变化也会立即失效缓存。
        active 索引版本必须计入：切换索引版本时分块集合本身不变，只有指针动了，
        指纹若不含它，API 进程会继续用旧版本的倒排，混合检索会命中已被切走的分块。
        """

        validate_knowledge_base_id(knowledge_base_id)
        active = self._active_index_version(knowledge_base_id)
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                """SELECT count(*), COALESCE(max(c.created_at), to_timestamp(0)),
                          COALESCE(max((c.metadata->>'acl_version')::integer), 1),
                          COALESCE(max((c.metadata->'data_source_acl'->>'version')::integer), 1),
                          COALESCE(max(c.metadata->>'classified_at'), '')
                   FROM chunks c
                   JOIN documents d
                     ON d.knowledge_base_id = c.knowledge_base_id
                    AND d.document_id = (c.metadata->>'document_id')
                    AND d.current_version_id = c.document_version_id
                   WHERE c.knowledge_base_id = %s AND c.index_version_id = %s""",
                (knowledge_base_id, active),
            ).fetchone()
        return f"{active}:{int(row[0])}:{row[1].isoformat()}:{int(row[2])}:{int(row[3])}:{row[4]}"

    def score_by_ids(
        self,
        chunk_ids: list[str],
        embedding: list[float],
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
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
                (embedding, knowledge_base_id, self._active_index_version(knowledge_base_id), chunk_ids),
            ).fetchall()
        return {str(row[0]): round(float(row[1]), 6) for row in rows}

    def list_documents(self, knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID) -> list[dict[str, Any]]:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT d.document_id, d.filename, d.current_version_id, d.metadata,
                          d.created_at, s.source_type,
                          current_version.status AS current_status,
                          count(c.chunk_id) AS chunk_count,
                          pending.status AS pending_status
                   FROM documents d
                   JOIN data_sources s ON s.data_source_id = d.data_source_id
                   LEFT JOIN document_versions current_version
                     ON current_version.document_version_id = d.current_version_id
                   -- 只数 active 索引版本的分块：重建期间两个版本并存，不过滤会让分块数翻倍。
                   LEFT JOIN chunks c ON c.document_version_id = d.current_version_id
                                     AND c.index_version_id = %s
                   LEFT JOIN LATERAL (
                       SELECT dv.status FROM document_versions dv
                       WHERE dv.knowledge_base_id = d.knowledge_base_id
                         AND dv.document_id = d.document_id
                         AND dv.status IN ('pending', 'indexing', 'failed')
                       ORDER BY dv.version_number DESC LIMIT 1
                   ) pending ON true
                   WHERE d.knowledge_base_id = %s
                   GROUP BY d.document_id, d.filename, d.current_version_id, d.metadata,
                            d.created_at, s.source_type, current_version.status, pending.status
                   ORDER BY lower(d.filename)""",
                (self._active_index_version(knowledge_base_id), knowledge_base_id),
            ).fetchall()
        return [
            {
                "knowledge_base_id": knowledge_base_id,
                "document_id": row["document_id"],
                "filename": row["filename"],
                "chunk_count": int(row["chunk_count"]),
                "status": row["pending_status"] or row["current_status"] or "pending",
                "category": dict(row["metadata"] or {}).get("category", "未分类"),
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
                "acl_version": dict(row["metadata"] or {}).get("acl_version", 1),
                "allow_user_ids": dict(row["metadata"] or {}).get("allow_user_ids", []),
                "deny_user_ids": dict(row["metadata"] or {}).get("deny_user_ids", []),
                "classification_status": dict(row["metadata"] or {}).get("classification_status", "pending"),
                "classification_confidence": dict(row["metadata"] or {}).get("classification_confidence"),
                "suggested_category_id": dict(row["metadata"] or {}).get("suggested_category_id"),
                "classification_model": dict(row["metadata"] or {}).get("classification_model"),
                "classified_at": dict(row["metadata"] or {}).get("classified_at"),
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
            connection.execute("DELETE FROM data_sources WHERE data_source_id = %s", (source[0],))
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
        generator: GeminiGenerator,
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
                lambda knowledge_base_id: [
                    (item.chunk_id, item.text) for item in store.load_current_chunks(knowledge_base_id)
                ],
                store.chunk_fingerprint,
            ),
        )

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
                existing = connection.execute(
                    """SELECT dv.document_version_id, dv.status,
                              COALESCE((SELECT count(*) FROM chunks c
                                        WHERE c.document_version_id = dv.document_version_id), 0) chunks
                       FROM document_versions dv
                       WHERE dv.knowledge_base_id = %s AND dv.document_id = %s
                         AND dv.content_sha256 = %s""",
                    (knowledge_base_id, document_id, content_hash),
                ).fetchone()
                if existing:
                    return DocumentInfo(
                        knowledge_base_id=knowledge_base_id,
                        document_id=document_id,
                        filename=safe_name,
                        chunk_count=int(existing["chunks"]),
                        status=str(existing["status"]),
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
                       DO UPDATE SET filename = EXCLUDED.filename, metadata = EXCLUDED.metadata,
                                     updated_at = EXCLUDED.updated_at""",
                    (document_id, knowledge_base_id, source_id, safe_name, Jsonb(metadata or {}), now, now),
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
                        content_sha256, source_file_bytes, source_path, status, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)""",
                    (
                        version_id,
                        knowledge_base_id,
                        document_id,
                        version_number,
                        content_hash,
                        len(content),
                        relative_path,
                        now,
                    ),
                )
                connection.execute(
                    """INSERT INTO index_jobs
                       (index_job_id, knowledge_base_id, data_source_id, document_version_id,
                        idempotency_key, status, max_attempts, job_type, target_chunking_version)
                       VALUES (%s, %s, %s, %s, %s, 'queued', %s, 'index', %s)""",
                    (
                        f"job_{uuid4().hex[:20]}",
                        knowledge_base_id,
                        source_id,
                        version_id,
                        f"index:{version_id}",
                        self.settings.index_job_max_attempts,
                        chunking_version(self.settings.chunk_size, self.settings.chunk_overlap),
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
        index_version_id = create_building_version(
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
            candidates = connection.execute(
                """SELECT v.document_version_id, d.data_source_id
                   FROM documents d
                   JOIN document_versions v ON v.document_version_id = d.current_version_id
                   WHERE d.knowledge_base_id = %s
                     AND NOT EXISTS (
                         SELECT 1 FROM chunks c
                         WHERE c.document_version_id = v.document_version_id
                           AND c.index_version_id = %s)
                     AND NOT EXISTS (
                         SELECT 1 FROM index_jobs j
                         WHERE j.document_version_id = v.document_version_id
                           AND j.status IN ('queued', 'running'))
                   ORDER BY d.document_id""",
                (knowledge_base_id, index_version_id),
            ).fetchall()
            queued = 0
            for candidate in candidates:
                result = connection.execute(
                    """INSERT INTO index_jobs
                       (index_job_id, knowledge_base_id, data_source_id, document_version_id,
                        idempotency_key, status, max_attempts, job_type, rebuild_batch_id,
                        target_chunking_version)
                       VALUES (%s, %s, %s, %s, %s, 'queued', %s, 'rebuild', %s, %s)
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
                    ),
                )
                queued += result.rowcount
    return {
        "batch_id": batch_id,
        "index_version_id": index_version_id,
        "knowledge_base_id": knowledge_base_id,
        "target_chunking_version": target_chunking_version,
        "queued": queued,
    }


def rebuild_status(database_url: str, batch_id: str) -> dict[str, object]:
    """汇总一个重建批次的任务状态，并顺带推进索引版本状态机。

    状态查询是操作者唯一会反复执行的命令，把 building → ready / failed 的判定挂在
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
                 AND iv.status IN ('active', 'building', 'ready', 'previous')
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
        generator: GeminiGenerator | None = None,
    ):
        if not settings.database_url:
            raise ValueError("DATABASE_URL is required")
        self.settings = settings
        self.database_url = settings.database_url
        self.embedder = embedder
        self.generator = generator or get_generator()

    def recover_stale_jobs(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.index_job_stale_seconds)
        with psycopg.connect(self.database_url) as connection, connection.transaction():
            result = connection.execute(
                """UPDATE index_jobs SET status = 'queued', locked_at = NULL, locked_by = NULL,
                          available_at = now(), updated_at = now(),
                          failure_reason = 'stale worker lease recovered'
                   WHERE status = 'running' AND locked_at < %s""",
                (cutoff,),
            )
        return result.rowcount

    def run_once(self) -> bool:
        job = self._claim()
        if job is None:
            return False
        try:
            self._process(job)
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

    def _process(self, job: dict[str, Any]) -> None:
        if str(job.get("job_type", "index")) == "sync":
            # 同步任务针对整个数据源，没有 document_version_id，不能走下面的版本查询。
            from .data_source_sync import run_sync

            run_sync(self.settings, self.embedder, job)
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
        # 按入队时冻结的目标配置切分，重建期间修改进程配置不会让同一批次产生混合结果。
        target_version = str(
            job.get("target_chunking_version")
            or chunking_version(self.settings.chunk_size, self.settings.chunk_overlap)
        )
        _, chunk_size, chunk_overlap = parse_chunking_version(target_version)
        content = (self.settings.upload_path / version["source_path"]).read_bytes()
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """UPDATE document_versions SET parse_status='parsing', parse_failure_code=NULL
                   WHERE document_version_id=%s""",
                (version["document_version_id"],),
            )
        parsed = parse_structured_document(str(version["filename"]), content)
        sections = parsed.sections
        classifier = DocumentClassifier(self.generator)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            categories = [
                dict(row)
                for row in connection.execute(
                    """SELECT category_id, name, description, active, is_system
                       FROM document_categories WHERE knowledge_base_id=%s""",
                    (version["knowledge_base_id"],),
                ).fetchall()
            ]
            uncategorized = next((item for item in categories if item["is_system"]), None)
        summary = "\n".join(section.text[:500] for section in sections[:4])
        classification = classifier.classify(str(version["filename"]), summary, categories)
        selected = next(
            (
                item
                for item in categories
                if classification.status == "auto_assigned"
                and item["category_id"] == classification.category_id
            ),
            uncategorized,
        )
        classification_patch = {
            "category_id": selected["category_id"] if selected else None,
            "category": selected["name"] if selected else "未分类",
            "classification_status": classification.status,
            "classification_confidence": classification.confidence,
            "suggested_category_id": (
                classification.category_id if classification.status == "review_required" else None
            ),
            "classification_model": self.generator.model_name,
            "classification_reason": classification.reason,
            "classified_at": datetime.now(UTC).isoformat(),
        }
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """UPDATE documents SET metadata=metadata || %s, updated_at=now()
                   WHERE knowledge_base_id=%s AND document_id=%s""",
                (Jsonb(classification_patch), version["knowledge_base_id"], version["document_id"]),
            )
        version["document_metadata"] = {
            **dict(version["document_metadata"] or {}),
            **classification_patch,
        }
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
            )
        now = datetime.now(UTC)
        with psycopg.connect(self.database_url) as connection:
            register_vector(connection)
            with connection.transaction():
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
                """SELECT attempt_count, max_attempts, document_version_id, job_type
                   FROM index_jobs WHERE index_job_id = %s""",
                (job_id,),
            ).fetchone()
            terminal = int(job[0]) >= int(job[1])
            status = "failed" if terminal else "queued"
            connection.execute(
                """UPDATE index_jobs SET status = %s, failure_reason = %s,
                          available_at = now() + interval '5 seconds', locked_at = NULL,
                          locked_by = NULL, finished_at = CASE WHEN %s THEN now() ELSE NULL END,
                          updated_at = now() WHERE index_job_id = %s""",
                (status, reason[:1000], terminal, job_id),
            )
            if str(job[3]) == "rebuild":
                # 重建失败时上一批 chunks 仍然完好，文档必须保持可检索，只由任务记录失败。
                return
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
