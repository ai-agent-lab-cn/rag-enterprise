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
from backend.app.connectors import LocalDirectoryConnector, SourceObject
from backend.app.data_source_sync import (
    SyncDiff,
    check_delete_circuit_breaker,
    compute_diff,
    enqueue_sync,
)
from backend.app.database import apply_migrations
from backend.app.pipeline_governance import (
    TERMINAL_RESOURCE_STATUSES,
    aggregate_sync_run,
)
from backend.app.errors import AppError
from backend.app.postgres_documents import IndexWorker, PostgresAsyncRAGService


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
        # V25 把「停用」拆成了 sync_enabled 与 retrieval_enabled 两个开关：一个数据源
        # 可以停止同步但保留检索。enqueue_sync 看的是前者，这里必须跟着改——
        # 改 enabled 的旧写法在 V25 之后一直是空操作，这条守卫从那时起就没有生效过。
        connection.execute(
            "UPDATE data_sources SET sync_enabled=false WHERE data_source_id=%s", (source_id,)
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


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_deleted_objects_leave_a_tombstone_and_return_clears_it(tmp_path: Path) -> None:
    """软删除要留下墓碑，对象回来时撤掉。

    此前删除只是把 data_source_objects 那一行删掉——移走是对的（留着会反复触发软删除、
    并永久污染熔断分母），但删除这件事本身不留痕：查不到某份资料何时被哪次同步删的，
    历史文档快照里的成员也无从解释为什么不在当前清单里。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "keep.md").write_text("保留", encoding="utf-8")
    (root / "gone.md").write_text("将被删除", encoding="utf-8")
    settings = _settings(tmp_path, database_url)
    source_id = _create_directory_source(database_url, root)

    _run_full_sync(settings, database_url, source_id)

    (root / "gone.md").unlink()
    _run_full_sync(settings, database_url, source_id)

    with psycopg.connect(database_url) as connection:
        tombstones = connection.execute(
            """SELECT object_key, version, document_id, sync_run_id
               FROM data_source_tombstones WHERE data_source_id=%s""",
            (source_id,),
        ).fetchall()
    assert [row[0] for row in tombstones] == ["gone.md"]
    # 墓碑要记住删除时的内容版本与所属文档，否则它只是一条「某个键没了」的空记录。
    assert tombstones[0][1]
    assert tombstones[0][2]
    assert tombstones[0][3], "要记得是哪次同步删的"

    # 对象回来后墓碑必须撤掉：墓碑说的是「远端已经没有它了」，这句话不再成立。
    (root / "gone.md").write_text("又回来了", encoding="utf-8")
    _run_full_sync(settings, database_url, source_id)

    with psycopg.connect(database_url) as connection:
        remaining = connection.execute(
            "SELECT count(*) FROM data_source_tombstones WHERE data_source_id=%s",
            (source_id,),
        ).fetchone()[0]
    assert remaining == 0


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_synced_versions_record_where_they_came_from(tmp_path: Path) -> None:
    """同步产生的文档版本要记得来源；API 上传的留空，两者可区分。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "handbook.md").write_text("手册正文", encoding="utf-8")
    settings = _settings(tmp_path, database_url)
    source_id = _create_directory_source(database_url, root)

    _run_full_sync(settings, database_url, source_id)

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """SELECT source_uri, source_etag, sync_run_id FROM document_versions
               WHERE knowledge_base_id=%s ORDER BY created_at DESC LIMIT 1""",
            (KNOWLEDGE_BASE_ID,),
        ).fetchone()
    assert row[0] == "handbook.md"
    assert row[1], "source_etag 记的是连接器给出的内容版本"
    assert row[2], "要记得是哪次同步产生的"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_sync_cost_does_not_grow_with_unchanged_object_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """零变化同步的连接数不随对象总数增长。

    `upsert_sync_resource` 每次调用自建连接与事务，而差异阶段要为**每个**远端对象记一行，
    其中绝大多数是「无变化」。逐条写的话，一个千份文档、零变化的数据源光记录无变化就要
    开一千个连接——每个都是一次 TCP 握手加一次事务提交。这个开销不会报错，只会让同步
    越来越慢，规模上去之前没人会发现。

    这条守卫比对两种规模：对象数翻两倍多，连接数必须几乎不动。只断言一个绝对上界是不够的，
    那测的是常数开销，而真正要锁住的是**增长率**。
    """

    database_url = os.environ["TEST_DATABASE_URL"]

    def _connections_for(object_count: int, tag: str) -> int:
        _reset(database_url)
        root = tmp_path / f"docs_{tag}"
        root.mkdir()
        for index in range(object_count):
            (root / f"doc{index:02d}.md").write_text(f"第 {index} 份资料", encoding="utf-8")
        settings = _settings(tmp_path / tag, database_url)
        source_id = _create_directory_source(database_url, root)
        _run_full_sync(settings, database_url, source_id)

        # 第二次同步全部对象都未变化，最能暴露「每对象一连接」。
        opened = 0
        real_connect = psycopg.connect

        def counting_connect(*args, **kwargs):
            nonlocal opened
            opened += 1
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(psycopg, "connect", counting_connect)
        try:
            _run_full_sync(settings, database_url, source_id)
        finally:
            monkeypatch.undo()
        return opened

    small = _connections_for(4, "small")
    large = _connections_for(16, "large")

    # 对象数从 4 涨到 16（+12），连接数增量必须远小于它。逐条写的话增量就是 +12。
    assert large - small <= 3, (
        f"对象数 4→16 时连接数 {small}→{large}，增量 {large - small}——"
        "说明未变化对象仍在逐个开连接"
    )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_failed_sync_closes_the_operation_row(tmp_path: Path) -> None:
    """同步失败时 operations 投影必须收口，不能永远停在 queued。

    失败路径此前只写 data_sources 与 sync_runs——库里 sync_runs 明明是 failed，而前端
    任务列表读的 operations 行原样停在 queued、finished_at 为空。用户看到的是一个永远
    排队、永远不报错的任务。aggregate_sync_run 只在成功路径上被调用，收不了这个口。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "a.md").write_text("内容", encoding="utf-8")
    settings = _settings(tmp_path, database_url)
    source_id = _create_directory_source(database_url, root)
    _run_full_sync(settings, database_url, source_id)

    # 根目录消失是最典型的失败：挂载点掉了、导出任务没跑成。
    shutil.rmtree(root)
    _run_full_sync(settings, database_url, source_id)

    with psycopg.connect(database_url) as connection:
        run = connection.execute(
            "SELECT status FROM sync_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        operation = connection.execute(
            """SELECT o.status, o.current_stage, o.error_code, o.finished_at
               FROM operations o JOIN sync_runs s USING (operation_id)
               ORDER BY s.created_at DESC LIMIT 1"""
        ).fetchone()

    assert run[0] == "failed"
    # 两套状态机必须给出同一个结论。
    assert operation[0] == "failed", f"operations 停在 {operation[0]}"
    assert operation[1] == "failed"
    assert operation[2] == "SOURCE_ROOT_UNAVAILABLE"
    assert operation[3] is not None, "finished_at 为空表示任务在页面上永远转圈"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_a_retry_that_cannot_enqueue_does_not_lock_the_data_source_forever(
    tmp_path: Path,
) -> None:
    """重试无法入队时，资源行不能停在非终态——否则整个数据源永久无法再同步。

    链路是确定性的，不是竞态：
    1. 索引非终态失败 → 该对象下次同步不在 indexed 里 → 被归入 retry 分支
    2. `_retry_object` 撞上 `index_jobs_one_active_version_idx` → 返回 None
    3. `if retried_version:` 没有 else → 那一行 sync_resource_runs 停在批量写入时的
       'discovered'，而它不在 TERMINAL_RESOURCE_STATUSES 里
    4. `update_sync_resource_for_job` 按 document_version_id 匹配，该列在这行上是 NULL，
       于是**没有任何代码路径能再推进它**，aggregate 的 done 永远为假
    5. sync_runs 卡在 'indexing'，撞上 `sync_runs_one_active_source_idx`
       （UNIQUE(data_source_id) WHERE status IN (...,'indexing')）
    6. 之后每一次 enqueue_sync 都报 SYNC_ALREADY_RUNNING「该数据源已有同步任务在进行中」
       ——而实际上没有任何任务在跑。错误信息指向一个不存在的原因，只能人工取消。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "a.md").write_text("正文", encoding="utf-8")
    settings = _settings(tmp_path, database_url)
    source_id = _create_directory_source(database_url, root)
    _run_full_sync(settings, database_url, source_id)

    with psycopg.connect(database_url) as connection, connection.transaction():
        version_id = connection.execute(
            "SELECT document_version_id FROM document_versions LIMIT 1"
        ).fetchone()[0]
        # 把它踢出 indexed：下次同步会归入 retry 分支。
        connection.execute(
            "UPDATE document_versions SET status='failed' WHERE document_version_id=%s",
            (version_id,),
        )
        # 让 reprocess_version 撞上活动任务唯一索引，_retry_object 因此返回 None。
        connection.execute(
            """INSERT INTO index_jobs
               (index_job_id, knowledge_base_id, document_version_id, idempotency_key,
                status, job_type)
               VALUES ('job_blocker', %s, %s, 'idem_blocker', 'running', 'index')""",
            (KNOWLEDGE_BASE_ID, version_id),
        )

    _run_full_sync(settings, database_url, source_id)

    # 要守的不变量：不存在「既非终态、又没有 document_version_id」的资源行。
    # 这两个条件同时成立才是死局——非终态意味着批次算不出完成，而
    # update_sync_resource_for_job 只按 document_version_id 匹配，该列为空就再也推不动。
    with psycopg.connect(database_url) as connection:
        stranded = connection.execute(
            """SELECT external_resource_id, status FROM sync_resource_runs
               WHERE status <> ALL(%s) AND document_version_id IS NULL""",
            (list(TERMINAL_RESOURCE_STATUSES),),
        ).fetchall()
    assert not stranded, f"无法推进的资源行：{stranded}"

    # 不变量之外还要看结论是否诚实：这个版本确实有任务在跑，资源行应当是「构建中」并
    # 带上版本 id（等那个任务完成时推进它），而不是「失败」。只保证「落了终态」的话，
    # 一个正在正常重试的资源会被标成失败——不死锁了，但报告是假的。
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """SELECT status, document_version_id, error_code FROM sync_resource_runs
               WHERE operation='retry'"""
        ).fetchone()
    assert row is not None
    assert row[0] == "building", f"重试在途却被标成 {row[0]}"
    assert row[1] == version_id, "缺少版本 id，完成时无从推进"
    assert row[2] is None

    # 阻塞任务完成后批次应当收口，数据源恢复可同步——证明它只是在等，不是死了。
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("DELETE FROM index_jobs WHERE index_job_id='job_blocker'")
        connection.execute(
            "UPDATE sync_resource_runs SET status='succeeded' WHERE status='building'"
        )
    aggregate_sync_run(database_url, _latest_sync_run(database_url))
    enqueue_sync(database_url, source_id)


