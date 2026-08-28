"""S3 兼容对象存储的端到端增量同步。

与本地目录同构的场景在这里重跑一遍，验证的是「同步框架对不同连接器行为一致」——
这正是 V5-6 定连接器协议时无法验证、要等第二个实现才能确认的部分。

S3 特有的分段上传 ETag 行为在连接器层单独覆盖（test_connectors.py 的
test_s3_multipart_upload_changes_etag_for_identical_content）：那里直接断言同一内容
换 part_size 后 ETag 必变。端到端不再重复组合验证——「version 变化触发重新索引」
已由下面的改内容场景覆盖，为组合这两条去上传十几 MB 并真索引几千个分块不划算。
"""

from __future__ import annotations

import hashlib
import io
import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from backend.app.config import Settings
from backend.app.data_source_sync import enqueue_sync
from backend.app.database import apply_migrations
from backend.app.postgres_documents import IndexWorker

KNOWLEDGE_BASE_ID = "kb_default"
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "probe")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "probe12345")

requires_stack = pytest.mark.skipif(
    not MINIO_ENDPOINT or not os.getenv("TEST_DATABASE_URL"),
    reason="需要 MinIO 与 PostgreSQL",
)


class _FakeEmbedder:
    model_name = "test/embedding"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


def _client():
    from minio import Minio

    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )


@pytest.fixture
def bucket(request: pytest.FixtureRequest) -> Iterator[str]:
    """每个测试独立的桶，用完删掉。

    桶名用 sha256 而不是内置 ``hash()``：后者对 str 带 PYTHONHASHSEED 随机化，每次
    运行 pytest 都会换一批桶名，于是桶只增不减——实测在开发机上攒了九十多个。
    """

    client = _client()
    name = f"sync-{hashlib.sha256(request.node.name.encode()).hexdigest()[:12]}"
    if not client.bucket_exists(name):
        client.make_bucket(name)
    for item in client.list_objects(name, recursive=True):
        client.remove_object(name, item.object_name)
    yield name
    for item in client.list_objects(name, recursive=True):
        client.remove_object(name, item.object_name)
    client.remove_bucket(name)


def _put(bucket_name: str, key: str, content: bytes, part_size: int = 0) -> None:
    client = _client()
    if part_size:
        client.put_object(
            bucket_name, key, io.BytesIO(content), len(content), part_size=part_size
        )
    else:
        client.put_object(bucket_name, key, io.BytesIO(content), len(content))


def _remove(bucket_name: str, key: str) -> None:
    _client().remove_object(bucket_name, key)


def _document(name: str) -> bytes:
    return f"# {name}\n\n{name} 的正文内容。".encode() + "正文段落。\n\n".encode() * 20


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


def _settings(tmp_path: Path, database_url: str, max_upload_mb: int = 15) -> Settings:
    return Settings(
        database_url=database_url,
        upload_path=tmp_path / "uploads",
        chunk_size=200,
        chunk_overlap=0,
        frontend_origin="http://localhost:5173",
        max_upload_mb=max_upload_mb,
        max_request_body_mb=max_upload_mb + 1,
    )


