"""数据源同步的差异计算、熔断与端到端同步。

差异计算与熔断判定都是纯函数，不碰数据库——因此它们能在没有 PostgreSQL 的环境里被测到。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from backend.app.config import Settings
from backend.app.connectors import SourceObject
from backend.app.data_source_sync import (
    SyncDiff,
    check_delete_circuit_breaker,
    compute_diff,
    enqueue_sync,
)
from backend.app.database import apply_migrations
from backend.app.errors import AppError
from backend.app.postgres_documents import IndexWorker


def _remote(key: str, version: str) -> SourceObject:
    return SourceObject(key=key, version=version, size=10, modified_at=None)


def test_compute_diff_classifies_three_kinds_of_change() -> None:
    remote = [
        _remote("keep.md", "v1"),       # 两边一致
        _remote("edit.md", "v2-new"),   # version 变了
        _remote("new.md", "v3"),        # 本地没有
    ]
    known = {"keep.md": "v1", "edit.md": "v2-old", "gone.md": "v4"}

    diff = compute_diff(remote, known)

    assert [item.key for item in diff.added] == ["new.md"]
    assert [item.key for item in diff.updated] == ["edit.md"]
    assert diff.deleted == ["gone.md"]


def test_compute_diff_on_first_sync_treats_everything_as_added() -> None:
    diff = compute_diff([_remote("a.md", "v1"), _remote("b.md", "v2")], {})

    assert [item.key for item in diff.added] == ["a.md", "b.md"]
    assert diff.updated == []
    assert diff.deleted == []


def test_compute_diff_with_no_change_is_empty() -> None:
    """无变化必须产出全空的差异，否则同步会做无谓的重新索引。"""

    remote = [_remote("a.md", "v1")]

    diff = compute_diff(remote, {"a.md": "v1"})

    assert diff.added == [] and diff.updated == [] and diff.deleted == []
    assert not diff.has_changes()


def test_deleted_keys_are_sorted_for_stable_reporting() -> None:
    """删除清单会进熔断的错误信息，顺序必须稳定才能复现和比对。"""

    diff = compute_diff([], {"b.md": "v1", "a.md": "v1", "c.md": "v1"})

    assert diff.deleted == ["a.md", "b.md", "c.md"]


def test_circuit_breaker_trips_past_threshold() -> None:
    """删除比例超阈值即中止。挡的是根目录配错被当成「全部删除」。"""

    diff = SyncDiff(added=[], updated=[], deleted=["a", "b", "c", "d"])

    with pytest.raises(AppError) as error:
        check_delete_circuit_breaker(diff, known_total=5, threshold_percent=30)

    assert error.value.code == "SYNC_DELETE_CIRCUIT_BREAKER"
    assert "a" in error.value.message, "错误信息必须带待删清单，否则操作者无从判断"


def test_circuit_breaker_ignores_small_absolute_deletions() -> None:
    """比例超了但绝对量很小时不拦。

    纯比例阈值在小知识库上会把日常操作全拦下：3 份文档删 1 份就是 33%，
    10 份删 4 份就是 40%。一个部门二十来份手册的知识库在企业里很常见。
    """

    diff = SyncDiff(added=[], updated=[], deleted=["a"])

    check_delete_circuit_breaker(diff, known_total=3, threshold_percent=30)


def test_circuit_breaker_still_catches_small_base_wipeout() -> None:
    """小知识库被整体清空时仍要拦住——绝对下限不能变成漏网口。"""

    known = {f"doc{index}.md": "v1" for index in range(8)}
    diff = compute_diff([], known)

    with pytest.raises(AppError) as error:
        check_delete_circuit_breaker(diff, known_total=len(known), threshold_percent=30)

    assert error.value.code == "SYNC_DELETE_CIRCUIT_BREAKER"


def test_circuit_breaker_allows_deletion_at_or_below_threshold() -> None:
    """恰好等于阈值不触发——阈值的语义是「超过」才拦。"""

    diff = SyncDiff(added=[], updated=[], deleted=[f"doc{index}" for index in range(6)])

    check_delete_circuit_breaker(diff, known_total=20, threshold_percent=30)


def test_circuit_breaker_skips_first_sync() -> None:
    """首次同步没有可删的东西，不做判定。"""

    diff = SyncDiff(added=[_remote("a.md", "v1")], updated=[], deleted=[])

    check_delete_circuit_breaker(diff, known_total=0, threshold_percent=30)


def test_circuit_breaker_catches_wholesale_wipe() -> None:
    """根目录被误改或挂载点掉了，列举结果几乎为空——这是熔断存在的首要理由。"""

    known = {f"doc-{index}.md": "v1" for index in range(20)}
    diff = compute_diff([], known)

    with pytest.raises(AppError) as error:
        check_delete_circuit_breaker(diff, known_total=len(known), threshold_percent=30)

    assert error.value.code == "SYNC_DELETE_CIRCUIT_BREAKER"
    assert "20/20" in error.value.message


# --- 以下需要 PostgreSQL ---

KNOWLEDGE_BASE_ID = "kb_default"


class _FakeEmbedder:
    model_name = "test/embedding"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


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


def _settings(tmp_path: Path, database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        upload_path=tmp_path / "uploads",
        chunk_size=200,
        chunk_overlap=0,
        frontend_origin="http://localhost:5173",
    )


def _create_directory_source(database_url: str, root: Path) -> str:
    data_source_id = "ds_dir"
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO data_sources
               (data_source_id, knowledge_base_id, source_type, name, configuration,
                created_at, updated_at)
               VALUES (%s, %s, 'local_directory', '手册目录', %s, now(), now())""",
            (data_source_id, KNOWLEDGE_BASE_ID, Jsonb({
                "root": str(root), "include_suffixes": [".md"],
            })),
        )
    return data_source_id


