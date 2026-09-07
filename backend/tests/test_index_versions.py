from __future__ import annotations

import os
import json
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.app.audit import AuditRepository
from backend.app.database import apply_migrations
from backend.app.errors import AppError
from backend.app.index_versions import (
    active_index_version_id,
    active_or_bootstrap_version,
    cleanup_version,
    compare_index_version,
    component_manifest,
    config_fingerprint,
    create_building_version,
    create_partial_vector_index,
    drop_partial_vector_index,
    finalize_building_version,
    get_version,
    retire_version,
    rollback_to_previous,
)
from backend.app.index_validation import (
    activate_with_report,
    check_retrieval_quality,
    get_validation_policy,
    get_report,
    list_reports,
    validate_index_version,
)
from backend.evaluation.report import RetrievalEvaluationReport, assess_metric
from backend.app.pipeline_governance import ensure_index_build
from backend.app.postgres_documents import (
    cancel_index_version_build,
    create_index_version_candidate,
    retry_index_version_build,
)

KNOWLEDGE_BASE_ID = "kb_default"
DATA_SOURCE_ID = "ds_default"
EMBEDDING_DIMENSION = 3


def test_config_fingerprint_is_stable_and_order_independent() -> None:
    first = config_fingerprint("v1-700-100", "test/embedding", 3, {"a": 1, "b": 2})
    second = config_fingerprint("v1-700-100", "test/embedding", 3, {"b": 2, "a": 1})
    assert first == second
    assert len(first) == 64


def test_config_fingerprint_changes_with_chunking_version() -> None:
    first = config_fingerprint("v1-700-100", "test/embedding", 3, {})
    second = config_fingerprint("v1-160-20", "test/embedding", 3, {})
    assert first != second


def test_config_fingerprint_changes_with_every_frozen_field() -> None:
    """指纹是切换放行的实际牙齿：任一冻结配置变化都必须改变指纹。"""

    base = ("v1-700-100", "test/embedding", 3, {"chunk_size": 700})
    variants = [
        ("v1-160-20", "test/embedding", 3, {"chunk_size": 700}),
        ("v1-700-100", "test/other", 3, {"chunk_size": 700}),
        ("v1-700-100", "test/embedding", 4, {"chunk_size": 700}),
        ("v1-700-100", "test/embedding", 3, {"chunk_size": 160}),
    ]
    fingerprints = {config_fingerprint(*base)} | {config_fingerprint(*item) for item in variants}
    assert len(fingerprints) == len(variants) + 1


def test_config_fingerprint_changes_with_component_manifest() -> None:
    base = component_manifest(reranker_model="test/reranker-v1")
    upgraded = {**base, "metadata_schema_version": "chunk-metadata-v2"}

    assert config_fingerprint("v1-700-100", "test/embedding", 3, {}, base) != config_fingerprint(
        "v1-700-100", "test/embedding", 3, {}, upgraded
    )


def test_validation_policy_is_explicitly_versioned() -> None:
    policy = get_validation_policy()

    assert policy["version"] == "v2"
    assert "component_manifest" in policy["critical_checks"]["technical"]
    assert "ndcg_at_10_present" in policy["critical_checks"]["retrieval_quality"]
    assert policy["warning_checks"] == ["legacy_advanced_metric_missing"]


def _reset(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO knowledge_bases
               (knowledge_base_id, name, name_normalized, description, is_default,
                created_at, updated_at)
               VALUES (%s, '默认知识库', '默认知识库', '', true, now(), now())""",
            (KNOWLEDGE_BASE_ID,),
        )
        connection.execute(
            """INSERT INTO data_sources
               (data_source_id, knowledge_base_id, source_type, name, created_at, updated_at)
               VALUES (%s, %s, 'file', '上传', now(), now())""",
            (DATA_SOURCE_ID, KNOWLEDGE_BASE_ID),
        )


def _create(database_url: str, chunking: str = "v1-700-100") -> str:
    index_version_id, _ = create_building_version(
        database_url,
        KNOWLEDGE_BASE_ID,
        chunking_version=chunking,
        parser_version="structured-1",
        embedding_model="test/embedding",
        embedding_dimension=EMBEDDING_DIMENSION,
        processing_options={"chunk_size": 700, "chunk_overlap": 100},
        rebuild_batch_id="rbd_test",
    )
    return index_version_id


def _add_document(database_url: str, name: str) -> str:
    """插入一个 ready 的当前版本文档，返回 document_version_id。

    documents 与 document_versions 互相引用，因此先插空指针的 documents 再回填。
    """

    document_id = f"doc_{name}"
    document_version_id = f"dv_{name}"
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO documents
               (document_id, knowledge_base_id, data_source_id, filename, created_at, updated_at)
               VALUES (%s, %s, %s, %s, now(), now())""",
            (document_id, KNOWLEDGE_BASE_ID, DATA_SOURCE_ID, f"{name}.md"),
        )
        connection.execute(
            """INSERT INTO document_versions
               (document_version_id, knowledge_base_id, document_id, version_number,
                content_sha256, source_file_bytes, source_path, status, created_at,
                parser_name, parser_version, parse_status)
               VALUES (%s, %s, %s, 1, %s, 10, %s, 'ready', now(),
                       'markdown', 'structured-1', 'ready')""",
            (
                document_version_id,
                KNOWLEDGE_BASE_ID,
                document_id,
                "a" * 64,
                f"/tmp/{name}.md",
            ),
        )
        connection.execute(
            """UPDATE documents SET current_version_id = %s
               WHERE knowledge_base_id = %s AND document_id = %s""",
            (document_version_id, KNOWLEDGE_BASE_ID, document_id),
        )
    return document_version_id