def _latest_sync_run(database_url: str) -> str:
    with psycopg.connect(database_url) as connection:
        return str(
            connection.execute(
                "SELECT sync_run_id FROM sync_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()[0]
        )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_content_rollback_restores_the_original_content(tmp_path: Path) -> None:
    """内容回退 A→B→A 之后，检索侧必须重新返回 A。

    `index_document` 按 content_sha256 查既有版本时**不过滤 status**，回退到 A 时命中的是
    早已 superseded 的 v1：直接短路返回，不插新版本、不入队、不移动 current_version_id。
    而 `_record_object` 把 version 记成 hash(A)，下次同步判定 unchanged——远端是 A，
    检索侧永久返回 B，且再也不会自愈。这类错误没有任何状态位会显示异常。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    target = root / "spec.md"
    settings = _settings(tmp_path, database_url)
    source_id = _create_directory_source(database_url, root)

    target.write_text("原始条款 A", encoding="utf-8")
    _run_full_sync(settings, database_url, source_id)
    target.write_text("修订条款 B", encoding="utf-8")
    _run_full_sync(settings, database_url, source_id)
    target.write_text("原始条款 A", encoding="utf-8")
    _run_full_sync(settings, database_url, source_id)

    with psycopg.connect(database_url) as connection:
        current = connection.execute(
            """SELECT dv.content_sha256 FROM documents d
               JOIN document_versions dv ON dv.document_version_id = d.current_version_id
               WHERE d.knowledge_base_id = %s""",
            (KNOWLEDGE_BASE_ID,),
        ).fetchone()
        served = connection.execute(
            """SELECT c.content FROM chunks c
               JOIN documents d ON d.current_version_id = c.document_version_id
               WHERE d.knowledge_base_id = %s""",
            (KNOWLEDGE_BASE_ID,),
        ).fetchall()

    import hashlib

    expected = hashlib.sha256("原始条款 A".encode()).hexdigest()
    assert current is not None, "回退后没有当前版本"
    assert current[0] == expected, "当前版本仍指向被回退掉的内容"
    assert any("原始条款 A" in str(row[0]) for row in served), (
        f"检索侧返回的仍是旧内容：{[str(r[0])[:20] for r in served]}"
    )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_a_document_first_created_by_upload_is_not_reindexed_every_sync(
    tmp_path: Path,
) -> None:
    """同名文件先经上传、后进同步目录时，不能每次同步都重新全量索引。

    `_known_objects(only_indexed=True)` 的 EXISTS 里有一条 `d.data_source_id = o.data_source_id`，
    而 `index_document` 的 documents upsert 是
    `DO UPDATE SET filename, metadata, updated_at`——**data_source_id 只在 INSERT 时写，
    之后永不更新**。上传按文件名自建 'file' 数据源，而 document_id 只由知识库与文件名
    折算（`_stable_id("doc", kb, safe_name.casefold())`），两条路径必然撞上同一个 document_id。

    于是 EXISTS 恒为假：该对象永远进不了 indexed，每次同步都被归入 retry，
    每次都跑一遍全量重解析加重嵌入。批次却能正常收口，没有任何状态显示异常。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)
    # 先经 API 上传：documents.data_source_id 落在按文件名自建的 'file' 源上。
    service.index_document("shared.md", "共享资料".encode(), KNOWLEDGE_BASE_ID)
    worker = IndexWorker(settings, _FakeEmbedder())
    while worker.run_once():
        pass

    root = tmp_path / "docs"
    root.mkdir()
    (root / "shared.md").write_text("共享资料", encoding="utf-8")
    source_id = _create_directory_source(database_url, root)

    _run_full_sync(settings, database_url, source_id)
    _run_full_sync(settings, database_url, source_id)

    with psycopg.connect(database_url) as connection:
        operation = connection.execute(
            """SELECT operation FROM sync_resource_runs
               WHERE external_resource_id='shared.md'
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
    assert operation is not None
    assert operation[0] == "unchanged", (
        f"内容未变却被判成 {operation[0]}——该对象每次同步都会全量重索引"
    )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_an_object_that_becomes_oversized_is_not_silently_soft_deleted(
    tmp_path: Path,
) -> None:
    """已同步的对象变成超限后不能被静默软删。

    代码注释写着「超限对象不入队、不软删、不进对象记录」，但被跳过的键不会被
    `list_objects` yield，因此不进 remote_keys，而
    `diff.deleted = [key for key in known if key not in remote_keys]` 会把它算成删除：
    软删文档、写墓碑、删对象记录。随后 skip 行又用同一个
    (sync_run_id, external_resource_id) 覆盖掉刚写的 delete 行——审计上只剩「跳过」，
    看不出文档已经退出检索。

    触发面比想象宽：调低 max_upload_mb、文件变大，或某次返回非文本 content-type 都会
    让线上文档静默消失。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "big.md").write_text("正文内容", encoding="utf-8")
    settings = _settings(tmp_path, database_url)
    source_id = _create_directory_source(database_url, root)
    _run_full_sync(settings, database_url, source_id)

    with psycopg.connect(database_url) as connection:
        before = connection.execute(
            "SELECT metadata->>'retrieval_status' FROM documents"
        ).fetchone()
    assert before[0] == "searchable"

    # 把上限压到该文件之下：它此后会被连接器跳过，而不是消失。
    tight = _settings(tmp_path, database_url).model_copy(update={"max_upload_mb": 0})
    _run_full_sync(tight, database_url, source_id)

    with psycopg.connect(database_url) as connection:
        after = connection.execute(
            "SELECT metadata->>'retrieval_status' FROM documents"
        ).fetchone()
        tombstones = connection.execute(
            "SELECT count(*) FROM data_source_tombstones"
        ).fetchone()[0]

    assert after[0] == "searchable", (
        "对象只是这次拉不动，不是从数据源消失了——软删会让它静默退出检索"
    )
    assert tombstones == 0, "跳过不该立墓碑：墓碑表达的是「远端已经没有它了」"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_resource_rows_record_every_object_with_its_operation(tmp_path: Path) -> None:
    """特征测试：一次同步后，每个对象在 sync_resource_runs 里的操作与终态。

    这一层此前**零覆盖**——把 run_sync 里所有 upsert_sync_resource 调用删光，
    原有测试仍然全绿。它是前端任务详情、单资源重试和批次完成判定的唯一数据来源，
    结构重构最容易在这里悄悄改坏。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "keep.md").write_text("保持不变", encoding="utf-8")
    (root / "edit.md").write_text("原始", encoding="utf-8")
    (root / "drop.md").write_text("将被删除", encoding="utf-8")
    settings = _settings(tmp_path, database_url)
    source_id = _create_directory_source(database_url, root)
    _run_full_sync(settings, database_url, source_id)

    def _rows(run_index: int) -> dict[str, tuple[str, str]]:
        with psycopg.connect(database_url) as connection:
            run_id = connection.execute(
                "SELECT sync_run_id FROM sync_runs ORDER BY created_at LIMIT 1 OFFSET %s",
                (run_index,),
            ).fetchone()[0]
            return {
                str(row[0]): (str(row[1]), str(row[2]))
                for row in connection.execute(
                    """SELECT external_resource_id, operation, status
                       FROM sync_resource_runs WHERE sync_run_id=%s""",
                    (run_id,),
                ).fetchall()
            }

    first = _rows(0)
    assert {key: value[0] for key, value in first.items()} == {
        "keep.md": "add", "edit.md": "add", "drop.md": "add",
    }
    assert all(value[1] == "succeeded" for value in first.values()), first

    (root / "edit.md").write_text("修订", encoding="utf-8")
    (root / "drop.md").unlink()
    _run_full_sync(settings, database_url, source_id)

    second = _rows(1)
    assert {key: value[0] for key, value in second.items()} == {
        "keep.md": "unchanged", "edit.md": "update", "drop.md": "delete",
    }
    assert second["keep.md"][1] == "unchanged"
    assert second["edit.md"][1] == "succeeded"
    assert second["drop.md"][1] == "deleted"

    # 每一行都必须落在终态：非终态行会让批次的 done 判定永远为假。
    with psycopg.connect(database_url) as connection:
        pending = connection.execute(
            "SELECT external_resource_id, status FROM sync_resource_runs WHERE status <> ALL(%s)",
            (list(TERMINAL_RESOURCE_STATUSES),),
        ).fetchall()
    assert not pending, f"未落终态的资源行：{pending}"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_a_single_object_failure_becomes_dead_letter_without_failing_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单个对象拉取失败只让它自己进 dead_letter，其余对象照常完成。

    这条分支此前零覆盖：没有任何测试让 run_sync 内部抛过异常。它是「单文档解析失败
    不该让整次同步失败」这一设计承诺的唯一落点。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "good.md").write_text("正常", encoding="utf-8")
    (root / "bad.md").write_text("会失败", encoding="utf-8")
    settings = _settings(tmp_path, database_url)
    source_id = _create_directory_source(database_url, root)

    from backend.app.connectors import LocalDirectoryConnector

    real_fetch = LocalDirectoryConnector.fetch

    def failing_fetch(self, key: str) -> bytes:
        if key == "bad.md":
            raise RuntimeError("模拟拉取失败")
        return real_fetch(self, key)

    monkeypatch.setattr(LocalDirectoryConnector, "fetch", failing_fetch)
    _run_full_sync(settings, database_url, source_id)
    monkeypatch.undo()

    with psycopg.connect(database_url) as connection:
        rows = dict(
            connection.execute(
                "SELECT external_resource_id, status FROM sync_resource_runs"
            ).fetchall()
        )
        source_status = connection.execute(
            "SELECT last_sync_status FROM data_sources WHERE data_source_id=%s", (source_id,)
        ).fetchone()[0]

    assert rows["bad.md"] == "dead_letter"
    assert rows["good.md"] == "succeeded"
    # 整次同步不因单个对象失败而失败，但也不能报告成完全成功。
    assert source_status == "failed", f"数据源状态是 {source_status}"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_a_long_running_sync_keeps_its_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """长时间运行的同步必须持续续租，否则会被判僵死并重复执行。

    `recover_stale_jobs` 把 `status='running' AND locked_at < now()-N` 的任务原地改回
    'queued'，而 run_sync 全程不刷新 locked_at。超过阈值的同步因此会被另一个 worker
    重新领走，两份 run_sync 并发跑在**同一个 sync_run_id** 上。

    两个唯一索引都拦不住：`index_jobs_one_active_sync_idx` 与
    `sync_runs_one_active_source_idx` 防的是「两条不同的记录」，而这里改的是同一行。
    后果是两份执行互相覆盖 data_source_objects，先跑完的那份还会提前释放同步锁。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    for index in range(3):
        (root / f"doc{index}.md").write_text(f"资料 {index}", encoding="utf-8")
    # 阈值压到 0 秒：任何仍在运行的任务都立刻符合僵死条件。
    settings = _settings(tmp_path, database_url).model_copy(
        update={"index_job_stale_seconds": 60}
    )
    source_id = _create_directory_source(database_url, root)
    enqueue_sync(database_url, source_id)

    worker = IndexWorker(settings, _FakeEmbedder())
    observed: list[object] = []
    real_fetch = LocalDirectoryConnector.fetch

    def fetch_and_check(self, key: str) -> bytes:
        if not observed:
            # 第一个对象：把租约拨回到远超阈值，模拟同步已经跑了很久。
            with psycopg.connect(database_url) as connection, connection.transaction():
                connection.execute(
                    """UPDATE index_jobs SET locked_at = now() - interval '2 hours'
                       WHERE job_type='sync' AND status='running'"""
                )
            observed.append(0)
        else:
            # 后续对象：此时已经过了下一个检查点。若检查点会续租，另一个 worker 此刻
            # 跑僵死回收就应当一无所获——这正是重启一个 worker 会发生的事。
            observed.append(IndexWorker(settings, _FakeEmbedder()).recover_stale_jobs())
        return real_fetch(self, key)

    monkeypatch.setattr(LocalDirectoryConnector, "fetch", fetch_and_check)
    worker.run_once()
    monkeypatch.undo()

    assert observed, "测试本身没跑到 fetch"
    assert sum(observed) == 0, (
        f"同步进行中被回收了 {sum(observed)} 次——任务会被重新领走，"
        "两份 run_sync 并发跑在同一个 sync_run_id 上，互相覆盖 data_source_objects"
    )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_keys_differing_only_in_case_do_not_silently_share_one_document(
    tmp_path: Path,
) -> None:
    """仅大小写不同的两个对象键不能被合并成同一份文档。

    `document_id = _stable_id("doc", kb, safe_name.casefold())`——casefold 让
    `Docs/A.md` 与 `docs/a.md` 算出同一个 id。S3 与 Linux 本地目录都允许两者并存：
    `data_source_objects` 里是两行，`documents` 里却只有一行。

    后果串成一条：软删其中一个会让另一个也退出检索；两条资源行若内容相同还会拿到同一个
    `document_version_id`，`update_sync_resource_for_job` 的
    `WHERE sync_run_id=%s AND document_version_id=%s` 一次更新两行。

    本机文件系统大小写不敏感，因此直接构造状态而不是造两个文件——测的是碰撞检测逻辑，
    与文件系统无关。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "report.md").write_text("季度报告", encoding="utf-8")
    settings = _settings(tmp_path, database_url)
    source_id = _create_directory_source(database_url, root)
    _run_full_sync(settings, database_url, source_id)

    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)
    # 同一个数据源下，另一个仅大小写不同的键。
    with pytest.raises(AppError) as error:
        service.index_document(
            "REPORT.md", "另一份内容".encode(), KNOWLEDGE_BASE_ID,
            data_source_id=source_id, relative_path="REPORT.md",
        )
    assert error.value.code == "SOURCE_OBJECT_KEY_COLLISION"

    # 原文档必须原样保留，不能被这次调用改名或改内容归属。
    with psycopg.connect(database_url) as connection:
        filenames = [
            row[0]
            for row in connection.execute(
                "SELECT filename FROM documents WHERE knowledge_base_id=%s",
                (KNOWLEDGE_BASE_ID,),
            ).fetchall()
        ]
    assert filenames == ["report.md"], f"原文档被覆盖成 {filenames}"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_changing_source_configuration_reaches_already_synced_objects(
    tmp_path: Path,
) -> None:
    """改数据源的治理配置后，存量对象也要跟着更新。

    `metadata_defaults` 与 `default_category_id` 的组装只在 added/updated 循环体里执行，
    而内容未变的对象根本不进那个循环。`_apply_governance_metadata` 的 docstring 写着
    「正文未变时仍扩散 Metadata/ACL，避免幂等短路留下旧权限」，但它的调用点在
    index_document 之后——前提是该对象的 version 变了。对 local_directory 而言 version
    就是内容哈希，正文不变则 version 不变，这条兜底永远不会被触发。

    结果：管理员改了数据源的默认分类或默认元数据，已同步的资料一份都不会更新，
    而页面上没有任何提示说明这一点。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "handbook.md").write_text("手册正文", encoding="utf-8")
    settings = _settings(tmp_path, database_url)
    source_id = _create_directory_source(database_url, root)
    _run_full_sync(settings, database_url, source_id)

    # 事后给数据源加一条默认元数据，内容一字不改。
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE data_sources
               SET configuration = configuration || %s::jsonb
               WHERE data_source_id = %s""",
            (Jsonb({"metadata_defaults": {"department": "法务部"}}), source_id),
        )
    _run_full_sync(settings, database_url, source_id)

    with psycopg.connect(database_url) as connection:
        department = connection.execute(
            "SELECT metadata->>'department' FROM documents WHERE knowledge_base_id=%s",
            (KNOWLEDGE_BASE_ID,),
        ).fetchone()
    assert department[0] == "法务部", (
        f"存量对象的 department 是 {department[0]}——改配置对已同步资料零效果"
    )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_content_can_roll_back_to_the_same_version_more_than_once(tmp_path: Path) -> None:
    """A→B→A→B→A：第二次回退到同一份内容不能失败。

    内容回退的修复会把那个 superseded 版本重新入队，任务的 idempotency_key 是
    `revive:{version_id}`。而 index_jobs.idempotency_key 有全表唯一约束，INSERT 的
    ON CONFLICT 只覆盖 document_version_id——第二次回退到同一内容时键已存在，
    UniqueViolation 未被处理，该对象直接进 dead_letter。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    target = root / "toggle.md"
    settings = _settings(tmp_path, database_url)
    source_id = _create_directory_source(database_url, root)

    for round_index, body in enumerate(["甲", "乙", "甲", "乙", "甲"]):
        target.write_text(body, encoding="utf-8")
        _run_full_sync(settings, database_url, source_id)
        with psycopg.connect(database_url) as connection:
            dead = connection.execute(
                "SELECT count(*) FROM sync_resource_runs WHERE status='dead_letter'"
            ).fetchone()[0]
        assert dead == 0, f"第 {round_index + 1} 轮（内容 {body}）后出现 dead_letter"

    import hashlib

    with psycopg.connect(database_url) as connection:
        current = connection.execute(
            """SELECT dv.content_sha256 FROM documents d
               JOIN document_versions dv ON dv.document_version_id = d.current_version_id
               WHERE d.knowledge_base_id = %s""",
            (KNOWLEDGE_BASE_ID,),
        ).fetchone()
    assert current[0] == hashlib.sha256("甲".encode()).hexdigest()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_retry_after_a_rollback_targets_the_rolled_back_version(tmp_path: Path) -> None:
    """回退后若索引失败，重试的必须是被回退到的那一版，不是版本号最大的那一版。

    `_retry_object` 的注释说要取「那个最新的、没能变成 current 的版本」，实现却是
    `ORDER BY version_number DESC LIMIT 1`。内容回退（A→B→A）会复活承载 A 的旧版本 v1，
    而版本号最大的是承载 B 的 v2——两者在这个场景里不是同一个。取错的话，重试会把
    内容 B 重新推成 current，而远端明明是 A。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    root = tmp_path / "docs"
    root.mkdir()
    target = root / "spec.md"
    settings = _settings(tmp_path, database_url)
    source_id = _create_directory_source(database_url, root)

    target.write_text("甲版正文", encoding="utf-8")
    _run_full_sync(settings, database_url, source_id)
    target.write_text("乙版正文", encoding="utf-8")
    _run_full_sync(settings, database_url, source_id)
    target.write_text("甲版正文", encoding="utf-8")
    _run_full_sync(settings, database_url, source_id)

    import hashlib

    jia = hashlib.sha256("甲版正文".encode()).hexdigest()
    with psycopg.connect(database_url) as connection, connection.transaction():
        revived = connection.execute(
            "SELECT document_version_id FROM document_versions WHERE content_sha256=%s",
            (jia,),
        ).fetchone()[0]
        # 把回退到的那一版打回失败：下次同步会把该对象归入 retry。
        connection.execute(
            """UPDATE document_versions SET status='failed' WHERE document_version_id=%s""",
            (revived,),
        )
        connection.execute(
            "UPDATE documents SET current_version_id=NULL WHERE knowledge_base_id=%s",
            (KNOWLEDGE_BASE_ID,),
        )

    _run_full_sync(settings, database_url, source_id)

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """SELECT dv.content_sha256 FROM documents d
               JOIN document_versions dv ON dv.document_version_id = d.current_version_id
               WHERE d.knowledge_base_id = %s""",
            (KNOWLEDGE_BASE_ID,),
        ).fetchone()
    assert row is not None, "重试之后没有当前版本"
    assert row[0] == jia, "重试推上去的是版本号更大的那一版，而不是远端实际的内容"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_sync_status_mapping_covers_every_domain() -> None:
    """同步聚合能产出的每一个状态，在三张目标表里都必须是合法取值。

    三者的 status 取值域交集只有 aborted / failed / queued / succeeded 四个。直接把
    sync_runs 的值塞给另外两张表会违反 CHECK，聚合事务整体回滚，同步卡在 queued 反复
    重试——而表面症状是「该数据源已有同步任务在进行中」，离真正的原因隔着两层。
    这个坑本轮咬过四次，所以按真实的 CHECK 逐值校验，而不是靠记忆维护映射表。
    """

    import re

    from backend.app.pipeline_governance import map_sync_status

    database_url = os.environ["TEST_DATABASE_URL"]
    apply_migrations(database_url)
    domains: dict[str, set[str]] = {}
    with psycopg.connect(database_url) as connection:
        rows = connection.execute(
            """SELECT rel.relname, pg_get_constraintdef(con.oid)
               FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid
               WHERE con.contype='c' AND pg_get_constraintdef(con.oid) LIKE '%ANY (ARRAY%'"""
        ).fetchall()
    for table, definition in rows:
        # 两种形态都要认：列有 ::text 转型的（(status)::text = ANY）与没有的（(status = ANY）。
        column = re.search(r"\((\w+)\)::text = ANY", definition) or re.search(
            r"^CHECK \(\((\w+) = ANY", definition
        )
        values = set(re.findall(r"'([a-z_]+)'::text", definition))
        if column and values:
            domains[f"{table}.{column.group(1)}"] = values

    # aggregate_sync_run 能算出的全部取值，与它计算 status 的那几行一一对应。
    producible = {"indexing", "succeeded", "partial_failed"}
    assert producible <= domains["sync_runs.status"], "sync_runs 自己的域必须容纳这些值"

    for target, key in (
        ("operations", "operations.status"),
        ("data_sources", "data_sources.last_sync_status"),
    ):
        for status in sorted(producible):
            mapped = map_sync_status(status, target=target)
            assert mapped in domains[key], (
                f"{status} 映射到 {target} 得到 {mapped!r}，不在该表的取值域里："
                f"写入会违反 CHECK，聚合事务整体回滚"
            )
