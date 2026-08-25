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
from .errors import AppError
from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID, validate_knowledge_base_id
from .models import EmbeddingModel, GeminiGenerator, Reranker
from .parsers import parse_document
from .ranking import fuse_retrieval_candidates
from .schemas import DocumentInfo
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

    def query(
        self,
        embedding: list[float],
        limit: int,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        query_text: str | None = None,
    ) -> list[RetrievedChunk]:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            register_vector(connection)
            vector_rows = connection.execute(
                """SELECT c.chunk_id, c.content, c.metadata,
                          1 - (c.embedding <=> %s::vector) AS retrieval_score
                   FROM chunks c
                   JOIN documents d
                     ON d.knowledge_base_id = c.knowledge_base_id
                    AND d.document_id = (c.metadata->>'document_id')
                    AND d.current_version_id = c.document_version_id
                   JOIN document_versions v
                     ON v.document_version_id = c.document_version_id
                    AND v.status = 'ready'
                   WHERE c.knowledge_base_id = %s
                   ORDER BY c.embedding <=> %s::vector
                   LIMIT %s""",
                (embedding, knowledge_base_id, embedding, limit),
            ).fetchall()
            lexical_rows = connection.execute(
                """SELECT c.chunk_id, c.content, c.metadata,
                          GREATEST(
                              similarity(lower(c.content), lower(%s)),
                              word_similarity(lower(%s), lower(c.content))
                          ) AS retrieval_score
                   FROM chunks c
                   JOIN documents d
                     ON d.knowledge_base_id = c.knowledge_base_id
                    AND d.document_id = (c.metadata->>'document_id')
                    AND d.current_version_id = c.document_version_id
                   JOIN document_versions v
                     ON v.document_version_id = c.document_version_id
                    AND v.status = 'ready'
                   WHERE c.knowledge_base_id = %s
                     AND GREATEST(
                         similarity(lower(c.content), lower(%s)),
                         word_similarity(lower(%s), lower(c.content))
                     ) > 0
                   ORDER BY retrieval_score DESC, c.chunk_id
                   LIMIT %s""",
                (query_text, query_text, knowledge_base_id, query_text, query_text, limit),
            ).fetchall() if query_text else []
        vectors = [
            RetrievedChunk(
                chunk_id=str(row["chunk_id"]),
                text=str(row["content"]),
                metadata=dict(row["metadata"]),
                retrieval_score=round(float(row["retrieval_score"]), 6),
                vector_score=round(float(row["retrieval_score"]), 6),
                retrieval_methods=["vector"],
            )
            for row in vector_rows
        ]
        lexical = [
            RetrievedChunk(
                chunk_id=str(row["chunk_id"]),
                text=str(row["content"]),
                metadata=dict(row["metadata"]),
                retrieval_score=round(float(row["retrieval_score"]), 6),
                lexical_score=round(float(row["retrieval_score"]), 6),
                retrieval_methods=["lexical"],
            )
            for row in lexical_rows
        ]
        return fuse_retrieval_candidates(vectors, lexical, limit)

    def list_documents(self, knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID) -> list[dict[str, Any]]:
        validate_knowledge_base_id(knowledge_base_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT d.document_id, d.filename, d.current_version_id,
                          current_version.status AS current_status,
                          count(c.chunk_id) AS chunk_count,
                          pending.status AS pending_status
                   FROM documents d
                   LEFT JOIN document_versions current_version
                     ON current_version.document_version_id = d.current_version_id
                   LEFT JOIN chunks c ON c.document_version_id = d.current_version_id
                   LEFT JOIN LATERAL (
                       SELECT dv.status FROM document_versions dv
                       WHERE dv.knowledge_base_id = d.knowledge_base_id
                         AND dv.document_id = d.document_id
                         AND dv.status IN ('pending', 'indexing', 'failed')
                       ORDER BY dv.version_number DESC LIMIT 1
                   ) pending ON true
                   WHERE d.knowledge_base_id = %s
                   GROUP BY d.document_id, d.filename, d.current_version_id,
                            current_version.status, pending.status
                   ORDER BY lower(d.filename)""",
                (knowledge_base_id,),
            ).fetchall()
        return [
            {
                "knowledge_base_id": knowledge_base_id,
                "document_id": row["document_id"],
                "filename": row["filename"],
                "chunk_count": int(row["chunk_count"]),
                "status": row["pending_status"] or row["current_status"] or "pending",
            }
            for row in rows
        ]

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
    stores_source_files = True

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
        super().__init__(
            settings,
            PostgresVectorStore(settings.database_url, settings.upload_path),
            embedder,
            reranker,
            generator,
        )

    def index_document(
        self,
        filename: str,
        content: bytes,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> DocumentInfo:
        validate_knowledge_base_id(knowledge_base_id)
        safe_name = Path(filename).name
        content_hash = hashlib.sha256(content).hexdigest()
        document_id = _stable_id("doc", knowledge_base_id, safe_name.casefold())
        source_id = _stable_id("src", knowledge_base_id, safe_name.casefold())
        now = datetime.now(UTC)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                if not connection.execute(
                    "SELECT 1 FROM knowledge_bases WHERE knowledge_base_id = %s",
                    (knowledge_base_id,),
                ).fetchone():
                    raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404)
                migrated_identity = connection.execute(
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
                connection.execute(
                    """INSERT INTO data_sources
                       (data_source_id, knowledge_base_id, source_type, name, configuration,
                        created_at, updated_at)
                       VALUES (%s, %s, 'file', %s, '{}'::jsonb, %s, %s)
                       ON CONFLICT (data_source_id) DO UPDATE SET updated_at = EXCLUDED.updated_at""",
                    (source_id, knowledge_base_id, safe_name, now, now),
                )
                connection.execute(
                    """INSERT INTO documents
                       (document_id, knowledge_base_id, data_source_id, filename, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (knowledge_base_id, document_id)
                       DO UPDATE SET filename = EXCLUDED.filename, updated_at = EXCLUDED.updated_at""",
                    (document_id, knowledge_base_id, source_id, safe_name, now, now),
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


def enqueue_rebuild(
    database_url: str,
    knowledge_base_id: str,
    target_chunking_version: str,
    max_attempts: int = 3,
) -> dict[str, object]:
    """把知识库中尚未使用目标切分配置的当前版本批量排队重建。

    只覆盖 ``current_version_id`` 指向的版本：历史版本不参与检索，重切它们没有收益。
    已有排队或运行中任务的版本会被跳过，重复调用因此是安全的，中断后再次调用即可续跑。
    """

    validate_knowledge_base_id(knowledge_base_id)
    parse_chunking_version(target_chunking_version)
    batch_id = f"rbd_{uuid4().hex[:16]}"
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            if not connection.execute(
                "SELECT 1 FROM knowledge_bases WHERE knowledge_base_id = %s",
                (knowledge_base_id,),
            ).fetchone():
                raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404)
            candidates = connection.execute(
                """SELECT v.document_version_id, d.data_source_id
                   FROM documents d
                   JOIN document_versions v ON v.document_version_id = d.current_version_id
                   WHERE d.knowledge_base_id = %s
                     AND v.chunking_version IS DISTINCT FROM %s
                     AND NOT EXISTS (
                         SELECT 1 FROM index_jobs j
                         WHERE j.document_version_id = v.document_version_id
                           AND j.status IN ('queued', 'running'))
                   ORDER BY d.document_id""",
                (knowledge_base_id, target_chunking_version),
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
        "knowledge_base_id": knowledge_base_id,
        "target_chunking_version": target_chunking_version,
        "queued": queued,
    }


def rebuild_status(database_url: str, batch_id: str) -> dict[str, object]:
    """汇总一个重建批次的任务状态，用于判断是否已经跑完或需要续跑。"""

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
        "counts": counts,
        "pending": counts.get("queued", 0) + counts.get("running", 0),
        "failures": [dict(row) for row in failures],
    }


def chunking_inventory(database_url: str, knowledge_base_id: str) -> dict[str, int]:
    """按切分配置统计知识库当前版本的分布，用于验证重建是否真正收敛。"""

    validate_knowledge_base_id(knowledge_base_id)
    with psycopg.connect(database_url) as connection:
        rows = connection.execute(
            """SELECT COALESCE(v.chunking_version, ''), count(*)
               FROM documents d
               JOIN document_versions v ON v.document_version_id = d.current_version_id
               WHERE d.knowledge_base_id = %s
               GROUP BY 1
               ORDER BY 1""",
            (knowledge_base_id,),
        ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


class IndexWorker:
    def __init__(self, settings: Settings, embedder: EmbeddingModel):
        if not settings.database_url:
            raise ValueError("DATABASE_URL is required")
        self.settings = settings
        self.database_url = settings.database_url
        self.embedder = embedder

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

    def _process(self, job: dict[str, Any]) -> None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            version = connection.execute(
                """SELECT v.*, d.filename FROM document_versions v
                   JOIN documents d ON d.knowledge_base_id = v.knowledge_base_id
                                   AND d.document_id = v.document_id
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
        sections = parse_document(str(version["filename"]), content)
        chunks = split_sections(
            str(version["document_id"]),
            str(version["filename"]),
            sections,
            chunk_size,
            chunk_overlap,
            str(version["knowledge_base_id"]),
        )
        embeddings = self.embedder.encode([chunk.text for chunk in chunks])
        now = datetime.now(UTC)
        with psycopg.connect(self.database_url) as connection:
            register_vector(connection)
            with connection.transaction():
                connection.execute(
                    "DELETE FROM chunks WHERE document_version_id = %s",
                    (version["document_version_id"],),
                )
                for chunk, embedding in zip(chunks, embeddings, strict=True):
                    connection.execute(
                        """INSERT INTO chunks
                           (chunk_id, document_version_id, knowledge_base_id, chunk_index,
                            content, metadata, embedding, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            f"{version['document_version_id']}:{chunk.chunk_index:05d}",
                            version["document_version_id"],
                            version["knowledge_base_id"],
                            chunk.chunk_index,
                            chunk.text,
                            Jsonb(chunk.metadata()),
                            embedding,
                            now,
                        ),
                    )
                if str(job.get("job_type", "index")) == "rebuild":
                    # 重建只替换同一版本的 chunks，不参与版本状态机，也不移动当前版本指针。
                    connection.execute(
                        "UPDATE document_versions SET chunking_version = %s WHERE document_version_id = %s",
                        (target_version, version["document_version_id"]),
                    )
                else:
                    connection.execute(
                        """UPDATE document_versions SET status = 'superseded'
                           WHERE knowledge_base_id = %s AND document_id = %s AND status = 'ready'""",
                        (version["knowledge_base_id"], version["document_id"]),
                    )
                    connection.execute(
                        """UPDATE document_versions SET status = 'ready', indexed_at = %s,
                                  failure_reason = NULL, chunking_version = %s
                           WHERE document_version_id = %s""",
                        (now, target_version, version["document_version_id"]),
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
                """UPDATE document_versions SET status = %s, failure_reason = %s
                   WHERE document_version_id = %s""",
                ("failed" if terminal else "pending", reason[:1000], job[2]),
            )
