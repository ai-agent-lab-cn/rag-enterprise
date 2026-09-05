"""V5 操作、同步资源与进度聚合。

批次状态只由持久化的单资源状态计算，禁止由调用链是否返回来猜测完成。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


TERMINAL_RESOURCE_STATUSES = frozenset(
    {"succeeded", "unchanged", "skipped", "deleted", "failed", "dead_letter", "cancelled"}
)


def create_operation(
    connection: psycopg.Connection[Any], *, operation_type: str, knowledge_base_id: str,
    idempotency_key: str, data_source_id: str | None = None,
    document_id: str | None = None, document_version_id: str | None = None,
    progress_mode: str = "resources",
) -> str:
    operation_id = f"op_{uuid4().hex[:20]}"
    connection.execute(
        """INSERT INTO operations
           (operation_id, operation_type, knowledge_base_id, data_source_id, document_id,
            document_version_id, idempotency_key, progress_mode)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (operation_id, operation_type, knowledge_base_id, data_source_id, document_id,
         document_version_id, idempotency_key, progress_mode),
    )
    return operation_id


def upsert_sync_resource(
    database_url: str, sync_run_id: str, external_resource_id: str, operation: str,
    *, status: str = "discovered", stage: str = "discover", document_id: str | None = None,
    document_version_id: str | None = None, error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO sync_resource_runs
               (sync_resource_run_id, sync_run_id, external_resource_id, operation, status,
                current_stage, document_id, document_version_id, error_code, error_message,
                started_at, finished_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       CASE WHEN %s <> 'discovered' THEN now() END,
                       CASE WHEN %s = ANY(%s) THEN now() END)
               ON CONFLICT (sync_run_id, external_resource_id) DO UPDATE SET
                 operation=EXCLUDED.operation, status=EXCLUDED.status,
                 current_stage=EXCLUDED.current_stage,
                 document_id=COALESCE(EXCLUDED.document_id, sync_resource_runs.document_id),
                 document_version_id=COALESCE(EXCLUDED.document_version_id,
                                              sync_resource_runs.document_version_id),
                 error_code=EXCLUDED.error_code, error_message=EXCLUDED.error_message,
                 started_at=COALESCE(sync_resource_runs.started_at, EXCLUDED.started_at),
                 finished_at=EXCLUDED.finished_at, updated_at=now()""",
            (f"srr_{uuid4().hex[:20]}", sync_run_id, external_resource_id, operation, status,
             stage, document_id, document_version_id, error_code, error_message, status, status,
             list(TERMINAL_RESOURCE_STATUSES)),
        )


def update_sync_resource_for_job(
    database_url: str, sync_run_id: str, document_version_id: str,
    *, succeeded: bool, terminal: bool, failure_reason: str | None = None,
) -> None:
    status = "succeeded" if succeeded else ("dead_letter" if terminal else "building")
    stage = "complete" if succeeded else ("dead_letter" if terminal else "retry_wait")
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE sync_resource_runs SET status=%s, current_stage=%s,
                      attempt_count=attempt_count+CASE WHEN %s THEN 0 ELSE 1 END,
                      error_code=CASE WHEN %s THEN NULL ELSE 'INDEX_BUILD_FAILED' END,
                      error_message=%s,
                      finished_at=CASE WHEN %s OR %s THEN now() ELSE NULL END,
                      updated_at=now()
               WHERE sync_run_id=%s AND document_version_id=%s AND status <> 'cancelled'""",
            (status, stage, succeeded, succeeded, failure_reason, succeeded, terminal,
             sync_run_id, document_version_id),
        )
    aggregate_sync_run(database_url, sync_run_id)


