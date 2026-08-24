from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

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


def _chunk_char_counts(database_url: str) -> list[int]:
    with psycopg.connect(database_url) as connection:
        return [
            int(row[0])
            for row in connection.execute(
                "SELECT length(content) FROM chunks ORDER BY chunk_index"
            ).fetchall()
        ]


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
    # 重建只替换分块，不产生新版本，也不移动当前版本指针。
    assert after[0] == before[0]
    assert after[1] == "ready"
    assert after[2] == target
    assert versions == 1
    assert _chunk_count(database_url) > coarse_chunks
    assert max(_chunk_char_counts(database_url)) <= 160
    assert chunking_inventory(database_url, KNOWLEDGE_BASE_ID) == {target: 1}
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
    assert chunking_inventory(database_url, KNOWLEDGE_BASE_ID) == {target: 2}

    # 全部达到目标配置后再次发起，不会产生任何任务。
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
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda target: enqueue_rebuild(database_url, KNOWLEDGE_BASE_ID, target),
                targets,
            )
        )

    assert sorted(int(result["queued"]) for result in results) == [0, 1]
    with psycopg.connect(database_url) as connection:
        assert connection.execute(
            """SELECT count(*) FROM index_jobs
               WHERE job_type = 'rebuild' AND status IN ('queued', 'running')"""
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
