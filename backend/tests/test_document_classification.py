"""分类归属的人工治理：归类、置空、重新分类。

这些操作都必须在同一事务里同时更新 Document 与当前活动版本的 Chunk。分开写会漂移：
文档列表显示已归类、检索过滤却按旧分类走，而这种不一致在页面上完全看不出来。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from backend.app.config import Settings
from backend.app.database import apply_migrations
from backend.app.postgres_documents import IndexWorker, PostgresAsyncRAGService
from backend.app.postgres_repositories import PostgresCategoryRepository

KNOWLEDGE_BASE_ID = "kb_default"
requires_postgres = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector"
)


class _FakeEmbedder:
    model_name = "test/embedding"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


def _settings(tmp_path: Path, database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        upload_path=tmp_path / "uploads",
        chunk_size=200,
        chunk_overlap=0,
        frontend_origin="http://localhost:5173",
    )


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


def _index(settings: Settings, name: str, text: str) -> str:
    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)
    document = service.index_document(name, text.encode(), KNOWLEDGE_BASE_ID)
    worker = IndexWorker(settings, _FakeEmbedder())
    processed = 0
    while processed < 20 and worker.run_once():
        processed += 1
    return document.document_id


def _mark_failed(database_url: str, document_id: str) -> None:
    """把资料置成一次可重试的分类失败，作为治理动作的起点。"""

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE documents SET metadata = metadata || '{
                   "category_id": null, "category": null,
                   "classification_status": "failed",
                   "classification_failure_code": "MODEL_TIMEOUT",
                   "classification_failure_reason": "模型 30 秒未响应",
                   "classification_failed_at": "2026-08-30T00:00:00+00:00",
                   "classification_retry_count": 2
               }'::jsonb
               WHERE knowledge_base_id=%s AND document_id=%s""",
            (KNOWLEDGE_BASE_ID, document_id),
        )


def _document_metadata(database_url: str, document_id: str) -> dict:
    with psycopg.connect(database_url) as connection:
        return connection.execute(
            "SELECT metadata FROM documents WHERE knowledge_base_id=%s AND document_id=%s",
            (KNOWLEDGE_BASE_ID, document_id),
        ).fetchone()[0]


def _current_chunk_metadata(database_url: str, document_id: str) -> list[dict]:
    with psycopg.connect(database_url) as connection:
        return [
            row[0]
            for row in connection.execute(
                """SELECT c.metadata FROM chunks c
                   JOIN documents d ON d.knowledge_base_id=c.knowledge_base_id
                    AND d.current_version_id=c.document_version_id
                   WHERE d.knowledge_base_id=%s AND d.document_id=%s""",
                (KNOWLEDGE_BASE_ID, document_id),
            ).fetchall()
        ]


class _Generator:
    """按脚本返回分类结果的假模型。"""

    model_name = "test-classifier"

    def __init__(self, *responses: str | Exception, ready: bool = True):
        self.responses = list(responses)
        self.calls = 0
        self._ready = ready

    @property
    def ready(self) -> bool:
        return self._ready

    def generate(self, _prompt: str):
        self.calls += 1
        item = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item, {}


def _jobs(database_url: str, job_type: str) -> list[dict]:
    with psycopg.connect(database_url) as connection:
        return [
            {"status": row[0], "attempts": row[1], "due": row[2]}
            for row in connection.execute(
                """SELECT status, attempt_count, available_at FROM index_jobs
                   WHERE job_type = %s ORDER BY created_at""",
                (job_type,),
            ).fetchall()
        ]


def _drain(settings: Settings, generator) -> int:
    worker = IndexWorker(settings, _FakeEmbedder(), generator)
    processed = 0
    while processed < 20 and worker.run_once():
        processed += 1
    return processed