def _add_chunks(
    database_url: str,
    index_version_id: str,
    document_version_id: str,
    count: int = 1,
) -> None:
    with psycopg.connect(database_url) as connection, connection.transaction():
        for chunk_index in range(count):
            connection.execute(
                """INSERT INTO chunks
                   (chunk_id, document_version_id, index_version_id, knowledge_base_id,
                    chunk_index, content, embedding, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s::vector, now())""",
                (
                    f"{document_version_id}:{index_version_id}:{chunk_index:05d}",
                    document_version_id,
                    index_version_id,
                    KNOWLEDGE_BASE_ID,
                    chunk_index,
                    f"第 {chunk_index} 段语料。",
                    f"[{0.01 * chunk_index:.4f},0.2,0.3]",
                ),
            )


def _add_job(database_url: str, name: str, status: str, document_version_id: str | None) -> None:
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO index_jobs
               (index_job_id, knowledge_base_id, document_version_id, idempotency_key, status,
                job_type, rebuild_batch_id, target_chunking_version)
               VALUES (%s, %s, %s, %s, %s, 'rebuild', 'rbd_test', 'v1-700-100')""",
            (f"job_{name}", KNOWLEDGE_BASE_ID, document_version_id, f"idem_{name}", status),
        )


def _index_names(database_url: str) -> list[str]:
    with psycopg.connect(database_url) as connection:
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'chunks'"
            ).fetchall()
        ]


def _hnsw_index_name(database_url: str, index_version_id: str) -> str | None:
    """按 indexdef 而不是命名约定找该版本的部分 HNSW 索引，测试不依赖私有命名函数。"""

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """SELECT indexname FROM pg_indexes
               WHERE tablename = 'chunks' AND indexdef ILIKE %s AND indexdef ILIKE %s""",
            ("%using hnsw%", f"%{index_version_id}%"),
        ).fetchone()
    return str(row[0]) if row else None


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_only_one_building_version_per_knowledge_base() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    _create(database_url)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _create(database_url, "v1-160-20")


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_active_version_requires_evaluation_report() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    index_version_id = _create(database_url)
    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "UPDATE index_versions SET status='active' WHERE index_version_id=%s",
                (index_version_id,),
            )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_bootstrap_creates_and_activates_the_first_version() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    assert active_index_version_id(database_url, KNOWLEDGE_BASE_ID) is None

    index_version_id = active_or_bootstrap_version(
        database_url,
        KNOWLEDGE_BASE_ID,
        chunking_version="v1-700-100",
        parser_version="structured-1",
        embedding_model="test/embedding",
        embedding_dimension=EMBEDDING_DIMENSION,
        processing_options={"chunk_size": 700, "chunk_overlap": 100},
        reranker_model="test/reranker",
    )

    assert index_version_id.startswith("iv_")
    assert active_index_version_id(database_url, KNOWLEDGE_BASE_ID) == index_version_id
    version = get_version(database_url, index_version_id)
    assert version is not None
    assert version["status"] == "active"
    # 首个版本没有可比较的基线，用固定标记满足"active 必须有放行依据"的约束。
    assert version["evaluation_report_id"] == "initial-index"
    assert version["activated_at"] is not None
    assert version["version_no"] == 1
    assert version["creation_reason"] == "initial_build"
    assert version["config_completeness"] == "complete"
    assert version["component_manifest"]["reranker_model"] == "test/reranker"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_bootstrap_is_idempotent() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    first = active_or_bootstrap_version(
        database_url,
        KNOWLEDGE_BASE_ID,
        chunking_version="v1-700-100",
        parser_version="structured-1",
        embedding_model="test/embedding",
        embedding_dimension=EMBEDDING_DIMENSION,
        processing_options={"chunk_size": 700, "chunk_overlap": 100},
    )
    # 第二次调用即使配置不同也不得再建版本：active 指针已经存在。
    second = active_or_bootstrap_version(
        database_url,
        KNOWLEDGE_BASE_ID,
        chunking_version="v1-160-20",
        parser_version="structured-1",
        embedding_model="test/embedding",
        embedding_dimension=EMBEDDING_DIMENSION,
        processing_options={"chunk_size": 160, "chunk_overlap": 20},
    )
    assert second == first
    with psycopg.connect(database_url) as connection:
        total = int(connection.execute("SELECT count(*) FROM index_versions").fetchone()[0])
    assert total == 1


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_finalize_requires_full_document_coverage() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    index_version_id = _create(database_url)
    first = _add_document(database_url, "first")
    _add_document(database_url, "second")
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO document_versions
               (document_version_id, knowledge_base_id, document_id, version_number,
                content_sha256, source_file_bytes, source_path, status, created_at,
                parser_name, parser_version, parse_status)
               VALUES ('dv_first_v2', %s, 'doc_first', 2, %s, 12, '/tmp/first-v2.md',
                       'ready', now(), 'markdown', 'structured-1', 'ready')""",
            (KNOWLEDGE_BASE_ID, "b" * 64),
        )
        connection.execute(
            """UPDATE documents SET current_version_id='dv_first_v2'
               WHERE knowledge_base_id=%s AND document_id='doc_first'""",
            (KNOWLEDGE_BASE_ID,),
        )
    _add_job(database_url, "first", "succeeded", first)
    _add_job(database_url, "second", "succeeded", None)
    # 只覆盖了一篇文档，新版本不完整，不能放行。
    _add_chunks(database_url, index_version_id, first)

    assert finalize_building_version(database_url, index_version_id) == "build_failed"
    version = get_version(database_url, index_version_id)
    assert version is not None
    assert version["status"] == "build_failed"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_finalize_keeps_building_while_jobs_are_pending() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    index_version_id = _create(database_url)
    document_version_id = _add_document(database_url, "first")
    _add_job(database_url, "first", "queued", document_version_id)

    assert finalize_building_version(database_url, index_version_id) == "building"
    version = get_version(database_url, index_version_id)
    assert version is not None
    assert version["status"] == "building"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_finalize_fails_the_version_when_a_job_failed() -> None:
    """任一任务终态失败即整批不完整，active 指针不动。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    index_version_id = _create(database_url)
    first = _add_document(database_url, "first")
    _add_job(database_url, "first", "failed", first)
    _add_chunks(database_url, index_version_id, first)

    assert finalize_building_version(database_url, index_version_id) == "build_failed"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_finalize_marks_ready_and_creates_the_partial_index() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    # 文档要先于索引版本存在：版本创建时会冻结输入快照，此后新增的文档不属于本次构建，
    # 也就不能拿来充抵它的覆盖率。此前这里是先建版本再加文档，靠的正是那个已被修掉的漏洞。
    first = _add_document(database_url, "first")
    index_version_id = _create(database_url)
    _add_job(database_url, "first", "succeeded", first)
    _add_chunks(database_url, index_version_id, first, count=3)

    assert finalize_building_version(database_url, index_version_id) == "validating"
    assert _hnsw_index_name(database_url, index_version_id) is not None

    # 已经离开 building 的版本再次调用只回报当前状态，不重复推进。
    assert finalize_building_version(database_url, index_version_id) == "validating"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_finalize_rejects_an_unknown_version() -> None:
    """错误类型与 switch/rollback/retire 保持一致，CLI 才能统一按 AppError.code 输出。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    with pytest.raises(AppError) as error:
        finalize_building_version(database_url, "iv_missing")
    assert error.value.code == "INDEX_VERSION_NOT_FOUND"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_partial_index_create_is_idempotent_and_drop_removes_it() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    index_version_id = _create(database_url)
    document_version_id = _add_document(database_url, "first")
    _add_chunks(database_url, index_version_id, document_version_id, count=3)

    create_partial_vector_index(database_url, index_version_id)
    create_partial_vector_index(database_url, index_version_id)
    assert len([name for name in _index_names(database_url) if index_version_id in name]) == 1

    drop_partial_vector_index(database_url, index_version_id)
    assert not any(index_version_id in name for name in _index_names(database_url))
    # 清理路径要能重复执行：retire 可能在索引已被删除后再跑一次。
    drop_partial_vector_index(database_url, index_version_id)


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_partial_index_fixes_a_dimensionless_embedding_column() -> None:
    """空库迁移后 chunks.embedding 没有维度修饰，pgvector 直接拒绝建 ANN 索引。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    with psycopg.connect(database_url) as connection:
        typmod = int(
            connection.execute(
                """SELECT atttypmod FROM pg_attribute
                   WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"""
            ).fetchone()[0]
        )
    assert typmod < 0

    index_version_id = _create(database_url)
    document_version_id = _add_document(database_url, "first")
    _add_chunks(database_url, index_version_id, document_version_id, count=3)
    create_partial_vector_index(database_url, index_version_id)

    with psycopg.connect(database_url) as connection:
        column_type = str(
            connection.execute(
                """SELECT format_type(atttypid, atttypmod) FROM pg_attribute
                   WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"""
            ).fetchone()[0]
        )
    assert column_type == f"vector({EMBEDDING_DIMENSION})"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_partial_index_is_used_by_planner() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    index_version_id = _create(database_url)
    document_version_id = _add_document(database_url, "first")
    _add_chunks(database_url, index_version_id, document_version_id, count=30)
    create_partial_vector_index(database_url, index_version_id)
    name = _hnsw_index_name(database_url, index_version_id)
    assert name is not None

    with psycopg.connect(database_url) as connection:
        connection.execute("ANALYZE chunks")
        # 30 行的顺扫成本（1.45）低于 HNSW 扫描（12.22），规划器实测会选顺扫；
        # 关掉顺扫才能断言"索引谓词与查询谓词匹配、且能承担 ORDER BY <=>"这件事本身。
        connection.execute("SET enable_seqscan = off")
        plan = connection.execute(
            """EXPLAIN SELECT chunk_id FROM chunks
               WHERE index_version_id = %s
               ORDER BY embedding <=> %s::vector LIMIT 5""",
            (index_version_id, "[0.1,0.2,0.3]"),
        ).fetchall()
    rendered = "\n".join(str(row[0]) for row in plan)
    # 只断言 "Index Scan" 不够：0010 的 chunks_index_version_idx（btree）同样是 Index Scan。
    assert f"Index Scan using {name}" in rendered
    # 距离排序由索引承担，因此不该再有 Sort 节点。
    assert "Sort" not in rendered


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_finalize_rejects_a_version_covering_no_documents() -> None:
    """覆盖 0 篇文档的版本不得放行：分子分母同时为 0 时算术上"完整"，切过去却是空索引。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    index_version_id = _create(database_url)
    assert finalize_building_version(database_url, index_version_id) == "build_failed"


