import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from backend.app.chunking import Chunk
from backend.app.config import Settings
from backend.app.database import apply_migrations
from backend.app.postgres_documents import IndexWorker, PostgresAsyncRAGService
from backend.app.postgres_repositories import PostgresDataSourceRepository
from backend.app.retrieval_access import RetrievalAccessContext, can_retrieve_metadata
from backend.app.store import ChromaStore

USER = "usr_0123456789abcdef"
OTHER = "usr_fedcba9876543210"
NOW = datetime(2026, 8, 26, tzinfo=UTC)
KNOWLEDGE_BASE_ID = "kb_default"
DOCUMENT_TEXT = "\n\n".join(
    "备份根目录固定覆盖 chroma、uploads、knowledge_bases 三个目录。" * 4 for _ in range(3)
)


def test_document_deny_has_priority_over_allow() -> None:
    metadata = {"allow_user_ids": [USER], "deny_user_ids": [USER]}
    assert can_retrieve_metadata(metadata, RetrievalAccessContext(USER), NOW) is False


def test_non_empty_allow_list_requires_current_user() -> None:
    metadata = {"allow_user_ids": [OTHER], "deny_user_ids": []}
    assert can_retrieve_metadata(metadata, RetrievalAccessContext(USER), NOW) is False
    assert can_retrieve_metadata(metadata, RetrievalAccessContext(OTHER), NOW) is True


def test_data_source_acl_is_enforced_after_document_acl() -> None:
    metadata = {
        "allow_user_ids": [],
        "deny_user_ids": [],
        "data_source_acl": {"allow_user_ids": [OTHER], "deny_user_ids": []},
    }
    assert can_retrieve_metadata(metadata, RetrievalAccessContext(USER), NOW) is False


def test_expired_or_deleted_document_is_never_retrievable() -> None:
    assert can_retrieve_metadata(
        {"retrieval_status": "deleted"}, RetrievalAccessContext(USER), NOW
    ) is False
    assert can_retrieve_metadata(
        {"retrieval_status": "searchable", "valid_to": "2026-08-25T00:00:00Z"},
        RetrievalAccessContext(USER),
        NOW,
    ) is False


def test_empty_acl_and_active_validity_inherit_knowledge_base_access() -> None:
    metadata = {
        "retrieval_status": "searchable",
        "valid_from": "2026-08-01T00:00:00Z",
        "valid_to": "2026-09-01T00:00:00Z",
        "allow_user_ids": [],
        "deny_user_ids": [],
    }
    assert can_retrieve_metadata(metadata, RetrievalAccessContext(USER), NOW) is True


def test_chroma_acl_update_takes_effect_on_next_query(tmp_path) -> None:
    store = ChromaStore(tmp_path / "chroma", "acl_boundary", "test-embedding")
    chunk = Chunk(
        chunk_id="doc_acl:chunk:00000",
        knowledge_base_id="kb_default",
        document_id="doc_acl",
        filename="acl.md",
        text="restricted content",
        page=None,
        paragraph=0,
        chunk_index=0,
        char_count=18,
        summary="restricted content",
        governance_metadata={"retrieval_status": "searchable", "acl_version": 1},
    )
    store.upsert([chunk], [[1.0, 0.0]])

    assert store.query([1.0, 0.0], 5, access=RetrievalAccessContext(USER))
    assert store.update_document_acl("doc_acl", [], [USER]) == 2
    assert store.query([1.0, 0.0], 5, access=RetrievalAccessContext(USER)) == []


class _FakeEmbedder:
    model_name = "test/embedding"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


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


def _settings(tmp_path: Path, database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        upload_path=tmp_path / "uploads",
        chunk_size=700,
        chunk_overlap=100,
        frontend_origin="http://localhost:5173",
    )