@requires_postgres
def test_reclassify_actually_runs_the_classifier(tmp_path: Path) -> None:
    """点「重新分类」必须真的重新分类，而不是把状态改成 pending 就不管了。

    分类原本只在索引任务里跑一次，而 reclassify 只改 metadata、不入队——资料回到
    pending 之后没有任何东西会去分类它，用户点了按钮却什么也不会发生。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    document_id = _index(settings, "guide.md", "# 指南\n\n正文段落。" * 20)
    _mark_failed(database_url, document_id)
    categories = PostgresCategoryRepository(database_url)
    category = categories.create(KNOWLEDGE_BASE_ID, "运维文档", "", 100)

    assert categories.reclassify(KNOWLEDGE_BASE_ID, [document_id]) == 1
    assert _jobs(database_url, "classify"), "重新分类必须入队一个分类任务"

    generator = _Generator(
        f'{{"category_id":"{category["category_id"]}","confidence":0.95,"reason":"运维手册"}}'
    )
    _drain(settings, generator)

    assert generator.calls == 1, "分类模型必须真的被调用"
    metadata = _document_metadata(database_url, document_id)
    assert metadata["category_id"] == category["category_id"]
    assert metadata["classification_status"] == "auto_assigned"
    assert metadata["classification_failure_code"] is None
    assert [job["status"] for job in _jobs(database_url, "classify")] == ["succeeded"]


@requires_postgres
def test_retryable_classification_failure_is_retried_with_backoff(tmp_path: Path) -> None:
    """可重试的失败要排队重来，并且要退避。

    「等一等会自己好」的故障值得重试；但立刻重试只会撞上同一个故障，所以要推迟。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    document_id = _index(settings, "guide.md", "# 指南\n\n正文段落。" * 20)
    categories = PostgresCategoryRepository(database_url)
    category = categories.create(KNOWLEDGE_BASE_ID, "运维文档", "", 100)
    categories.reclassify(KNOWLEDGE_BASE_ID, [document_id])

    # 第一次超时，第二次成功。
    generator = _Generator(
        TimeoutError("30s"),
        f'{{"category_id":"{category["category_id"]}","confidence":0.92,"reason":"重试成功"}}',
    )
    _drain(settings, generator)

    job = _jobs(database_url, "classify")[0]
    assert job["status"] == "queued", "可重试失败后任务要回到队列"
    assert job["attempts"] == 1
    assert job["due"] > datetime.now(UTC), "必须退避，不能立刻重试"
    metadata = _document_metadata(database_url, document_id)
    assert metadata["classification_failure_code"] == "MODEL_TIMEOUT"
    assert metadata["classification_retry_count"] == 1
    assert metadata["classification_next_retry_at"] is not None, "下次重试时间要能被看到"

    # 退避到期后重试成功。
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("UPDATE index_jobs SET available_at = now() WHERE job_type='classify'")
    _drain(settings, generator)

    assert generator.calls == 2
    metadata = _document_metadata(database_url, document_id)
    assert metadata["classification_status"] == "auto_assigned"
    assert metadata["classification_failure_code"] is None
    assert metadata["classification_retry_count"] == 0, "成功后重试计数要归零"


@requires_postgres
def test_non_retryable_failure_stops_immediately(tmp_path: Path) -> None:
    """不可重试的失败不得占用队列反复重来。

    模型返回的格式不对、分类不存在、字典是空的——重试多少次都是同样的结果。让它把
    重试次数耗光只会掩盖「这需要人来处理」，还白烧几轮模型调用。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    document_id = _index(settings, "guide.md", "# 指南\n\n正文段落。" * 20)
    categories = PostgresCategoryRepository(database_url)
    categories.create(KNOWLEDGE_BASE_ID, "运维文档", "", 100)
    categories.reclassify(KNOWLEDGE_BASE_ID, [document_id])

    generator = _Generator("这不是 JSON")
    _drain(settings, generator)

    assert generator.calls == 1, "不可重试的失败只调用一次模型，不得反复重来"
    job = _jobs(database_url, "classify")[0]
    assert job["status"] == "succeeded", "任务本身完成了：它跑了分类并记录了结果"
    metadata = _document_metadata(database_url, document_id)
    assert metadata["classification_status"] == "failed"
    assert metadata["classification_failure_code"] == "INVALID_RESPONSE"
    assert metadata["classification_retry_count"] == 0, "不可重试的失败不累加重试计数"
    assert metadata["classification_next_retry_at"] is None, "不会再重试，就不该给出重试时间"


@requires_postgres
def test_classification_failure_keeps_document_searchable(tmp_path: Path) -> None:
    """分类失败不得连累资料主体：上传、解析、切片、索引都已经完成了。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    document_id = _index(settings, "guide.md", "# 指南\n\n正文段落。" * 20)
    categories = PostgresCategoryRepository(database_url)
    categories.create(KNOWLEDGE_BASE_ID, "运维文档", "", 100)
    categories.reclassify(KNOWLEDGE_BASE_ID, [document_id])
    _drain(settings, _Generator("这不是 JSON"))

    with psycopg.connect(database_url) as connection:
        version_status, chunk_count = connection.execute(
            """SELECT v.status, (SELECT count(*) FROM chunks c
                                 WHERE c.document_version_id = d.current_version_id)
               FROM documents d
               JOIN document_versions v ON v.document_version_id = d.current_version_id
               WHERE d.document_id = %s""",
            (document_id,),
        ).fetchone()
    assert version_status == "ready", "分类失败不得把文档版本标成失败"
    assert chunk_count > 0, "分块必须还在"
    metadata = _document_metadata(database_url, document_id)
    assert metadata.get("retrieval_status", "searchable") == "searchable", "不得被软删"