def _passing_report(
    fingerprint: str | None,
    *,
    official: bool = True,
    regressing: bool = False,
    below_threshold: bool = False,
) -> RetrievalEvaluationReport:
    """构造一份可控的放行报告；指标结论由 assess_metric 生成，不手写结论字段。

    ``regressing`` 通过传入更高的 baseline 触发 regressed——切换的质量门看的是这一项。
    ``below_threshold`` 只压低绝对值不设 baseline，用于验证未达冻结阈值**不**阻止切换。
    """

    if regressing:
        graded = assess_metric(0.70, 0.80, baseline=0.90)
    elif below_threshold:
        graded = assess_metric(0.60, 0.80)
    else:
        graded = assess_metric(0.90, 0.80)
    return RetrievalEvaluationReport(
        report_id="rep_switch_test",
        dataset_id="corpus_v2",
        dataset_version="2.0.0",
        commit="2554195",
        run_at=datetime(2026, 8, 27, tzinfo=UTC),
        official=official,
        models={"embedding": "test/embedding", "reranker": "test/reranker"},
        parameters={"chunk_size": 700},
        query_count=12,
        recall_at_5=graded,
        recall_at_10=assess_metric(0.90, 0.80),
        vector_mrr=assess_metric(0.90, 0.80),
        rerank_mrr=assess_metric(0.90, 0.80),
        ndcg_at_10=assess_metric(0.90, 0.80),
        metadata_filter_accuracy=assess_metric(1.0, 1.0),
        acl_leak_count=0,
        config_fingerprint=fingerprint,
    )