def aggregate_sync_run(database_url: str, sync_run_id: str) -> None:
    """按单资源真实状态收口同步批次，并在完全结束后提交 cursor。"""
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        run = connection.execute(
            "SELECT operation_id, data_source_id, discovered_cursor FROM sync_runs WHERE sync_run_id=%s FOR UPDATE",
            (sync_run_id,),
        ).fetchone()
        if run is None:
            return
        # 取消/熔断是人为确定的终态。迟到的 Worker 只能完成自身清理，不能把批次改回成功。
        terminal = connection.execute(
            "SELECT status FROM sync_runs WHERE sync_run_id=%s", (sync_run_id,)
        ).fetchone()
        if terminal and str(terminal[0]) in {"aborted", "failed"}:
            return
        counts = connection.execute(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE status = ANY(%s)) AS completed,
                      count(*) FILTER (WHERE status NOT IN ('discovered','succeeded','unchanged','skipped','deleted','failed','dead_letter','cancelled')) AS processing,
                      count(*) FILTER (WHERE status IN ('failed','dead_letter')) AS failed,
                      count(*) FILTER (WHERE status='dead_letter') AS dead_letter
               FROM sync_resource_runs WHERE sync_run_id=%s""",
            (list(TERMINAL_RESOURCE_STATUSES), sync_run_id),
        ).fetchone()
        total, completed = int(counts["total"]), int(counts["completed"])
        processing, failed = int(counts["processing"]), int(counts["failed"])
        done = total == completed
        status = ("partial_failed" if failed else "succeeded") if done else "indexing"
        stage = "complete_with_failures" if done and failed else ("complete" if done else "build")
        connection.execute(
            """UPDATE sync_runs SET status=%s, stage=%s, total_count=%s,
                      completed_count=%s, processing_count=%s, failed_count=%s,
                      dead_letter_count=%s,
                      committed_cursor=CASE WHEN %s AND %s=0 THEN discovered_cursor ELSE committed_cursor END,
                      next_cursor=CASE WHEN %s AND %s=0 THEN discovered_cursor ELSE next_cursor END,
                      finished_at=CASE WHEN %s THEN now() ELSE NULL END, updated_at=now()
               WHERE sync_run_id=%s""",
            (status, stage, total, completed, processing, failed, int(counts["dead_letter"]),
             done, failed, done, failed, done, sync_run_id),
        )
        percent = 100 if done else (round(completed * 100 / total, 2) if total else None)
        source_status = "failed" if status == "partial_failed" else status
        connection.execute(
            """UPDATE operations SET status=%s, current_stage=%s, total_count=%s,
                      completed_count=%s, processing_count=%s, failed_count=%s,
                      progress_percent=%s, started_at=COALESCE(started_at, now()),
                      finished_at=CASE WHEN %s THEN now() ELSE NULL END, updated_at=now()
               WHERE operation_id=%s""",
            (status, stage, total, completed, processing, failed, percent, done, run["operation_id"]),
        )
        connection.execute(
            """UPDATE data_sources SET last_sync_status=%s,
                      sync_failure_reason=CASE WHEN %s THEN %s ELSE NULL END,
                      last_sync_at=CASE WHEN %s THEN now() ELSE last_sync_at END, updated_at=now()
               WHERE data_source_id=%s""",
            (source_status, bool(failed), f"{failed} 个资源处理失败", done, run["data_source_id"]),
        )


def list_sync_resources(
    database_url: str, sync_run_id: str, data_source_id: str | None = None
) -> list[dict[str, object]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """SELECT r.* FROM sync_resource_runs r JOIN sync_runs s USING (sync_run_id)
               WHERE r.sync_run_id=%s AND (%s IS NULL OR s.data_source_id=%s)
               ORDER BY r.created_at, r.external_resource_id""",
            (sync_run_id, data_source_id, data_source_id),
        ).fetchall()
    return [dict(row) for row in rows]


def list_operations(database_url: str, knowledge_base_id: str, limit: int = 50) -> list[dict[str, object]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """SELECT * FROM operations WHERE knowledge_base_id=%s
               ORDER BY created_at DESC LIMIT %s""",
            (knowledge_base_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def cancel_sync_run(database_url: str, data_source_id: str, sync_run_id: str) -> bool:
    """取消尚未完成的同步；已在供应商侧执行的读取不会回滚，落库任务停止激活。"""
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        run = connection.execute(
            """SELECT operation_id, status FROM sync_runs
               WHERE sync_run_id=%s AND data_source_id=%s FOR UPDATE""",
            (sync_run_id, data_source_id),
        ).fetchone()
        if run is None:
            return False
        if str(run["status"]) in {"succeeded", "partial_failed", "failed", "aborted"}:
            return True
        connection.execute(
            """UPDATE index_jobs SET status='cancelled', finished_at=now(), updated_at=now()
               WHERE sync_run_id=%s AND status IN ('queued','running')""",
            (sync_run_id,),
        )
        connection.execute(
            """UPDATE sync_resource_runs SET status='cancelled', current_stage='cancelled',
                      finished_at=now(), updated_at=now()
               WHERE sync_run_id=%s AND status NOT IN
                 ('succeeded','unchanged','skipped','deleted','failed','dead_letter','cancelled')""",
            (sync_run_id,),
        )
        connection.execute(
            """UPDATE sync_runs SET status='aborted', stage='cancelled', finished_at=now(),
                      failure_reason='管理员取消同步', updated_at=now() WHERE sync_run_id=%s""",
            (sync_run_id,),
        )
        connection.execute(
            """UPDATE operations SET status='cancelled', current_stage='cancelled',
                      finished_at=now(), updated_at=now() WHERE operation_id=%s""",
            (run["operation_id"],),
        )
        connection.execute(
            """UPDATE data_sources SET last_sync_status='aborted',
                      sync_failure_reason='管理员取消同步', updated_at=now()
               WHERE data_source_id=%s""",
            (data_source_id,),
        )
    return True


def ensure_index_definition(
    connection: psycopg.Connection[Any], *, knowledge_base_id: str, index_version_id: str
) -> str:
    version = connection.execute(
        """SELECT chunking_version, parser_version, embedding_model, embedding_dimension,
                  processing_options, config_fingerprint
           FROM index_versions WHERE index_version_id=%s""",
        (index_version_id,),
    ).fetchone()
    if version is None:
        raise ValueError("index version not found")
    definition_id = f"idef_{uuid4().hex[:20]}"
    definition_name = f"definition-{str(version[5])[:12]}"
    definition = connection.execute(
        """INSERT INTO index_definitions
           (index_definition_id, knowledge_base_id, name, vector_config, keyword_config,
            metadata_schema, parser_schema_version, chunking_policy, embedding_model,
            embedding_dimension, reranker_config, config_fingerprint)
           VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s,%s,%s::jsonb,%s)
           ON CONFLICT (knowledge_base_id, name) DO UPDATE SET updated_at=now()
           RETURNING index_definition_id""",
        (definition_id, knowledge_base_id, definition_name,
         '{"engine":"pgvector"}', '{"engine":"pg_trgm"}',
         '{"category":true,"acl":true}', str(version[1]),
         Jsonb(dict(version[4] or {})), str(version[2]), int(version[3]),
         '{}', str(version[5])),
    ).fetchone()
    return str(definition[0])


def ensure_index_build(
    connection: psycopg.Connection[Any], *, knowledge_base_id: str,
    index_version_id: str, build_type: str = "full_rebuild",
) -> str:
    existing = connection.execute(
        "SELECT index_build_id FROM index_builds WHERE index_version_id=%s",
        (index_version_id,),
    ).fetchone()
    if existing:
        return str(existing[0])
    definition_id = ensure_index_definition(
        connection, knowledge_base_id=knowledge_base_id, index_version_id=index_version_id
    )
    operation_id = create_operation(
        connection, operation_type="index_build", knowledge_base_id=knowledge_base_id,
        idempotency_key=f"index-build:{index_version_id}", progress_mode="documents",
    )
    index_build_id = f"ib_{uuid4().hex[:20]}"
    connection.execute(
        """INSERT INTO index_builds
           (index_build_id, operation_id, index_version_id, index_definition_id, build_type)
           VALUES (%s,%s,%s,%s,%s)""",
        (index_build_id, operation_id, index_version_id, definition_id, build_type),
    )
    return index_build_id


def upsert_document_index_state(
    connection: psycopg.Connection[Any], *, index_build_id: str, index_version_id: str,
    document_id: str, document_version_id: str, status: str = "pending",
) -> None:
    lane = "ready" if status == "ready" else ("failed" if status == "failed" else "pending")
    connection.execute(
        """INSERT INTO document_index_states
           (index_build_id, index_version_id, document_id, document_version_id,
            vector_status, keyword_status, metadata_status, overall_status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (index_build_id, document_id) DO NOTHING""",
        (index_build_id, index_version_id, document_id, document_version_id,
         lane, lane, lane, status),
    )


def update_index_build_for_job(
    database_url: str, rebuild_batch_id: str, document_version_id: str,
    *, succeeded: bool, terminal: bool, failure_reason: str | None = None,
) -> None:
    with psycopg.connect(database_url) as connection, connection.transaction():
        state = "ready" if succeeded else ("failed" if terminal else "building")
        lane = "ready" if succeeded else ("failed" if terminal else "building")
        connection.execute(
            """UPDATE document_index_states dis SET overall_status=%s,
                      vector_status=%s, keyword_status=%s, metadata_status=%s,
                      chunk_count=(SELECT count(*) FROM chunks c
                                   WHERE c.index_version_id=dis.index_version_id
                                     AND c.document_version_id=dis.document_version_id),
                      failure_stage=CASE WHEN %s THEN NULL ELSE 'build' END,
                      failure_code=CASE WHEN %s THEN NULL ELSE 'INDEX_BUILD_FAILED' END,
                      failure_reason=%s, updated_at=now()
               FROM index_builds ib JOIN index_versions iv USING (index_version_id)
               WHERE dis.index_build_id=ib.index_build_id
                 AND iv.rebuild_batch_id=%s AND dis.document_version_id=%s""",
            (state, lane, lane, lane, succeeded, succeeded, failure_reason,
             rebuild_batch_id, document_version_id),
        )
    aggregate_index_build(database_url, rebuild_batch_id)


def aggregate_index_build(database_url: str, rebuild_batch_id: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        build = connection.execute(
            """SELECT ib.index_build_id, ib.operation_id FROM index_builds ib
               JOIN index_versions iv USING (index_version_id)
               WHERE iv.rebuild_batch_id=%s FOR UPDATE""",
            (rebuild_batch_id,),
        ).fetchone()
        if build is None:
            return
        counts = connection.execute(
            """SELECT count(*) total,
                      count(*) FILTER (WHERE overall_status='pending') queued,
                      count(*) FILTER (WHERE overall_status='building') processing,
                      count(*) FILTER (WHERE overall_status='ready') succeeded,
                      count(*) FILTER (WHERE overall_status='failed') failed
               FROM document_index_states WHERE index_build_id=%s""",
            (build["index_build_id"],),
        ).fetchone()
        total = int(counts["total"])
        finished = int(counts["succeeded"]) + int(counts["failed"])
        done = total == finished
        status = ("partial_failed" if counts["failed"] else "ready") if done else "building"
        connection.execute(
            """UPDATE index_builds SET status=%s, total_documents=%s, queued_documents=%s,
                      processing_documents=%s, succeeded_documents=%s, failed_documents=%s,
                      started_at=COALESCE(started_at,now()),
                      finished_at=CASE WHEN %s THEN now() ELSE NULL END, updated_at=now()
               WHERE index_build_id=%s""",
            (status, total, counts["queued"], counts["processing"], counts["succeeded"],
             counts["failed"], done, build["index_build_id"]),
        )
        percent = 100 if done else (round(finished * 100 / total, 2) if total else None)
        operation_status = "partial_failed" if done and counts["failed"] else ("ready" if done else "running")
        connection.execute(
            """UPDATE operations SET status=%s, current_stage=%s, progress_percent=%s,
                      total_count=%s, completed_count=%s, processing_count=%s, failed_count=%s,
                      started_at=COALESCE(started_at,now()),
                      finished_at=CASE WHEN %s THEN now() ELSE NULL END, updated_at=now()
               WHERE operation_id=%s""",
            (operation_status, "validate" if done else "build", percent, total, finished,
             counts["processing"], counts["failed"], done, build["operation_id"]),
        )
    if done:
        # Build 与 Index Version 共用同一个终态判断，避免页面 100% 而版本仍停在 building。
        from .index_versions import finalize_building_version

        with psycopg.connect(database_url) as connection:
            version = connection.execute(
                "SELECT index_version_id FROM index_versions WHERE rebuild_batch_id=%s",
                (rebuild_batch_id,),
            ).fetchone()
        if version:
            version_status = finalize_building_version(database_url, str(version[0]))
            with psycopg.connect(database_url) as connection, connection.transaction():
                connection.execute(
                    """UPDATE index_builds SET status=%s, updated_at=now()
                       WHERE index_build_id=%s""",
                    ("ready" if version_status == "ready" else "failed", build["index_build_id"]),
                )


def update_index_stage(
    database_url: str, rebuild_batch_id: str, document_version_id: str, stage: str
) -> None:
    """按实际执行点更新 Vector/Keyword/Metadata 三路状态。"""
    allowed = {"parsing", "chunking", "vector", "keyword", "metadata", "validating"}
    if stage not in allowed:
        raise ValueError(f"unsupported index stage: {stage}")
    assignments = {
        "parsing": ("building", "pending", "pending", "building"),
        "chunking": ("building", "pending", "pending", "building"),
        "vector": ("building", "pending", "pending", "building"),
        "keyword": ("ready", "building", "pending", "building"),
        "metadata": ("ready", "ready", "building", "building"),
        "validating": ("ready", "ready", "ready", "validating"),
    }[stage]
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE document_index_states dis SET vector_status=%s, keyword_status=%s,
                      metadata_status=%s, overall_status=%s, updated_at=now()
               FROM index_builds ib JOIN index_versions iv USING (index_version_id)
               WHERE dis.index_build_id=ib.index_build_id AND iv.rebuild_batch_id=%s
                 AND dis.document_version_id=%s""",
            (*assignments, rebuild_batch_id, document_version_id),
        )


def list_index_builds(database_url: str, knowledge_base_id: str) -> list[dict[str, object]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """SELECT ib.*, o.progress_percent, o.current_stage, iv.knowledge_base_id
               FROM index_builds ib JOIN operations o USING (operation_id)
               JOIN index_versions iv USING (index_version_id)
               WHERE iv.knowledge_base_id=%s ORDER BY ib.created_at DESC""",
            (knowledge_base_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_index_definitions(database_url: str, knowledge_base_id: str) -> list[dict[str, object]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """SELECT * FROM index_definitions WHERE knowledge_base_id=%s
               ORDER BY active DESC, updated_at DESC""",
            (knowledge_base_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_document_index_states(
    database_url: str, knowledge_base_id: str, index_build_id: str
) -> list[dict[str, object]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """SELECT dis.*, d.filename FROM document_index_states dis
               JOIN documents d ON d.document_id=dis.document_id
               JOIN index_versions iv ON iv.index_version_id=dis.index_version_id
               WHERE dis.index_build_id=%s AND iv.knowledge_base_id=%s
               ORDER BY d.filename""",
            (index_build_id, knowledge_base_id),
        ).fetchall()
    return [dict(row) for row in rows]