def _clone_index_version(database_url: str, template_id: str, status: str) -> str:
    """按模板版本的配置再造一个索引版本，并把它的分块整套复制过去。

    switch_to_version / rollback_to_previous 尚未实现，多版本共存只能用 SQL 直接构造。
    """

    index_version_id = f"iv_{uuid4().hex[:16]}"
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO index_versions
               (index_version_id, knowledge_base_id, status, chunking_version, parser_version,
                embedding_model, embedding_dimension, processing_options, config_fingerprint,
                evaluation_report_id)
               SELECT %s, knowledge_base_id, %s, chunking_version, parser_version,
                      embedding_model, embedding_dimension, processing_options,
                      config_fingerprint, evaluation_report_id
               FROM index_versions WHERE index_version_id = %s""",
            (index_version_id, status, template_id),
        )
        connection.execute(
            """INSERT INTO chunks
               (chunk_id, document_version_id, index_version_id, knowledge_base_id,
                chunk_index, content, metadata, embedding, created_at)
               SELECT %s::text || ':' || c.chunk_index::text, c.document_version_id, %s,
                      c.knowledge_base_id, c.chunk_index, c.content, c.metadata,
                      c.embedding, c.created_at
               FROM chunks c WHERE c.index_version_id = %s""",
            (index_version_id, index_version_id, template_id),
        )
    return index_version_id


def _statuses_with_chunks(database_url: str) -> set[str]:
    with psycopg.connect(database_url) as connection:
        rows = connection.execute(
            """SELECT DISTINCT iv.status FROM chunks c
               JOIN index_versions iv ON iv.index_version_id = c.index_version_id"""
        ).fetchall()
    return {str(row[0]) for row in rows}


def _statuses_carrying_deny(database_url: str, user_id: str) -> set[str]:
    """哪些索引版本状态的分块已经带上收紧后的 data_source deny 名单。"""

    with psycopg.connect(database_url) as connection:
        rows = connection.execute(
            """SELECT DISTINCT iv.status FROM chunks c
               JOIN index_versions iv ON iv.index_version_id = c.index_version_id
               WHERE COALESCE(c.metadata->'data_source_acl'->'deny_user_ids', '[]'::jsonb) ? %s""",
            (user_id,),
        ).fetchall()
    return {str(row[0]) for row in rows}


def _simulate_rollback(database_url: str, to_version_id: str, from_version_id: str) -> None:
    """把读指针从 from_version_id 挪回 to_version_id。

    三条 UPDATE 分开执行：``index_versions_one_active_idx`` 与 ``index_versions_one_previous_idx``
    是非延迟的 partial unique index，同一语句内出现瞬时重复也会立刻报错。
    """

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE index_versions SET status='ready' WHERE index_version_id=%s", (to_version_id,)
        )
        connection.execute(
            "UPDATE index_versions SET status='previous' WHERE index_version_id=%s",
            (from_version_id,),
        )
        connection.execute(
            """UPDATE index_versions SET status='active', activated_at=now(),
                      evaluation_report_id=COALESCE(evaluation_report_id, 'rollback')
               WHERE index_version_id=%s""",
            (to_version_id,),
        )
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id=%s WHERE knowledge_base_id=%s",
            (to_version_id, KNOWLEDGE_BASE_ID),
        )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_acl_tightening_covers_every_non_retired_index_version(tmp_path: Path) -> None:
    """data_source ACL 收紧必须刷进 active / previous / building 三种版本的分块。

    只更新 active 版本的话，回滚到 previous 之后旧分块仍带着收紧前的宽松 ACL；building
    版本漏更新则会在它被切为 active 的瞬间生效一份过期 ACL。retired 与 failed 只等清理，
    写入无意义。

    完整的"回滚后越权"端到端断言要等读路径按 active 索引版本过滤（另一任务）才能成立：
    当前 PostgresVectorStore.query 不区分索引版本，且 data_source ACL 还会实时 JOIN
    data_sources 校验一遍，因此本测试的判别性断言落在"元数据被刷到哪些版本"上。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)
    service.index_document("backup.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    assert IndexWorker(settings, _FakeEmbedder()).run_once() is True

    with psycopg.connect(database_url) as connection:
        active_version_id = str(
            connection.execute(
                "SELECT active_index_version_id FROM knowledge_bases WHERE knowledge_base_id=%s",
                (KNOWLEDGE_BASE_ID,),
            ).fetchone()[0]
        )
        data_source_id = str(
            connection.execute("SELECT data_source_id FROM data_sources").fetchone()[0]
        )

    previous_version_id = _clone_index_version(database_url, active_version_id, "previous")
    for status in ("building", "retired", "failed"):
        _clone_index_version(database_url, active_version_id, status)
    assert _statuses_with_chunks(database_url) == {
        "active",
        "previous",
        "building",
        "retired",
        "failed",
    }

    # 收紧前 USER 能检索到内容，否则后面的"检索不到"是空断言。
    assert service.retrieve_candidates(
        "备份根目录", [0.1, 0.2, 0.3], 5, KNOWLEDGE_BASE_ID,
        access=RetrievalAccessContext(USER),
    )

    policy = PostgresDataSourceRepository(database_url).update_acl(data_source_id, [], [USER])
    assert policy is not None
    assert _statuses_carrying_deny(database_url, USER) == {"active", "previous", "building"}

    _simulate_rollback(database_url, previous_version_id, active_version_id)
    assert service.retrieve_candidates(
        "备份根目录", [0.1, 0.2, 0.3], 5, KNOWLEDGE_BASE_ID,
        access=RetrievalAccessContext(USER),
    ) == []
    # 未被拒的用户不受影响，说明收紧没有把整个数据源一起封死。
    assert service.retrieve_candidates(
        "备份根目录", [0.1, 0.2, 0.3], 5, KNOWLEDGE_BASE_ID,
        access=RetrievalAccessContext(OTHER),
    )