def _fingerprint_of(database_url: str, index_version_id: str) -> str:
    version = get_version(database_url, index_version_id)
    assert version is not None
    return str(version["config_fingerprint"])


def _matching_report(database_url: str, index_version_id: str) -> RetrievalEvaluationReport:
    return _passing_report(_fingerprint_of(database_url, index_version_id))


def _ready_version(
    database_url: str,
    name: str,
    document_version_id: str,
    *,
    chunking: str,
    chunk_count: int = 2,
) -> str:
    """建一个覆盖该文档、且已通过三层门禁的 ready 版本。

    finalize 现在只推进到 validating——ready 在新状态机里只有一个入口，就是门禁通过。
    """

    index_version_id, _ = create_building_version(
        database_url,
        KNOWLEDGE_BASE_ID,
        chunking_version=chunking,
        parser_version="structured-1",
        embedding_model="test/embedding",
        embedding_dimension=EMBEDDING_DIMENSION,
        processing_options={"chunk_size": 700, "chunk_overlap": 100},
        rebuild_batch_id=f"rbd_{name}",
    )
    _add_chunks(database_url, index_version_id, document_version_id, count=chunk_count)
    assert finalize_building_version(database_url, index_version_id) == "validating"
    result = validate_index_version(
        database_url, index_version_id, _matching_report(database_url, index_version_id)
    )
    assert result["status"] == "pass", result["failure_items"]
    return index_version_id


def _chunk_count(database_url: str, index_version_id: str) -> int:
    with psycopg.connect(database_url) as connection:
        return int(
            connection.execute(
                "SELECT count(*) FROM chunks WHERE index_version_id = %s", (index_version_id,)
            ).fetchone()[0]
        )