@requires_postgres
def test_deleting_a_category_releases_its_documents(tmp_path: Path) -> None:
    """删掉分类不删掉资料：被引用的资料退回「没有分类」，仍然可检索。

    V15 之前这里是硬拦截（CATEGORY_IN_USE「请先批量迁移资料」），因为那时资料必须
    属于某个分类，删了分类它就无处可去。V16 拆掉伪分类之后「没有分类」已经是一等
    状态，这个限制就没有存在理由了——它只是逼着人先做一轮无意义的批量归类。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    document_id = _index(settings, "policy.md", "# 制度\n\n正文段落。" * 20)
    categories = PostgresCategoryRepository(database_url)
    category = categories.create(KNOWLEDGE_BASE_ID, "临时分类", "", 100)
    categories.assign(KNOWLEDGE_BASE_ID, [document_id], str(category["category_id"]))

    assert categories.delete(KNOWLEDGE_BASE_ID, str(category["category_id"])) is True

    metadata = _document_metadata(database_url, document_id)
    assert metadata["category_id"] is None, "资料要退回没有分类"
    assert metadata["category"] is None
    assert metadata["classification_status"] == "pending", "等待重新分类"

    # 当前版本的分块必须同步，否则检索过滤还按已删除的分类走。
    chunks = _current_chunk_metadata(database_url, document_id)
    assert chunks and all(chunk["category_id"] is None for chunk in chunks)

    with psycopg.connect(database_url) as connection:
        remaining = connection.execute(
            "SELECT count(*) FROM document_categories WHERE knowledge_base_id=%s",
            (KNOWLEDGE_BASE_ID,),
        ).fetchone()[0]
        chunk_total = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert remaining == 0, "分类本身要被删掉"
    assert chunk_total > 0, "资料与分块不得受牵连"


@requires_postgres
def test_manual_assignment_clears_every_failure_field(tmp_path: Path) -> None:
    """人工归类之后，失败信息必须一并消失。

    留着失败码的话，页面会同时显示「已人工归类」和「分类失败：模型超时」，
    看的人无从判断到底哪个是当前状态。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    document_id = _index(settings, "handbook.md", "# 手册\n\n正文段落。" * 20)
    _mark_failed(database_url, document_id)
    categories = PostgresCategoryRepository(database_url)
    category = categories.create(KNOWLEDGE_BASE_ID, "运维文档", "", 100)

    updated = categories.assign(
        KNOWLEDGE_BASE_ID, [document_id], str(category["category_id"])
    )

    assert updated == 1
    metadata = _document_metadata(database_url, document_id)
    assert metadata["category"] == "运维文档"
    assert metadata["classification_status"] == "manual"
    assert metadata["classification_failure_code"] is None
    assert metadata["classification_failure_reason"] is None
    assert metadata["classification_failed_at"] is None
    assert metadata["classification_retry_count"] == 0

    chunks = _current_chunk_metadata(database_url, document_id)
    assert chunks, "文档必须已经切出分块，否则这条断言什么也没验证"
    assert all(chunk["category_id"] == category["category_id"] for chunk in chunks)
    assert all(chunk["classification_failure_code"] is None for chunk in chunks)


@requires_postgres
def test_clearing_category_returns_document_to_pending(tmp_path: Path) -> None:
    """把资料退回「没有分类」不是归到某个叫未分类的分类里。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    document_id = _index(settings, "notes.md", "# 记录\n\n正文段落。" * 20)
    categories = PostgresCategoryRepository(database_url)
    category = categories.create(KNOWLEDGE_BASE_ID, "临时", "", 100)
    categories.assign(KNOWLEDGE_BASE_ID, [document_id], str(category["category_id"]))

    updated = categories.clear(KNOWLEDGE_BASE_ID, [document_id])

    assert updated == 1
    metadata = _document_metadata(database_url, document_id)
    assert metadata["category"] is None and metadata["category_id"] is None
    assert metadata["classification_status"] == "pending"

    chunks = _current_chunk_metadata(database_url, document_id)
    assert chunks
    assert all(chunk["category_id"] is None for chunk in chunks), "当前分块必须同步清空"

    # 清空之后该分类不再被引用，于是可以删除——伪分类时代这一步是做不到的。
    assert categories.delete(KNOWLEDGE_BASE_ID, str(category["category_id"])) is True


@requires_postgres
def test_reclassify_resets_state_without_touching_content(tmp_path: Path) -> None:
    """重新分类先回到 pending 并清空失败信息，正文与分块数量不变。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    document_id = _index(settings, "guide.md", "# 指南\n\n正文段落。" * 20)
    _mark_failed(database_url, document_id)
    categories = PostgresCategoryRepository(database_url)
    with psycopg.connect(database_url) as connection:
        chunks_before = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]

    updated = categories.reclassify(KNOWLEDGE_BASE_ID, [document_id])

    assert updated == 1
    metadata = _document_metadata(database_url, document_id)
    assert metadata["classification_status"] == "pending"
    assert metadata["classification_failure_code"] is None
    assert metadata["classification_retry_count"] == 0
    assert metadata["category_id"] is None

    with psycopg.connect(database_url) as connection:
        chunks_after = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert chunks_after == chunks_before, "重新分类不得触发重新切分或重新索引"