def _run_full_sync(settings: Settings, database_url: str, data_source_id: str) -> None:
    """入队一次同步并把队列跑空（sync 任务会为每个变化对象再入队 index 任务）。"""

    enqueue_sync(database_url, data_source_id)
    worker = IndexWorker(settings, _FakeEmbedder())
    processed = 0
    while processed < 50 and worker.run_once():
        processed += 1


def _count(database_url: str, sql: str) -> int:
    with psycopg.connect(database_url) as connection:
        return int(connection.execute(sql).fetchone()[0])


def _document_count(database_url: str) -> int:
    return _count(database_url, "SELECT count(*) FROM documents")


def _searchable_count(database_url: str) -> int:
    return _count(
        database_url,
        """SELECT count(*) FROM documents
           WHERE COALESCE(metadata->>'retrieval_status', 'searchable') = 'searchable'""",
    )


def _index_job_count(database_url: str) -> int:
    return _count(database_url, "SELECT count(*) FROM index_jobs WHERE job_type = 'index'")


def _sync_state(database_url: str, data_source_id: str) -> tuple[str, str | None]:
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            "SELECT last_sync_status, sync_failure_reason FROM data_sources WHERE data_source_id=%s",
            (data_source_id,),
        ).fetchone()
    return str(row[0]), row[1]


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_sync_handles_add_update_delete_and_noop(tmp_path: Path) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    names = ("a.md", "b.md", "c.md", "d.md", "e.md", "f.md")
    for name in names:
        (root / name).write_text(f"# {name}\n\n{name} 的正文内容。" * 20, encoding="utf-8")
    source_id = _create_directory_source(database_url, root)
    settings = _settings(tmp_path, database_url)

    # 首次同步：六份全部索引
    _run_full_sync(settings, database_url, source_id)
    assert _document_count(database_url) == 6
    assert _searchable_count(database_url) == 6
    assert _sync_state(database_url, source_id)[0] == "succeeded"

    # 无变化再同步：不得产生任何索引任务
    baseline = _index_job_count(database_url)
    _run_full_sync(settings, database_url, source_id)
    assert _index_job_count(database_url) == baseline, "无变化的同步不得产生任务"

    # touch 全部文件：version 只跟内容有关，仍然零任务
    for name in names:
        os.utime(root / name, (0, 0))
    _run_full_sync(settings, database_url, source_id)
    assert _index_job_count(database_url) == baseline, "mtime 变化不得触发重新索引"

    # 改一个文件：只重建那一个
    (root / "a.md").write_text("# a.md\n\n改过的正文内容。" * 20, encoding="utf-8")
    _run_full_sync(settings, database_url, source_id)
    assert _index_job_count(database_url) == baseline + 1

    # 删一个：软删除，文档记录保留但不可检索（1 个不超过绝对下限，不触发熔断）
    (root / "c.md").unlink()
    _run_full_sync(settings, database_url, source_id)
    assert _document_count(database_url) == 6, "软删除不得删除文档记录"
    assert _searchable_count(database_url) == 5

    # 恢复：内容未变，回到可检索且不重新索引
    (root / "c.md").write_text("# c.md\n\nc.md 的正文内容。" * 20, encoding="utf-8")
    _run_full_sync(settings, database_url, source_id)
    assert _searchable_count(database_url) == 6


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_sync_aborts_without_writing_when_circuit_breaker_trips(tmp_path: Path) -> None:
    """熔断中止时数据库不得有任何变更——这是它存在的全部意义。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    for index in range(5):
        (root / f"doc{index}.md").write_text(f"# doc{index}\n\n正文。" * 20, encoding="utf-8")
    source_id = _create_directory_source(database_url, root)
    settings = _settings(tmp_path, database_url)
    _run_full_sync(settings, database_url, source_id)
    documents_before = _document_count(database_url)
    searchable_before = _searchable_count(database_url)
    jobs_before = _index_job_count(database_url)

    # 删掉 4/5（80% > 30%），并新增一个——熔断时新增也不得执行
    for index in range(4):
        (root / f"doc{index}.md").unlink()
    (root / "new.md").write_text("# new\n\n新文件。" * 20, encoding="utf-8")
    _run_full_sync(settings, database_url, source_id)

    status, reason = _sync_state(database_url, source_id)
    assert status == "aborted"
    assert reason is not None and "doc0.md" in reason
    assert _document_count(database_url) == documents_before
    assert _searchable_count(database_url) == searchable_before
    assert _index_job_count(database_url) == jobs_before, "熔断时不得执行新增"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_missing_root_fails_the_sync_without_deleting_anything(tmp_path: Path) -> None:
    """挂载点掉了不能被当成「全部删除」。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "a.md").write_text("# a\n\n正文。" * 20, encoding="utf-8")
    source_id = _create_directory_source(database_url, root)
    settings = _settings(tmp_path, database_url)
    _run_full_sync(settings, database_url, source_id)
    assert _searchable_count(database_url) == 1

    shutil.rmtree(root)
    _run_full_sync(settings, database_url, source_id)

    status, reason = _sync_state(database_url, source_id)
    assert status == "failed"
    assert reason is not None and "SOURCE_ROOT_UNAVAILABLE" in reason
    assert _searchable_count(database_url) == 1, "根目录不可用不得导致软删除"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_concurrent_sync_is_rejected(tmp_path: Path) -> None:
    """同一数据源同时只允许一个活动同步任务，由数据库唯一索引保证。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "a.md").write_text("# a\n\n正文。" * 20, encoding="utf-8")
    source_id = _create_directory_source(database_url, root)

    enqueue_sync(database_url, source_id)

    with pytest.raises(AppError) as error:
        enqueue_sync(database_url, source_id)
    assert error.value.code == "SYNC_ALREADY_RUNNING"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_disabled_source_cannot_start_sync(tmp_path: Path) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    source_id = _create_directory_source(database_url, root)
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "UPDATE data_sources SET enabled=false WHERE data_source_id=%s", (source_id,)
        )

    with pytest.raises(AppError) as error:
        enqueue_sync(database_url, source_id)

    assert error.value.code == "DATA_SOURCE_DISABLED"


class _FailOnKeyEmbedder(_FakeEmbedder):
    """只让指定关键字的文档索引失败，模拟单个文档解析或嵌入失败。"""

    def __init__(self, keyword: str):
        self.keyword = keyword

    def encode(self, texts: list[str]) -> list[list[float]]:
        if any(self.keyword in text for text in texts):
            raise RuntimeError(f"embedding failed for {self.keyword}")
        return super().encode(texts)


def _drain_with(settings: Settings, embedder: object, limit: int = 60) -> None:
    worker = IndexWorker(settings, embedder)
    processed = 0
    while processed < limit and worker.run_once():
        processed += 1


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_sync_retries_objects_whose_indexing_failed(tmp_path: Path) -> None:
    """索引失败的对象必须在后续同步里被重试，故障恢复后自动补齐。

    对象记录是在 index_document 返回后就写入的，而那时索引只是入队。若不把「未 ready」
    的对象排除出「已知」，下次同步会把它当成无变化而永久跳过——文档在列表里一直显示
    失败，重跑同步毫无反应。而且重试不能走 index_document：它对相同 content_sha256 的
    既有版本会幂等短路，必须走 reprocess_version。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "good.md").write_text("# good\n\n正常文档。" * 20, encoding="utf-8")
    (root / "bad.md").write_text("# bad\n\n会失败的文档。" * 20, encoding="utf-8")
    source_id = _create_directory_source(database_url, root)
    settings = _settings(tmp_path, database_url).model_copy(
        update={"index_job_max_attempts": 1}
    )

    # 第一次：bad.md 索引失败
    enqueue_sync(database_url, source_id)
    _drain_with(settings, _FailOnKeyEmbedder("会失败"))
    assert _count(database_url, "SELECT count(*) FROM document_versions WHERE status='failed'") == 1
    after_first = _index_job_count(database_url)

    # 第二次仍然失败，但必须真的重试过（任务数增加）
    enqueue_sync(database_url, source_id)
    _drain_with(settings, _FailOnKeyEmbedder("会失败"))
    assert _index_job_count(database_url) > after_first, "失败的对象必须被重试，不能永久跳过"

    # 第三次故障恢复：自动补齐，不需要人工干预
    enqueue_sync(database_url, source_id)
    _drain_with(settings, _FakeEmbedder())

    assert _count(database_url, "SELECT count(*) FROM document_versions WHERE status='failed'") == 0
    assert _searchable_count(database_url) == 2


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_oversized_objects_never_enter_the_diff(tmp_path: Path) -> None:
    """超限对象不入队、不软删、不进对象记录，同步整体仍然成功。

    同步走 index_document，绕过了 API 上传路径的 validate_upload，所以大小限制必须
    在同步侧自己做，否则桶里或目录里一个大文件就能打死 Worker。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "ok.md").write_text("# ok\n\n正文内容。" * 20, encoding="utf-8")
    (root / "huge.md").write_bytes(b"x" * (3 * 1024 * 1024))
    source_id = _create_directory_source(database_url, root)
    settings = _settings(tmp_path, database_url).model_copy(update={"max_upload_mb": 1})

    _run_full_sync(settings, database_url, source_id)

    assert _document_count(database_url) == 1
    assert _sync_state(database_url, source_id)[0] == "succeeded"
    with psycopg.connect(database_url) as connection:
        keys = [
            row[0]
            for row in connection.execute(
                "SELECT object_key FROM data_source_objects ORDER BY object_key"
            ).fetchall()
        ]
    assert keys == ["ok.md"], "超限对象不得进入对象记录"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_skipped_objects_are_reported_in_the_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """跳过必须留下可查的记录，否则运维无从知道那份文档为什么搜不到。

    跳过对同步结果是"成功"，对提问的人是"这份资料不在库里"。这两者之间只有日志。
    run_sync 的返回值里带着 skipped，但 IndexWorker 调用它时不接返回值——没有日志
    就等于这个字段只存在于代码里。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "ok.md").write_text("# ok\n\n正文内容。" * 20, encoding="utf-8")
    (root / "huge.md").write_bytes(b"x" * (3 * 1024 * 1024))
    source_id = _create_directory_source(database_url, root)
    settings = _settings(tmp_path, database_url).model_copy(update={"max_upload_mb": 1})

    with caplog.at_level(logging.INFO):
        _run_full_sync(settings, database_url, source_id)

    skipped_events = [
        record.message
        for record in caplog.records
        if "data_source.object_skipped" in record.message
    ]
    assert len(skipped_events) == 1, "每个被跳过的对象都要留一条记录"
    assert "huge.md" in skipped_events[0], "记录里必须能看出是哪个对象"
    assert "3145728" in skipped_events[0], "记录里必须能看出实际大小，才能判断该放宽还是该拆分"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_object_storage_source_requires_credential_env(tmp_path: Path) -> None:
    """对象存储数据源必须配置 credential_env，凭据本身绝不进数据库。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO data_sources
               (data_source_id, knowledge_base_id, source_type, name, configuration,
                created_at, updated_at)
               VALUES ('ds_s3', %s, 'object_storage', '对象存储', %s, now(), now())""",
            (KNOWLEDGE_BASE_ID, Jsonb({"endpoint": "127.0.0.1:9000", "bucket": "docs"})),
        )
    settings = _settings(tmp_path, database_url)

    _run_full_sync(settings, database_url, "ds_s3")

    status, reason = _sync_state(database_url, "ds_s3")
    assert status == "failed"
    assert reason is not None and "SOURCE_CONFIGURATION_INVALID" in reason


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_object_storage_source_fails_loudly_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺凭据必须明确失败，不回退匿名访问。

    回退会让配置错误表现成「桶是空的」，而空清单会被差异计算判成全部删除。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    monkeypatch.delenv("SYNC_PROBE_ACCESS_KEY", raising=False)
    monkeypatch.delenv("SYNC_PROBE_SECRET_KEY", raising=False)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO data_sources
               (data_source_id, knowledge_base_id, source_type, name, configuration,
                created_at, updated_at)
               VALUES ('ds_s3', %s, 'object_storage', '对象存储', %s, now(), now())""",
            (
                KNOWLEDGE_BASE_ID,
                Jsonb({
                    "endpoint": "127.0.0.1:9000", "bucket": "docs",
                    "credential_env": "SYNC_PROBE",
                }),
            ),
        )
    settings = _settings(tmp_path, database_url)

    _run_full_sync(settings, database_url, "ds_s3")

    status, reason = _sync_state(database_url, "ds_s3")
    assert status == "failed"
    assert reason is not None and "SOURCE_CREDENTIALS_MISSING" in reason