def _status(database_url: str, index_version_id: str) -> str:
    version = get_version(database_url, index_version_id)
    assert version is not None
    return str(version["status"])


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_switch_rejects_an_unknown_version() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    with pytest.raises(AppError) as error:
        activate_with_report(database_url, "iv_missing", _passing_report("b" * 64))
    assert error.value.code == "INDEX_VERSION_NOT_FOUND"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_switch_rejects_a_version_that_is_not_ready() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    index_version_id = _create(database_url)
    with pytest.raises(AppError) as error:
        activate_with_report(database_url, index_version_id, _matching_report(database_url, index_version_id))
    assert error.value.code == "INDEX_VERSION_NOT_VALIDATABLE"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_switch_accepts_a_report_below_the_frozen_thresholds() -> None:
    """未达冻结绝对阈值不阻止切换，只在返回值里如实标注。

    绝对阈值回答"系统能否上线"，切换要回答的是"这次换配置是变好还是变坏"。用绝对阈值
    当切换门槛会锁死功能：corpus_v2 在当前实现下召回 0.6862 未达 0.70，永远产不出
    passed 的报告。official 同样不再检查——run_corpus_baseline 里它就等于 passed。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    index_version_id = _ready_version(database_url, "first", document_version_id, chunking="v1-700-100")
    report = _passing_report(
        _fingerprint_of(database_url, index_version_id), official=False, below_threshold=True
    )

    result = activate_with_report(database_url, index_version_id, report)

    assert result["active"] == index_version_id
    # 绝对阈值结论如实记进报告的检索质量层，但不参与放行判定。
    stored = get_report(database_url, str(result["validation_report_id"]))
    assert stored["retrieval_result"]["meets_frozen_thresholds"] is False
    assert len(list_reports(database_url, index_version_id, KNOWLEDGE_BASE_ID)) == 1
    assert list_reports(database_url, index_version_id, "kb_other") == []
    assert get_version(database_url, index_version_id)["status"] == "active"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_switch_rejects_a_report_that_regressed_against_the_baseline() -> None:
    """回退判定归报告自己（assess_metric 的 baseline 与 max_regression），切换只读 regressed。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    index_version_id = _ready_version(database_url, "first", document_version_id, chunking="v1-700-100")
    report = _passing_report(_fingerprint_of(database_url, index_version_id), regressing=True)
    with pytest.raises(AppError) as error:
        activate_with_report(database_url, index_version_id, report)
    assert error.value.code == "VALIDATION_NOT_PASSED"
    assert "recall_at_5" in error.value.message


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_switch_rejects_report_without_fingerprint() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    index_version_id = _ready_version(database_url, "first", document_version_id, chunking="v1-700-100")
    with pytest.raises(AppError) as error:
        activate_with_report(database_url, index_version_id, _passing_report(None))
    assert error.value.code == "VALIDATION_NOT_PASSED"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_switch_rejects_fingerprint_mismatch() -> None:
    """质量门的实际牙齿：A 配置跑出的合格报告不能放行 B 配置的索引。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    index_version_id = _ready_version(database_url, "first", document_version_id, chunking="v1-700-100")
    other = config_fingerprint("v1-160-20", "test/embedding", EMBEDDING_DIMENSION, {})
    assert other != _fingerprint_of(database_url, index_version_id)
    with pytest.raises(AppError) as error:
        activate_with_report(database_url, index_version_id, _passing_report(other))
    assert error.value.code == "VALIDATION_NOT_PASSED"
    # 门禁未过的版本进入 validation_failed，而不是停在 ready 等人再试一次——
    # 停在 ready 的话，它和「已通过门禁」的版本又变得同形了。
    assert _status(database_url, index_version_id) == "validation_failed"
    assert active_index_version_id(database_url, KNOWLEDGE_BASE_ID) is None


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_retrieval_quality_gate_blocks_an_acl_leak() -> None:
    """报告即使 Recall/MRR 全过，只要出现越权召回也不能进入 ready。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    index_version_id = _create(database_url)
    _add_job(database_url, "first", "succeeded", document_version_id)
    _add_chunks(database_url, index_version_id, document_version_id)
    assert finalize_building_version(database_url, index_version_id) == "validating"
    report = _matching_report(database_url, index_version_id).model_copy(
        update={"acl_leak_count": 1}
    )

    result = validate_index_version(database_url, index_version_id, report)

    assert result["status"] == "failed"
    assert [item["check_key"] for item in result["failure_items"]] == ["acl_leak_count"]


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_complete_version_requires_all_advanced_governance_metrics() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    index_version_id = _create(database_url)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE index_versions SET config_completeness='complete'
               WHERE index_version_id=%s""",
            (index_version_id,),
        )
    legacy_shape = _matching_report(database_url, index_version_id).model_copy(
        update={
            "recall_at_10": None,
            "ndcg_at_10": None,
            "metadata_filter_accuracy": None,
            "acl_leak_count": None,
        }
    )

    with psycopg.connect(database_url) as connection:
        result = check_retrieval_quality(connection, index_version_id, legacy_shape)

    failed = {
        item["check_key"]
        for item in result["checks"]
        if item["status"] == "fail" and item["severity"] == "critical"
    }
    assert failed == {
        "recall_at_10_present",
        "ndcg_at_10_present",
        "metadata_filter_accuracy_present",
        "acl_leak_count",
    }


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_creation_context_exposes_the_effective_definition_and_real_document_scope() -> None:
    """创建入口必须读取真实配置与文档集合，不能再由页面硬编码 500/50。"""

    from backend.app.index_versions import get_index_version_creation_context

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    _add_document(database_url, "first")
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "INSERT INTO index_settings (embedding_model, embedding_dimension) VALUES (%s,%s)",
            ("test/embedding", EMBEDDING_DIMENSION),
        )

    context = get_index_version_creation_context(
        database_url,
        KNOWLEDGE_BASE_ID,
        chunk_size=700,
        chunk_overlap=100,
        reranker_model="test/reranker",
    )

    assert context["scenario"] == "initial_build"
    assert context["creation_allowed"] is True
    assert context["document_scope"] == {
        "included": 1,
        "excluded": 0,
        "source_bytes": 10,
        "parse_failed": 0,
        "missing_current_revision": 0,
    }
    assert context["document_exclusions"] == []
    assert context["definition"]["chunking"]["chunk_size"] == 700
    assert context["definition"]["embedding"]["model"] == "test/embedding"
    assert context["definition"]["components"]["citation_schema_version"]


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_candidate_creation_rolls_back_if_a_document_job_started_after_preview() -> None:
    """Preview 后出现任务竞态时，不得留下半套 Version/Snapshot/Build。"""

    from backend.app.index_versions import preview_index_version_candidate

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "INSERT INTO index_settings (embedding_model, embedding_dimension) VALUES (%s,%s)",
            ("test/embedding", EMBEDDING_DIMENSION),
        )
    preview = preview_index_version_candidate(
        database_url,
        KNOWLEDGE_BASE_ID,
        reason="initial_build",
        chunk_size=700,
        chunk_overlap=100,
        force=False,
        force_reason=None,
        reranker_model="test/reranker",
    )
    _add_job(database_url, "preview_race", "queued", document_version_id)

    with pytest.raises(AppError) as error:
        create_index_version_candidate(
            database_url,
            KNOWLEDGE_BASE_ID,
            reason="initial_build",
            chunk_size=700,
            chunk_overlap=100,
            force=False,
            force_reason=None,
            expected_config_fingerprint=str(preview["config_fingerprint"]),
            expected_document_set_fingerprint=str(preview["document_set_fingerprint"]),
            expected_release_fingerprint=str(preview["release_fingerprint"]),
            requested_by="usr_admin",
            idempotency_key="preview-race",
            reranker_model="test/reranker",
        )

    assert error.value.code == "DOCUMENT_INDEX_TASK_IN_PROGRESS"
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM index_versions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM document_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM index_builds").fetchone()[0] == 0


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_candidate_preview_rejects_an_unchanged_release_without_a_force_reason() -> None:
    """相同配置与相同文档集合不能误创建重复版本。"""

    from backend.app.document_snapshots import create_snapshot
    from backend.app.index_versions import preview_index_version_candidate

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    _add_document(database_url, "first")
    components = component_manifest(reranker_model="test/reranker")
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "INSERT INTO index_settings (embedding_model, embedding_dimension) VALUES (%s,%s)",
            ("test/embedding", EMBEDDING_DIMENSION),
        )
        snapshot_id = create_snapshot(
            connection, knowledge_base_id=KNOWLEDGE_BASE_ID, reason="active-test"
        )
        snapshot = connection.execute(
            "SELECT snapshot_fingerprint FROM document_snapshots WHERE document_snapshot_id=%s",
            (snapshot_id,),
        ).fetchone()
        connection.execute(
            """INSERT INTO index_versions
               (index_version_id, knowledge_base_id, status, chunking_version, parser_version,
                embedding_model, embedding_dimension, processing_options, config_fingerprint,
                evaluation_report_id, validation_report_id, document_snapshot_id, activated_at,
                component_manifest, config_completeness)
               VALUES ('iv_active_context', %s, 'active', 'v1-700-100', 'structured-1',
                       'test/embedding', %s, %s::jsonb, %s, 'report', NULL, %s, now(),
                       %s::jsonb, 'complete')""",
            (
                KNOWLEDGE_BASE_ID,
                EMBEDDING_DIMENSION,
                '{"chunk_size":700,"chunk_overlap":100}',
                config_fingerprint(
                    "v1-700-100",
                    "test/embedding",
                    EMBEDDING_DIMENSION,
                    {"chunk_size": 700, "chunk_overlap": 100},
                    components,
                ),
                snapshot_id,
                json.dumps(components),
            ),
        )
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id='iv_active_context' WHERE knowledge_base_id=%s",
            (KNOWLEDGE_BASE_ID,),
        )

    preview = preview_index_version_candidate(
        database_url,
        KNOWLEDGE_BASE_ID,
        reason="manual_rebuild",
        chunk_size=700,
        chunk_overlap=100,
        force=False,
        force_reason=None,
        reranker_model="test/reranker",
    )

    assert preview["document_set_fingerprint"] == snapshot[0]
    assert preview["creation_allowed"] is False
    assert "配置与文档集合均未变化；如需修复性重建，请填写原因。" in preview[
        "blocked_reasons"
    ]
    assert "修复性或主动重建必须显式确认强制创建。" in preview["blocked_reasons"]


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_candidate_preview_allows_a_document_snapshot_change() -> None:
    """配置未变但当前文档集合变化时仍属于有效的新版本场景。"""

    from backend.app.document_snapshots import create_snapshot
    from backend.app.index_versions import preview_index_version_candidate

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    _add_document(database_url, "first")
    components = component_manifest(reranker_model="test/reranker")
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "INSERT INTO index_settings (embedding_model, embedding_dimension) VALUES (%s,%s)",
            ("test/embedding", EMBEDDING_DIMENSION),
        )
        snapshot_id = create_snapshot(
            connection, knowledge_base_id=KNOWLEDGE_BASE_ID, reason="active-test"
        )
        connection.execute(
            """INSERT INTO index_versions
               (index_version_id, knowledge_base_id, status, chunking_version, parser_version,
                embedding_model, embedding_dimension, processing_options, config_fingerprint,
                evaluation_report_id, document_snapshot_id, activated_at,
                component_manifest, config_completeness)
               VALUES ('iv_active_context', %s, 'active', 'v1-700-100', 'structured-1',
                       'test/embedding', %s, %s::jsonb, %s, 'report', %s, now(),
                       %s::jsonb, 'complete')""",
            (
                KNOWLEDGE_BASE_ID,
                EMBEDDING_DIMENSION,
                '{"chunk_size":700,"chunk_overlap":100}',
                config_fingerprint(
                    "v1-700-100", "test/embedding", EMBEDDING_DIMENSION,
                    {"chunk_size": 700, "chunk_overlap": 100},
                    components,
                ),
                snapshot_id,
                json.dumps(components),
            ),
        )
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id='iv_active_context' WHERE knowledge_base_id=%s",
            (KNOWLEDGE_BASE_ID,),
        )
    _add_document(database_url, "second")

    preview = preview_index_version_candidate(
        database_url,
        KNOWLEDGE_BASE_ID,
        reason="document_snapshot_changed",
        chunk_size=700,
        chunk_overlap=100,
        force=False,
        force_reason=None,
        reranker_model="test/reranker",
    )

    assert preview["creation_allowed"] is True
    assert preview["document_diff"]["added"] == 1
    assert preview["document_diff"]["updated"] == 1
    assert preview["estimated_documents"] == 2


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_switch_moves_the_pointer_and_keeps_previous_chunks() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    first = _ready_version(
        database_url, "first", document_version_id, chunking="v1-700-100", chunk_count=2
    )
    activate_with_report(database_url, first, _matching_report(database_url, first))
    assert active_index_version_id(database_url, KNOWLEDGE_BASE_ID) == first

    second = _ready_version(
        database_url, "second", document_version_id, chunking="v1-160-20", chunk_count=3
    )
    result = activate_with_report(database_url, second, _matching_report(database_url, second))

    assert result["knowledge_base_id"] == KNOWLEDGE_BASE_ID
    assert result["active"] == second
    assert result["previous"] == first
    assert result["validation_report_id"].startswith("vr_")
    assert active_index_version_id(database_url, KNOWLEDGE_BASE_ID) == second
    assert _status(database_url, second) == "active"
    assert _status(database_url, first) == "previous"
    # 回滚的前提：降级不删数据。
    assert _chunk_count(database_url, first) == 2
    version = get_version(database_url, second)
    assert version is not None
    assert version["evaluation_report_id"] == "rep_switch_test"
    assert version["activated_at"] is not None


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_three_versions_switch_without_violating_the_partial_unique_indexes() -> None:
    """连续两次切换会同时出现"降级原 active"和"退役原 previous"。

    顺序错的实测后果：退役语句跑在降级之后会把刚降级的版本一并退役，第二个版本变成
    retired 而不是 previous，回滚路径静默消失。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    first = _ready_version(
        database_url, "first", document_version_id, chunking="v1-700-100", chunk_count=2
    )
    activate_with_report(database_url, first, _matching_report(database_url, first))
    second = _ready_version(
        database_url, "second", document_version_id, chunking="v1-160-20", chunk_count=3
    )
    activate_with_report(database_url, second, _matching_report(database_url, second))
    third = _ready_version(
        database_url, "third", document_version_id, chunking="v1-320-40", chunk_count=4
    )
    activate_with_report(database_url, third, _matching_report(database_url, third))

    assert _status(database_url, first) == "retired"
    assert _status(database_url, second) == "previous"
    assert _status(database_url, third) == "active"
    assert active_index_version_id(database_url, KNOWLEDGE_BASE_ID) == third
    # retired 只表示"可以删"，本身不删数据。
    assert _chunk_count(database_url, first) == 2


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_rollback_restores_the_previous_version() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    first = _ready_version(
        database_url, "first", document_version_id, chunking="v1-700-100", chunk_count=2
    )
    activate_with_report(database_url, first, _matching_report(database_url, first))
    second = _ready_version(
        database_url, "second", document_version_id, chunking="v1-160-20", chunk_count=3
    )
    activate_with_report(database_url, second, _matching_report(database_url, second))

    result = rollback_to_previous(database_url, KNOWLEDGE_BASE_ID)

    assert result == {
        "knowledge_base_id": KNOWLEDGE_BASE_ID,
        "active": first,
        "demoted": second,
    }
    assert active_index_version_id(database_url, KNOWLEDGE_BASE_ID) == first
    assert _status(database_url, first) == "active"
    # 被撤下的版本回到 ready：它已被质量门放行过，且再降为 previous 会撞唯一索引。
    assert _status(database_url, second) == "ready"
    assert _chunk_count(database_url, second) == 3
    version = get_version(database_url, first)
    assert version is not None
    # active 必须带放行依据，回滚沿用该版本原有报告。
    assert version["evaluation_report_id"] == "rep_switch_test"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_rollback_requires_confirmation_when_current_document_revisions_are_newer() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    first = _ready_version(
        database_url, "first", document_version_id, chunking="v1-700-100"
    )
    activate_with_report(database_url, first, _matching_report(database_url, first))
    second = _ready_version(
        database_url, "second", document_version_id, chunking="v1-160-20"
    )
    activate_with_report(database_url, second, _matching_report(database_url, second))
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO document_versions
               (document_version_id, knowledge_base_id, document_id, version_number,
                content_sha256, source_file_bytes, source_path, status, created_at,
                parser_name, parser_version, parse_status)
               VALUES ('dv_first_v2', %s, 'doc_first', 2, %s, 12, '/tmp/first-v2.md',
                       'ready', now(), 'markdown', 'structured-1', 'ready')""",
            (KNOWLEDGE_BASE_ID, "b" * 64),
        )
        connection.execute(
            """UPDATE documents SET current_version_id='dv_first_v2'
               WHERE knowledge_base_id=%s AND document_id='doc_first'""",
            (KNOWLEDGE_BASE_ID,),
        )

    comparison = compare_index_version(database_url, KNOWLEDGE_BASE_ID, first)
    assert comparison["current_content"]["requires_confirmation"] is True
    assert comparison["current_content"]["diff"]["updated"] == 1
    assert comparison["current_content"]["retrievable_documents"] == 0
    with pytest.raises(AppError) as error:
        rollback_to_previous(database_url, KNOWLEDGE_BASE_ID)
    assert error.value.code == "INDEX_ROLLBACK_CONTENT_LAG_CONFIRMATION_REQUIRED"
    assert active_index_version_id(database_url, KNOWLEDGE_BASE_ID) == second

    result = rollback_to_previous(
        database_url,
        KNOWLEDGE_BASE_ID,
        confirm_content_lag=True,
    )

    assert result["active"] == first


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_rollback_without_previous_version_is_rejected() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    first = _ready_version(database_url, "first", document_version_id, chunking="v1-700-100")
    activate_with_report(database_url, first, _matching_report(database_url, first))
    with pytest.raises(AppError) as error:
        rollback_to_previous(database_url, KNOWLEDGE_BASE_ID)
    assert error.value.code == "INDEX_NO_PREVIOUS_VERSION"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_retire_rejects_a_version_still_in_use() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    first = _ready_version(database_url, "first", document_version_id, chunking="v1-700-100")
    activate_with_report(database_url, first, _matching_report(database_url, first))
    with pytest.raises(AppError) as error:
        cleanup_version(database_url, first)
    assert error.value.code == "INDEX_VERSION_IN_USE"
    assert _chunk_count(database_url, first) == 2


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_cleanup_deletes_chunks_drops_the_index_and_marks_cleaned() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    first = _ready_version(
        database_url, "first", document_version_id, chunking="v1-700-100", chunk_count=2
    )
    activate_with_report(database_url, first, _matching_report(database_url, first))
    second = _ready_version(
        database_url, "second", document_version_id, chunking="v1-160-20", chunk_count=3
    )
    activate_with_report(database_url, second, _matching_report(database_url, second))
    third = _ready_version(
        database_url, "third", document_version_id, chunking="v1-320-40", chunk_count=4
    )
    activate_with_report(database_url, third, _matching_report(database_url, third))
    assert _status(database_url, first) == "retired"
    assert _hnsw_index_name(database_url, first) is not None

    assert cleanup_version(database_url, first) == 2

    assert _chunk_count(database_url, first) == 0
    assert _hnsw_index_name(database_url, first) is None
    # 版本记录本身保留，仍是可审计的事实；但状态必须推进到 cleaned——分块已经删光，
    # 继续显示 retired 会让它看起来还能回滚回去，实际切过去就是空索引。
    assert _status(database_url, first) == "cleaned"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_previous_version_can_be_explicitly_retired_before_cleanup() -> None:
    """操作者可以明确放弃唯一回滚点，但 active / ready 都不能误退役。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    first = _ready_version(
        database_url, "first", document_version_id, chunking="v1-700-100"
    )
    activate_with_report(database_url, first, _matching_report(database_url, first))
    second = _ready_version(
        database_url, "second", document_version_id, chunking="v1-160-20"
    )
    activate_with_report(database_url, second, _matching_report(database_url, second))

    with pytest.raises(AppError) as scoped_error:
        retire_version(database_url, first, knowledge_base_id="kb_other")
    assert scoped_error.value.code == "INDEX_VERSION_NOT_FOUND"

    retired = retire_version(database_url, first)

    assert retired["index_version_id"] == first
    assert _status(database_url, first) == "retired"
    with pytest.raises(AppError) as error:
        retire_version(database_url, second)
    assert error.value.code == "INDEX_VERSION_NOT_RETIRABLE"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_retry_build_keeps_the_version_and_snapshot_but_opens_a_new_attempt() -> None:
    """重试 Build 不是创建新 Version；失败现场与冻结输入都要保留。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    index_version_id = _create(database_url)
    _add_chunks(database_url, index_version_id, document_version_id)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            first_build = ensure_index_build(
                connection,
                knowledge_base_id=KNOWLEDGE_BASE_ID,
                index_version_id=index_version_id,
            )
            connection.execute(
                "UPDATE index_builds SET status='failed' WHERE index_build_id=%s",
                (first_build,),
            )
            connection.execute(
                "UPDATE index_versions SET status='build_failed' WHERE index_version_id=%s",
                (index_version_id,),
            )
            snapshot_id = connection.execute(
                "SELECT document_snapshot_id FROM index_versions WHERE index_version_id=%s",
                (index_version_id,),
            ).fetchone()["document_snapshot_id"]

    result = retry_index_version_build(
        database_url,
        KNOWLEDGE_BASE_ID,
        index_version_id,
        requested_by="usr_admin",
    )

    assert result["index_version_id"] == index_version_id
    assert result["index_build_id"] != first_build
    assert result["queued"] == 1
    with psycopg.connect(database_url) as connection:
        version = connection.execute(
            "SELECT status, document_snapshot_id FROM index_versions WHERE index_version_id=%s",
            (index_version_id,),
        ).fetchone()
        attempts = connection.execute(
            "SELECT attempt_no, status FROM index_builds WHERE index_version_id=%s ORDER BY attempt_no",
            (index_version_id,),
        ).fetchall()
        chunks = connection.execute(
            "SELECT count(*) FROM chunks WHERE index_version_id=%s", (index_version_id,)
        ).fetchone()[0]
    assert version == ("building", snapshot_id)
    assert attempts == [(1, "failed"), (2, "building")]
    assert chunks == 0


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_cancel_build_stops_jobs_and_moves_the_version_to_build_failed() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    index_version_id = _create(database_url)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            index_build_id = ensure_index_build(
                connection,
                knowledge_base_id=KNOWLEDGE_BASE_ID,
                index_version_id=index_version_id,
            )
            operation_id = connection.execute(
                "SELECT operation_id FROM index_builds WHERE index_build_id=%s",
                (index_build_id,),
            ).fetchone()["operation_id"]
            connection.execute(
                """INSERT INTO index_jobs
                   (index_job_id, knowledge_base_id, data_source_id, document_version_id,
                    idempotency_key, status, job_type, rebuild_batch_id,
                    target_chunking_version, operation_id)
                   VALUES ('job_cancel',%s,%s,%s,'cancel-test','queued','rebuild',
                           'rbd_test','v1-700-100',%s)""",
                (KNOWLEDGE_BASE_ID, DATA_SOURCE_ID, document_version_id, operation_id),
            )

    result = cancel_index_version_build(
        database_url, KNOWLEDGE_BASE_ID, index_version_id, requested_by="usr_admin"
    )

    assert result["cancelled_jobs"] == 1
    with psycopg.connect(database_url) as connection:
        assert connection.execute(
            "SELECT status FROM index_versions WHERE index_version_id=%s", (index_version_id,)
        ).fetchone()[0] == "build_failed"
        assert connection.execute(
            "SELECT status FROM index_builds WHERE index_build_id=%s", (index_build_id,)
        ).fetchone()[0] == "cancelled"
        assert connection.execute(
            "SELECT status FROM index_jobs WHERE index_job_id='job_cancel'"
        ).fetchone()[0] == "cancelled"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_switch_and_rollback_are_audited(tmp_path: Path) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    audit = AuditRepository(tmp_path / "audit.json")
    document_version_id = _add_document(database_url, "first")
    first = _ready_version(database_url, "first", document_version_id, chunking="v1-700-100")
    activate_with_report(database_url, first, _matching_report(database_url, first), audit)
    second = _ready_version(database_url, "second", document_version_id, chunking="v1-160-20")
    activate_with_report(database_url, second, _matching_report(database_url, second), audit)
    rollback_to_previous(database_url, KNOWLEDGE_BASE_ID, audit)

    events = audit.list(offset=0, limit=10)
    assert [event["action"] for event in events] == [
        "index_version.rollback",
        "index_version.activate",
        "index_version.activate",
    ]
    assert [event["resource_id"] for event in events] == [first, second, first]
    assert {event["resource_type"] for event in events} == {"index_version"}
    assert {event["result"] for event in events} == {"success"}
    assert audit.verify()