@requires_postgres
def test_document_listing_exposes_failure_fields(tmp_path: Path) -> None:
    """失败码必须能读回来，否则页面上「分类失败」永远说不出原因。

    写入路径和读取路径是两处代码：`_classify` 把字段写进 metadata，`list_documents`
    手工逐个挑字段构造响应。漏挑一个的表现是「库里有值、接口返回 null」。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    document_id = _index(settings, "broken.md", "# 故障\n\n正文段落。" * 20)
    _mark_failed(database_url, document_id)

    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)
    listed = {item.document_id: item for item in service.list_documents(KNOWLEDGE_BASE_ID)}

    item = listed[document_id]
    assert item.category is None, "没有分类就是 None，不得回退成「未分类」"
    assert item.classification_status == "failed"
    assert item.classification_failure_code == "MODEL_TIMEOUT"
    assert item.classification_failure_reason == "模型 30 秒未响应"
    assert item.classification_retry_count == 2


@requires_postgres
def test_history_chunks_keep_their_snapshot(tmp_path: Path) -> None:
    """归类只改当前版本的分块，历史版本保留当时的快照。

    历史分块受索引版本隔离，不进当前检索。改写它们等于篡改历史，而且回滚到旧版本时
    会看到一份「当时并不存在」的分类。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    document_id = _index(settings, "policy.md", "# 制度\n\n第一版正文。" * 20)
    with psycopg.connect(database_url) as connection:
        old_version = connection.execute(
            "SELECT current_version_id FROM documents WHERE document_id=%s", (document_id,)
        ).fetchone()[0]

    _index(settings, "policy.md", "# 制度\n\n第二版正文，内容已改。" * 20)
    categories = PostgresCategoryRepository(database_url)
    category = categories.create(KNOWLEDGE_BASE_ID, "制度规范", "", 100)
    categories.assign(KNOWLEDGE_BASE_ID, [document_id], str(category["category_id"]))

    with psycopg.connect(database_url) as connection:
        current_version = connection.execute(
            "SELECT current_version_id FROM documents WHERE document_id=%s", (document_id,)
        ).fetchone()[0]
        historical = [
            row[0]
            for row in connection.execute(
                "SELECT metadata FROM chunks WHERE document_version_id=%s", (old_version,)
            ).fetchall()
        ]
    assert current_version != old_version, "第二次索引必须产生新版本，否则这条断言是空的"
    assert historical, "旧版本的分块必须还在"
    assert all(
        chunk.get("category_id") != category["category_id"] for chunk in historical
    ), "历史分块不得被改写"


@requires_postgres
def test_deactivating_a_category_keeps_existing_assignments(tmp_path: Path) -> None:
    """停用只影响新资料的自动分类，不动已归属的资料。

    停用往往是「这个分类以后不要再用了」，而不是「以前归进去的都算错」。清空归属会让
    一批资料无声地失去分类。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    document_id = _index(settings, "legacy.md", "# 旧制度\n\n正文段落。" * 20)
    categories = PostgresCategoryRepository(database_url)
    category = categories.create(KNOWLEDGE_BASE_ID, "待淘汰", "", 100)
    categories.assign(KNOWLEDGE_BASE_ID, [document_id], str(category["category_id"]))

    categories.update(
        KNOWLEDGE_BASE_ID, str(category["category_id"]), "待淘汰", "", 100, False
    )

    metadata = _document_metadata(database_url, document_id)
    assert metadata["category_id"] == category["category_id"]
    assert metadata["category"] == "待淘汰"


@requires_postgres
def test_assignment_rejects_inactive_category(tmp_path: Path) -> None:
    """停用的分类不能作为归类目标，否则资料会归到一个用户选不到的分类里。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    document_id = _index(settings, "old.md", "# 旧\n\n正文段落。" * 20)
    categories = PostgresCategoryRepository(database_url)
    category = categories.create(KNOWLEDGE_BASE_ID, "已停用", "", 100)
    categories.update(
        KNOWLEDGE_BASE_ID, str(category["category_id"]), "已停用", "", 100, False
    )

    assert categories.assign(
        KNOWLEDGE_BASE_ID, [document_id], str(category["category_id"])
    ) is None
