from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from backend.app.chunking import chunking_version, parse_chunking_version
from backend.app.config import Settings
from backend.app.database import apply_migrations
from backend.app.errors import AppError
from backend.app.postgres_documents import (
    IndexWorker,
    PostgresAsyncRAGService,
    chunking_inventory,
    enqueue_rebuild,
    rebuild_status,
)

KNOWLEDGE_BASE_ID = "kb_default"
# 每个段落都超过细粒度切分阈值，确保 700/100 与 160/20 产生不同的 chunks。
DOCUMENT_TEXT = "\n\n".join(f"第 {index} 段：" + "重建索引验证语料。" * 30 for index in range(1, 6))


class FakeEmbedder:
    model_name = "test/embedding"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class FailingEmbedder(FakeEmbedder):
    def encode(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding failed")


def _settings(tmp_path: Path, database_url: str, chunk_size: int, chunk_overlap: int) -> Settings:
    return Settings(
        database_url=database_url,
        upload_path=tmp_path / "uploads",
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        frontend_origin="http://localhost:5173",
    )


def _reset(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    apply_migrations(database_url)
    now = datetime.now(UTC)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO knowledge_bases
               (knowledge_base_id, name, name_normalized, description, is_default,
                created_at, updated_at)
               VALUES (%s, '默认知识库', '默认知识库', '', true, %s, %s)""",
            (KNOWLEDGE_BASE_ID, now, now),
        )


def _service(settings: Settings) -> PostgresAsyncRAGService:
    return PostgresAsyncRAGService(settings, FakeEmbedder(), None, None)


def _chunk_count(database_url: str) -> int:
    with psycopg.connect(database_url) as connection:
        return int(connection.execute("SELECT count(*) FROM chunks").fetchone()[0])


def _chunk_char_counts(database_url: str, index_version_id: str | None = None) -> list[int]:
    """分块长度。并存期间必须指定索引版本，否则会把两套切分配置的分块混在一起量。"""

    with psycopg.connect(database_url) as connection:
        if index_version_id is None:
            rows = connection.execute(
                "SELECT length(content) FROM chunks ORDER BY chunk_index"
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT length(content) FROM chunks WHERE index_version_id = %s
                   ORDER BY chunk_index""",
                (index_version_id,),
            ).fetchall()
        return [int(row[0]) for row in rows]


def _drain(worker: IndexWorker, limit: int = 20) -> int:
    processed = 0
    while processed < limit and worker.run_once():
        processed += 1
    return processed


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_rebuild_reindexes_current_versions_without_moving_the_version_pointer(
    tmp_path: Path,
) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    coarse = _settings(tmp_path, database_url, 700, 100)
    service = _service(coarse)
    service.index_document("guide.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    assert _drain(IndexWorker(coarse, FakeEmbedder())) == 1

    with psycopg.connect(database_url) as connection:
        before = connection.execute(
            """SELECT d.current_version_id, v.status, v.chunking_version
               FROM documents d JOIN document_versions v
                 ON v.document_version_id = d.current_version_id"""
        ).fetchone()
    coarse_chunks = _chunk_count(database_url)
    assert before[1] == "ready"
    assert before[2] == chunking_version(700, 100)
    assert chunking_inventory(database_url, KNOWLEDGE_BASE_ID) == {chunking_version(700, 100): 1}

    fine = _settings(tmp_path, database_url, 160, 20)
    target = chunking_version(160, 20)
    result = enqueue_rebuild(database_url, KNOWLEDGE_BASE_ID, target)
    assert result["queued"] == 1
    assert _drain(IndexWorker(fine, FakeEmbedder())) == 1

    with psycopg.connect(database_url) as connection:
        after = connection.execute(
            """SELECT d.current_version_id, v.status, v.chunking_version
               FROM documents d JOIN document_versions v
                 ON v.document_version_id = d.current_version_id"""
        ).fetchone()
        versions = int(
            connection.execute("SELECT count(*) FROM document_versions").fetchone()[0]
        )
    # 重建不产生新的文档版本，也不移动当前版本指针。
    assert after[0] == before[0]
    assert after[1] == "ready"
    # 切分配置归索引版本记录：新分块还在未放行的 building 版本里，文档版本不得谎称已是新配置。
    assert after[2] == chunking_version(700, 100)
    assert versions == 1
    # 新旧分块并存，总数因此增加而不是替换。
    assert _chunk_count(database_url) > coarse_chunks
    assert max(_chunk_char_counts(database_url, str(result["index_version_id"]))) <= 160
    assert chunking_inventory(database_url, KNOWLEDGE_BASE_ID) == {
        chunking_version(700, 100): 1,
        target: 1,
    }
    assert rebuild_status(database_url, str(result["batch_id"]))["pending"] == 0


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_rebuild_is_idempotent_and_resumable(tmp_path: Path) -> None:
    """已经处于目标配置的版本不再重复排队，因此中断后重复调用即可续跑。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url, 700, 100)
    service = _service(settings)
    for name in ("first.md", "second.md"):
        service.index_document(name, f"{name}\n\n{DOCUMENT_TEXT}".encode(), KNOWLEDGE_BASE_ID)
    assert _drain(IndexWorker(settings, FakeEmbedder())) == 2

    fine = _settings(tmp_path, database_url, 160, 20)
    target = chunking_version(160, 20)
    first_batch = enqueue_rebuild(database_url, KNOWLEDGE_BASE_ID, target)
    assert first_batch["queued"] == 2

    # 只处理一个任务就中断，模拟 Worker 中途退出。
    worker = IndexWorker(fine, FakeEmbedder())
    assert worker.run_once() is True

    # 队列里仍有未完成任务时不重复排队。
    assert enqueue_rebuild(database_url, KNOWLEDGE_BASE_ID, target)["queued"] == 0
    assert _drain(worker) == 1
    # 新版本已覆盖两篇文档，旧的 active 版本同样仍覆盖两篇——两套并存。
    assert chunking_inventory(database_url, KNOWLEDGE_BASE_ID) == {
        chunking_version(700, 100): 2,
        target: 2,
    }

    # 目标版本已覆盖全量文档后再次发起，不会产生任何任务：续跑判定改为按索引版本
    # 是否已覆盖该文档，而不是按文档版本的切分配置。
    assert enqueue_rebuild(database_url, KNOWLEDGE_BASE_ID, target)["queued"] == 0


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_failed_rebuild_keeps_the_document_searchable(tmp_path: Path) -> None:
    """重建失败时上一批分块仍然完好，文档状态不得被标记为失败。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url, 700, 100)
    service = _service(settings)
    service.index_document("guide.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    assert _drain(IndexWorker(settings, FakeEmbedder())) == 1
    healthy_chunks = _chunk_count(database_url)

    fine = _settings(tmp_path, database_url, 160, 20)
    target = chunking_version(160, 20)
    # 重试上限来自入队时写入任务行的 max_attempts，一次失败即进入终态。
    batch = enqueue_rebuild(database_url, KNOWLEDGE_BASE_ID, target, max_attempts=1)
    assert batch["queued"] == 1

    failing = IndexWorker(fine, FailingEmbedder())
    assert failing.run_once() is True

    with psycopg.connect(database_url) as connection:
        version = connection.execute(
            """SELECT v.status, v.chunking_version, v.failure_reason
               FROM documents d JOIN document_versions v
                 ON v.document_version_id = d.current_version_id"""
        ).fetchone()
    assert version[0] == "ready"
    assert version[1] == chunking_version(700, 100)
    assert version[2] is None
    assert _chunk_count(database_url) == healthy_chunks

    status = rebuild_status(database_url, str(batch["batch_id"]))
    assert status["counts"] == {"failed": 1}
    assert len(status["failures"]) == 1

    # 文档仍然可以被正常检索到，说明失败的重建没有破坏在线索引。
    assert service.list_documents(KNOWLEDGE_BASE_ID)[0].status == "ready"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_rebuild_skips_versions_with_active_jobs(tmp_path: Path) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url, 700, 100)
    service = _service(settings)
    service.index_document("guide.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)

    # 首次索引任务尚未执行，重建不得抢占同一版本。
    assert enqueue_rebuild(database_url, KNOWLEDGE_BASE_ID, chunking_version(160, 20))["queued"] == 0
    assert _drain(IndexWorker(settings, FakeEmbedder())) == 1
    assert enqueue_rebuild(database_url, KNOWLEDGE_BASE_ID, chunking_version(160, 20))["queued"] == 1


class _OtherModelEmbedder(FakeEmbedder):
    model_name = "test/other-embedding"


class _WiderEmbedder(FakeEmbedder):
    model_name = "test/embedding"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_switching_embedding_model_is_rejected_before_polluting_the_index(tmp_path: Path) -> None:
    """换模型必须在写入前被拦下，而不是等检索执行 <=> 时才报维度错。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url, 700, 100)
    service = _service(settings)
    service.index_document("guide.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    assert _drain(IndexWorker(settings, FakeEmbedder())) == 1
    healthy = _chunk_count(database_url)

    with psycopg.connect(database_url) as connection:
        registered = connection.execute(
            "SELECT embedding_model, embedding_dimension FROM index_settings WHERE singleton"
        ).fetchone()
    assert registered == ("test/embedding", 3)

    # 换成另一个模型：任务失败，但既有分块完好。
    service.index_document("second.md", f"second\n\n{DOCUMENT_TEXT}".encode(), KNOWLEDGE_BASE_ID)
    assert IndexWorker(settings, _OtherModelEmbedder()).run_once() is True
    assert _chunk_count(database_url) == healthy

    # 同名模型但维度变化同样要拦下。
    service.index_document("third.md", f"third\n\n{DOCUMENT_TEXT}".encode(), KNOWLEDGE_BASE_ID)
    assert IndexWorker(settings, _WiderEmbedder()).run_once() is True
    assert _chunk_count(database_url) == healthy

    with psycopg.connect(database_url) as connection:
        reasons = [
            str(row[0])
            for row in connection.execute(
                "SELECT failure_reason FROM index_jobs WHERE failure_reason IS NOT NULL"
            ).fetchall()
        ]
    # 失败原因记录的是 AppError 的消息文本，须点明双方模型与维度以便排查。
    assert len(reasons) == 2
    assert all("索引使用 test/embedding（3 维）" in reason for reason in reasons)
    assert any("test/other-embedding" in reason for reason in reasons)
    assert any("（4 维）" in reason for reason in reasons)


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_rebuild_rejects_unknown_knowledge_base(tmp_path: Path) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    with pytest.raises(AppError):
        enqueue_rebuild(database_url, "kb_missing", chunking_version(700, 100))


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_concurrent_rebuild_start_enqueues_one_active_job(tmp_path: Path) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url, 700, 100)
    _service(settings).index_document("guide.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    assert _drain(IndexWorker(settings, FakeEmbedder())) == 1

    targets = (chunking_version(160, 20), chunking_version(180, 30))

    def _start(target: str) -> dict[str, object] | str:
        try:
            return enqueue_rebuild(database_url, KNOWLEDGE_BASE_ID, target)
        except AppError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(_start, targets))

    # 目标配置不同的并发重建只能有一个成立，另一个被明确拒绝而不是悄悄产生
    # 两套配置混合的索引。
    accepted = [item for item in results if isinstance(item, dict)]
    rejected = [item for item in results if item == "REBUILD_IN_PROGRESS"]
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert int(accepted[0]["queued"]) == 1
    with psycopg.connect(database_url) as connection:
        assert connection.execute(
            """SELECT count(*) FROM index_jobs
               WHERE job_type = 'rebuild' AND status IN ('queued', 'running')"""
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM index_versions WHERE status = 'building'"
        ).fetchone()[0] == 1


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_file_update_during_rebuild_keeps_the_new_version_current(tmp_path: Path) -> None:
    """旧版本重建与新版本索引可并存，旧任务不得把指针切回去。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    coarse = _settings(tmp_path, database_url, 700, 100)
    service = _service(coarse)
    first = service.index_document("guide.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    assert _drain(IndexWorker(coarse, FakeEmbedder())) == 1

    target = chunking_version(160, 20)
    assert enqueue_rebuild(database_url, KNOWLEDGE_BASE_ID, target)["queued"] == 1
    updated = service.index_document(
        "guide.md", (DOCUMENT_TEXT + "\n\n新增版本内容。").encode(), KNOWLEDGE_BASE_ID
    )
    assert updated.document_id == first.document_id

    # 队列按创建时间处理：先重建旧版本，再索引上传产生的新版本。
    assert _drain(IndexWorker(coarse, FakeEmbedder())) == 2
    with psycopg.connect(database_url) as connection:
        current = connection.execute(
            """SELECT d.current_version_id, v.version_number, v.chunking_version
               FROM documents d JOIN document_versions v
                 ON v.document_version_id = d.current_version_id"""
        ).fetchone()
        statuses = connection.execute(
            "SELECT version_number, status FROM document_versions ORDER BY version_number"
        ).fetchall()
    assert current[1:] == (2, chunking_version(700, 100))
    assert statuses == [(1, "superseded"), (2, "ready")]


def test_chunking_version_rejects_an_algorithm_not_implemented_by_this_worker() -> None:
    with pytest.raises(ValueError, match="is not supported"):
        parse_chunking_version("v2-700-100")


def _insert_extra_index_version(database_url: str, status: str) -> str:
    """手工造一个同知识库的另一索引版本及其分块，用于验证读路径的过滤边界。"""

    index_version_id = f"iv_extra_{status}"
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO index_versions
               (index_version_id, knowledge_base_id, status, chunking_version, parser_version,
                embedding_model, embedding_dimension, config_fingerprint, evaluation_report_id)
               VALUES (%s, %s, %s, 'v1-160-20', 'structured-1', 'test/embedding', 3, %s, NULL)""",
            (index_version_id, KNOWLEDGE_BASE_ID, status, "c" * 64),
        )
        rows = connection.execute(
            """SELECT document_version_id, knowledge_base_id, chunk_index, content, metadata
               FROM chunks ORDER BY chunk_index"""
        ).fetchall()
        for row in rows:
            connection.execute(
                """INSERT INTO chunks
                   (chunk_id, document_version_id, index_version_id, knowledge_base_id,
                    chunk_index, content, metadata, embedding, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, '[0.1,0.2,0.3]', now())""",
                (
                    f"{index_version_id}:{row[2]:05d}",
                    row[0],
                    index_version_id,
                    row[1],
                    row[2],
                    row[3],
                    Jsonb(row[4]),
                ),
            )
    return index_version_id


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_read_paths_only_see_active_index_version(tmp_path: Path) -> None:
    """未放行的索引版本对用户完全不可见。

    V5-4 之前重建是原地替换，重建跑到一半时知识库里同时存在两套切分策略的分块，
    而读路径只按 current_version_id 取，用户此刻的检索结果来自两套配置的混合。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url, 700, 100)
    service = _service(settings)
    service.index_document("profile.md", DOCUMENT_TEXT.encode("utf-8"), KNOWLEDGE_BASE_ID)
    IndexWorker(settings, FakeEmbedder()).run_once()

    active_chunks = service.store.load_current_chunks(KNOWLEDGE_BASE_ID)
    active_documents = service.list_documents(KNOWLEDGE_BASE_ID)
    total_before = _chunk_count(database_url)

    _insert_extra_index_version(database_url, "building")

    # 库里的分块总数翻倍，但用户可见的一切都不能变
    assert _chunk_count(database_url) == total_before * 2
    assert len(service.store.load_current_chunks(KNOWLEDGE_BASE_ID)) == len(active_chunks)
    assert service.list_documents(KNOWLEDGE_BASE_ID)[0].chunk_count == active_documents[0].chunk_count
    candidates = service.retrieve_candidates("重建索引验证语料", [0.1, 0.2, 0.3], 50, KNOWLEDGE_BASE_ID)
    assert all(not item.chunk_id.startswith("iv_extra") for item in candidates)


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_chunk_fingerprint_changes_when_active_index_version_switches(tmp_path: Path) -> None:
    """切换索引版本必须让词法索引指纹变化。

    LexicalIndexCache 靠这个指纹跨进程判断倒排是否过期；指纹不变则 API 进程继续用旧
    索引版本的 BM25 倒排，混合检索会命中已被切走的分块。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url, 700, 100)
    service = _service(settings)
    service.index_document("profile.md", DOCUMENT_TEXT.encode("utf-8"), KNOWLEDGE_BASE_ID)
    IndexWorker(settings, FakeEmbedder()).run_once()

    # 先把另一版本的分块写进库，再取指纹：这样切换指针时分块集合完全不变，
    # 指纹若仍然变化，只能是因为 active 索引版本被计入，而不是因为行数或时间戳变了。
    other = _insert_extra_index_version(database_url, "ready")
    before = service.store.chunk_fingerprint(KNOWLEDGE_BASE_ID)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id=%s WHERE knowledge_base_id=%s",
            (other, KNOWLEDGE_BASE_ID),
        )

    assert service.store.chunk_fingerprint(KNOWLEDGE_BASE_ID) != before


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_list_index_versions_reports_both_versions_during_rebuild(tmp_path: Path) -> None:
    """并存期间两个版本都要能被操作者看到，否则无从判断该切还是该清理。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url, 700, 100)
    service = _service(settings)
    service.index_document("profile.md", DOCUMENT_TEXT.encode("utf-8"), KNOWLEDGE_BASE_ID)
    IndexWorker(settings, FakeEmbedder()).run_once()

    result = enqueue_rebuild(database_url, KNOWLEDGE_BASE_ID, chunking_version(160, 20))
    fine = _settings(tmp_path, database_url, 160, 20)
    _drain(IndexWorker(fine, FakeEmbedder()))

    versions = service.list_index_versions(KNOWLEDGE_BASE_ID)
    by_status = {str(item["status"]): item for item in versions}
    assert set(by_status) == {"active", "building"}
    assert by_status["building"]["index_version_id"] == result["index_version_id"]
    assert by_status["building"]["chunking_version"] == chunking_version(160, 20)
    assert by_status["active"]["chunking_version"] == chunking_version(700, 100)
    # 首个版本用固定标记放行，不参与指纹比对
    assert by_status["active"]["evaluation_report_id"] == "initial-index"
    assert by_status["building"]["evaluation_report_id"] is None