def _create_source(database_url: str, bucket_name: str, prefix: str = "docs/") -> str:
    data_source_id = "ds_s3"
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO data_sources
               (data_source_id, knowledge_base_id, source_type, name, configuration,
                created_at, updated_at)
               VALUES (%s, %s, 'object_storage', '对象存储', %s, now(), now())""",
            (
                data_source_id,
                KNOWLEDGE_BASE_ID,
                Jsonb(
                    {
                        "endpoint": MINIO_ENDPOINT,
                        "bucket": bucket_name,
                        "prefix": prefix,
                        "secure": False,
                        "credential_env": "S3_SYNC_TEST",
                    }
                ),
            ),
        )
    return data_source_id


def _sync(settings: Settings, database_url: str, data_source_id: str) -> None:
    enqueue_sync(database_url, data_source_id)
    worker = IndexWorker(settings, _FakeEmbedder())
    processed = 0
    while processed < 60 and worker.run_once():
        processed += 1


def _count(database_url: str, sql: str) -> int:
    with psycopg.connect(database_url) as connection:
        return int(connection.execute(sql).fetchone()[0])


def _documents(database_url: str) -> int:
    return _count(database_url, "SELECT count(*) FROM documents")


def _searchable(database_url: str) -> int:
    return _count(
        database_url,
        """SELECT count(*) FROM documents
           WHERE COALESCE(metadata->>'retrieval_status', 'searchable') = 'searchable'""",
    )


def _index_jobs(database_url: str) -> int:
    return _count(database_url, "SELECT count(*) FROM index_jobs WHERE job_type = 'index'")


def _sync_status(database_url: str, data_source_id: str) -> tuple[str, str | None]:
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """SELECT last_sync_status, sync_failure_reason FROM data_sources
               WHERE data_source_id = %s""",
            (data_source_id,),
        ).fetchone()
    return str(row[0]), row[1]


@pytest.fixture(autouse=True)
def _credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_SYNC_TEST_ACCESS_KEY", MINIO_ACCESS_KEY)
    monkeypatch.setenv("S3_SYNC_TEST_SECRET_KEY", MINIO_SECRET_KEY)


@requires_stack
def test_s3_sync_handles_add_update_delete_and_noop(tmp_path: Path, bucket: str) -> None:
    """与本地目录同构的增量语义，验证同步框架对两个连接器行为一致。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    for name in ("a.md", "b.md", "c.md"):
        _put(bucket, f"docs/{name}", _document(name))
    _put(bucket, "other/ignored.md", _document("ignored"))
    source_id = _create_source(database_url, bucket)
    settings = _settings(tmp_path, database_url)

    _sync(settings, database_url, source_id)
    assert _documents(database_url) == 3, "prefix 之外的对象不得被同步"
    assert _searchable(database_url) == 3
    assert _sync_status(database_url, source_id)[0] == "succeeded"

    baseline = _index_jobs(database_url)
    _sync(settings, database_url, source_id)
    assert _index_jobs(database_url) == baseline, "无变化的同步不得产生任务"

    _put(bucket, "docs/a.md", _document("a.md") + "追加的新内容。".encode())
    _sync(settings, database_url, source_id)
    assert _index_jobs(database_url) == baseline + 1, "只重建内容变化的那一个"

    _remove(bucket, "docs/c.md")
    _sync(settings, database_url, source_id)
    assert _documents(database_url) == 3, "软删除不得删除文档记录"
    assert _searchable(database_url) == 2

    _put(bucket, "docs/c.md", _document("c.md"))
    _sync(settings, database_url, source_id)
    assert _searchable(database_url) == 3


@requires_stack
def test_s3_sync_skips_oversized_objects_entirely(tmp_path: Path, bucket: str) -> None:
    """超限对象不下载、不入队、不进对象记录，同步整体仍然成功。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    _put(bucket, "docs/ok.md", _document("ok"))
    _put(bucket, "docs/huge.md", b"x" * (3 * 1024 * 1024))
    source_id = _create_source(database_url, bucket)

    _sync(_settings(tmp_path, database_url, max_upload_mb=1), database_url, source_id)

    assert _documents(database_url) == 1
    assert _sync_status(database_url, source_id)[0] == "succeeded"
    with psycopg.connect(database_url) as connection:
        keys = [
            row[0]
            for row in connection.execute(
                "SELECT object_key FROM data_source_objects ORDER BY object_key"
            ).fetchall()
        ]
    assert keys == ["ok.md"]


@requires_stack
def test_s3_sync_aborts_on_circuit_breaker_without_writing(tmp_path: Path, bucket: str) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    for index in range(6):
        _put(bucket, f"docs/doc{index}.md", _document(f"doc{index}"))
    source_id = _create_source(database_url, bucket)
    settings = _settings(tmp_path, database_url)
    _sync(settings, database_url, source_id)
    documents_before = _documents(database_url)
    searchable_before = _searchable(database_url)
    jobs_before = _index_jobs(database_url)

    for index in range(5):
        _remove(bucket, f"docs/doc{index}.md")
    _sync(settings, database_url, source_id)

    status, reason = _sync_status(database_url, source_id)
    assert status == "aborted"
    assert reason is not None and "doc0.md" in reason
    assert _documents(database_url) == documents_before
    assert _searchable(database_url) == searchable_before
    assert _index_jobs(database_url) == jobs_before, "熔断时不得执行新增"


@requires_stack
def test_s3_sync_fails_loudly_when_bucket_is_missing(tmp_path: Path) -> None:
    """桶不存在不得退化成空清单——那会被差异计算判成全部删除。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    source_id = _create_source(database_url, "bucket-that-does-not-exist")

    _sync(_settings(tmp_path, database_url), database_url, source_id)

    status, reason = _sync_status(database_url, source_id)
    assert status == "failed"
    assert reason is not None and "SOURCE_ROOT_UNAVAILABLE" in reason
