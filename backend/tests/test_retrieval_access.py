import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from backend.app.config import Settings
from backend.app.data_source_sync import (
    mark_documents_deleted,
    mark_documents_searchable,
)
from backend.app.database import apply_migrations
from backend.app.errors import AppError
from backend.app.modular_rag import RAGPolicy
from backend.app.postgres_documents import IndexWorker, PostgresAsyncRAGService
from backend.app.postgres_repositories import PostgresDataSourceRepository
from backend.app.retrieval_access import RetrievalAccessContext, can_retrieve_metadata
from backend.app.schemas import QueryMetadataFilter, QueryResponse
from backend.app.service import RAGService
from backend.app.store import RetrievedChunk
from backend.app.web_retrieval import WebSearchResult

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


def _chunk_total(database_url: str) -> int:
    with psycopg.connect(database_url) as connection:
        return int(connection.execute("SELECT count(*) FROM chunks").fetchone()[0])


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
    版本漏更新则会在它被切为 active 的瞬间生效一份过期 ACL。retired 与两种 failed 只等清理，
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
    # failed 在 V31 拆成了 build_failed / validation_failed：两者的恢复路径不同
    # （重新构建 / 重新验证），页面要能分开给按钮。两个都要覆盖到。
    for status in ("building", "retired", "build_failed", "validation_failed"):
        _clone_index_version(database_url, active_version_id, status)
    assert _statuses_with_chunks(database_url) == {
        "active",
        "previous",
        "building",
        "retired",
        "build_failed",
        "validation_failed",
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


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_soft_delete_removes_from_retrieval_but_keeps_chunks(tmp_path: Path) -> None:
    """软删除只让分块不可检索，文档、版本与向量全部保留。

    ``retrieval_status`` 的 ``deleted`` 取值 V5-3 就预留在 schemas.py 的 Literal 里，
    检索侧 retrieval_access.py 已经在挡非 searchable 的分块，这里不新增过滤逻辑。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)
    indexed = service.index_document("guide.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    IndexWorker(settings, _FakeEmbedder()).run_once()
    assert service.retrieve_candidates("备份根目录", [0.1, 0.2, 0.3], 5, KNOWLEDGE_BASE_ID)
    chunks_before = _chunk_total(database_url)

    marked = mark_documents_deleted(database_url, KNOWLEDGE_BASE_ID, [indexed.document_id])

    assert marked == 1
    assert service.retrieve_candidates("备份根目录", [0.1, 0.2, 0.3], 5, KNOWLEDGE_BASE_ID) == []
    assert _chunk_total(database_url) == chunks_before, "软删除不得删除分块"

    restored = mark_documents_searchable(database_url, KNOWLEDGE_BASE_ID, [indexed.document_id])

    assert restored == 1
    assert service.retrieve_candidates("备份根目录", [0.1, 0.2, 0.3], 5, KNOWLEDGE_BASE_ID)


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_soft_delete_survives_index_version_rollback(tmp_path: Path) -> None:
    """软删除后回滚索引版本，被删文档必须仍然检索不到。

    这条正是「只刷 active 版本」那个错误实现会漏掉的场景：previous 版本的分块没被
    标记，切回去之后被删除的内容重新可检索。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)
    indexed = service.index_document("guide.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    IndexWorker(settings, _FakeEmbedder()).run_once()
    with psycopg.connect(database_url) as connection:
        active_version_id = connection.execute(
            "SELECT active_index_version_id FROM knowledge_bases WHERE knowledge_base_id=%s",
            (KNOWLEDGE_BASE_ID,),
        ).fetchone()[0]
    previous_version_id = _clone_index_version(database_url, active_version_id, "previous")

    mark_documents_deleted(database_url, KNOWLEDGE_BASE_ID, [indexed.document_id])
    _simulate_rollback(database_url, previous_version_id, active_version_id)

    assert service.retrieve_candidates("备份根目录", [0.1, 0.2, 0.3], 5, KNOWLEDGE_BASE_ID) == []


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_reindexing_a_document_preserves_manual_acl(tmp_path: Path) -> None:
    """重新索引同一份资料不能抹掉手工设的 ACL。

    ACL 就存在 ``documents.metadata`` 里（``update_document_acl`` 写
    ``metadata = metadata || {acl_version, allow_user_ids, deny_user_ids}``）。
    而 ``index_document`` 的 upsert 曾经是 ``DO UPDATE SET metadata = EXCLUDED.metadata``
    ——**替换而不是合并**，于是同步更新一个对象、或者重新上传同名文件，都会把
    allow_user_ids 清空；检索侧把空 allow 列表判为「不限人」，一份限定给某人的资料
    就此对全知识库可见。

    这条同时守住 retrieval_status：人工下架（置 deleted）也存在同一份 metadata 里，
    被替换掉就等于悄悄恢复上架。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)

    indexed = service.index_document("payroll.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    assert IndexWorker(settings, _FakeEmbedder()).run_once() is True

    assert service.update_document_acl(
        indexed.document_id, [USER], [OTHER], knowledge_base_id=KNOWLEDGE_BASE_ID
    ) is not None
    assert service.update_document_metadata(
        indexed.document_id, {"retrieval_status": "deleted"}, knowledge_base_id=KNOWLEDGE_BASE_ID
    ) is True

    def governance() -> dict[str, object]:
        with psycopg.connect(database_url) as connection:
            row = connection.execute(
                "SELECT metadata FROM documents WHERE knowledge_base_id = %s AND document_id = %s",
                (KNOWLEDGE_BASE_ID, indexed.document_id),
            ).fetchone()
        data = dict(row[0] or {})
        keys = ("acl_version", "allow_user_ids", "deny_user_ids", "retrieval_status")
        return {key: data.get(key) for key in keys}

    before = governance()
    assert before["allow_user_ids"] == [USER], "前置条件：ACL 已写入"
    assert before["retrieval_status"] == "deleted", "前置条件：已人工下架"

    # 同名文件重新上传一次（走 index_document 的 upsert 分支）
    service.index_document("payroll.md", (DOCUMENT_TEXT + "\n\n新增一段。").encode(), KNOWLEDGE_BASE_ID)

    after = governance()
    assert after["allow_user_ids"] == [USER], f"重新索引抹掉了 allow_user_ids：{after}"
    assert after["deny_user_ids"] == [OTHER], f"重新索引抹掉了 deny_user_ids：{after}"
    assert after["acl_version"] == before["acl_version"], f"重新索引重置了 acl_version：{after}"
    assert after["retrieval_status"] == "deleted", f"重新索引把人工下架恢复成上架：{after}"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_chunks_written_by_a_worker_pick_up_governance_changes(tmp_path: Path) -> None:
    """索引任务写分块时必须用库里当下的治理状态，不能用任务启动时的快照。

    索引任务在开头一次性读走 ``d.metadata AS document_metadata``，算完 embedding 才
    DELETE+INSERT 分块。这中间可能几十分钟（大文件解析 + 向量化），而这期间管理员
    完全可能收紧 ACL 或把资料下架，或者一次同步把远端已删的对象软删掉。

    软删除/撤权走的是 ``UPDATE chunks ... WHERE document_version_id = current_version_id``
    ——只作用于**已存在**的行。所以在这个窗口里它有两种死法：打不到还没写入的行，
    或者打到了也被随后的 INSERT 用旧快照覆盖。两种都会让分块带着过期的宽松 ACL /
    searchable 状态上线，而检索侧只看分块（见本文件其它用例），不校验 documents。

    这里用「任务已入队、worker 还没跑」来站在那个窗口里：入队时 metadata 是宽松的，
    worker 跑之前把它收紧，跑完之后分块必须是收紧后的样子。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)

    # 入队但先不跑 worker：此刻 documents.metadata 还是宽松的（无 ACL 限制）
    indexed = service.index_document("secret.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)

    # 真正站进窗口：embedding 是最耗时那一步，它跑的时候快照已经读走、分块还没写。
    # 在 encode() 里改治理状态，等价于「管理员在解析/向量化进行中收紧了权限」。
    class _TighteningEmbedder:
        model_name = "test/embedding"

        def __init__(self) -> None:
            self.tightened = False

        def encode(self, texts: list[str]) -> list[list[float]]:
            if not self.tightened:
                self.tightened = True
                assert service.update_document_acl(
                    indexed.document_id, [USER], [], knowledge_base_id=KNOWLEDGE_BASE_ID
                ) is not None
                assert service.update_document_metadata(
                    indexed.document_id,
                    {"retrieval_status": "deleted"},
                    knowledge_base_id=KNOWLEDGE_BASE_ID,
                ) is True
            return [[0.1, 0.2, 0.3] for _ in texts]

    embedder = _TighteningEmbedder()
    assert IndexWorker(settings, embedder).run_once() is True
    assert embedder.tightened, "前置条件：确实在窗口里改了治理状态"

    with psycopg.connect(database_url) as connection:
        rows = connection.execute(
            """SELECT metadata -> 'allow_user_ids' AS allow,
                      metadata ->> 'retrieval_status' AS status
               FROM chunks WHERE knowledge_base_id = %s""",
            (KNOWLEDGE_BASE_ID,),
        ).fetchall()

    assert rows, "前置条件：worker 确实写了分块"
    for allow, status in rows:
        assert allow == [USER], f"分块带着收紧前的宽松 ACL 上线：allow={allow}"
        assert status == "deleted", f"分块带着下架前的 searchable 状态上线：status={status}"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_document_listing_hides_documents_the_user_cannot_retrieve(tmp_path: Path) -> None:
    """资料清单必须按 ACL 过滤，不能只校验知识库可访问。

    检索路径有严格的 ACL 判据（can_retrieve_metadata：deny 优先、非空 allow 列表要求
    命中、软删/过期一律排除），而清单路径 list_documents 的 WHERE 只有
    knowledge_base_id。于是被 deny 的成员照样能拿到整份清单，而 DocumentInfo 里带着
    filename、owner_user_id、department、sensitivity，以及 **allow_user_ids /
    deny_user_ids 本身**——授权名单原样外泄。

    「知道有这份文件、它叫什么、归谁、多敏感、谁能看」在企业场景里本身就是信息泄漏，
    哪怕正文取不到。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)

    # 这份公开文档只需要存在，后面按文件名断言，不用它的返回值。
    service.index_document("public.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    secret = service.index_document("secret-payroll.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    while IndexWorker(settings, _FakeEmbedder()).run_once():
        pass

    # 只有 USER 能看 secret；OTHER 被显式 deny
    assert service.update_document_acl(
        secret.document_id, [USER], [OTHER], knowledge_base_id=KNOWLEDGE_BASE_ID
    ) is not None

    names_for_user = {
        item.filename
        for item in service.list_documents(
            KNOWLEDGE_BASE_ID, access=RetrievalAccessContext(USER)
        )
    }
    names_for_other = {
        item.filename
        for item in service.list_documents(
            KNOWLEDGE_BASE_ID, access=RetrievalAccessContext(OTHER)
        )
    }

    assert names_for_user == {"public.md", "secret-payroll.md"}, f"授权用户看不全：{names_for_user}"
    assert names_for_other == {"public.md"}, (
        f"被 deny 的用户拿到了受限资料的清单：{names_for_other}"
    )

    # 管理路径（不传 access）仍要能看全，否则管理员没法管理 ACL
    names_for_admin = {item.filename for item in service.list_documents(KNOWLEDGE_BASE_ID)}
    assert names_for_admin == {"public.md", "secret-payroll.md"}, (
        f"不传 access 时应当不过滤（管理视图）：{names_for_admin}"
    )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_document_listing_also_honours_data_source_acl(tmp_path: Path) -> None:
    """清单过滤要用检索侧的完整判据，数据源级 ACL 同样算。

    检索侧 can_retrieve_metadata 先看文档 ACL、再看 data_source_acl（deny 优先）。
    清单侧只带文档 ACL 的话，一份「文档级没限制、但整个数据源只给某人」的资料
    仍会出现在别人的清单里。

    顺带守住一件事：data_source_acl 本身**不能出现在 API 响应里**——它是内部授权数据，
    DocumentInfo 的 extra 策略是默认 ignore，所以 store 返回它、模型丢掉它。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)

    doc = service.index_document("dept-only.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    while IndexWorker(settings, _FakeEmbedder()).run_once():
        pass

    # 文档级不设限，只在数据源级 deny 掉 OTHER
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE data_sources
               SET acl = %s
               WHERE data_source_id = (
                 SELECT data_source_id FROM documents WHERE document_id = %s)""",
            (Jsonb({"version": 2, "allow_user_ids": [USER], "deny_user_ids": [OTHER]}), doc.document_id),
        )

    for_user = service.list_documents(KNOWLEDGE_BASE_ID, access=RetrievalAccessContext(USER))
    for_other = service.list_documents(KNOWLEDGE_BASE_ID, access=RetrievalAccessContext(OTHER))

    assert [item.filename for item in for_user] == ["dept-only.md"]
    assert for_other == [], "数据源级 ACL 没生效，被 deny 的用户仍看到清单"

    # 授权数据不得外泄到响应里
    assert "data_source_acl" not in for_user[0].model_dump()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_readable_chunk_ids_reflects_current_acl(tmp_path: Path) -> None:
    """批量可读判定必须反映**当前**的 ACL，供会话记录里的引用原文做遮蔽。

    会话记录把 Source.text（分块原文）整份存进 JSON 文件，而读取时只校验会话归属，
    不复查 ACL。于是 A 提问时能看的资料，事后被移出 allow 名单或整份下架之后，
    A 的历史会话里那段原文仍然可以无限期读取——检索侧的收紧对已生成的记录完全无效。

    判据必须与 get_citation 同源（文档级 + 数据源级 ACL、retrieval_status、有效期、
    只认 active 索引版本），不能在会话那边另写一套。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)
    sources = PostgresDataSourceRepository(database_url)

    indexed = service.index_document("evidence.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    assert IndexWorker(settings, _FakeEmbedder()).run_once() is True

    with psycopg.connect(database_url) as connection:
        chunk_ids = [
            str(row[0])
            for row in connection.execute(
                "SELECT chunk_id FROM chunks WHERE knowledge_base_id = %s", (KNOWLEDGE_BASE_ID,)
            ).fetchall()
        ]
    assert chunk_ids, "前置条件：分块已写入"

    # 提问时两人都能看
    assert sources.readable_chunk_ids(KNOWLEDGE_BASE_ID, chunk_ids, USER) == set(chunk_ids)
    assert sources.readable_chunk_ids(KNOWLEDGE_BASE_ID, chunk_ids, OTHER) == set(chunk_ids)

    # 事后收紧：只留 USER
    assert service.update_document_acl(
        indexed.document_id, [USER], [], knowledge_base_id=KNOWLEDGE_BASE_ID
    ) is not None

    assert sources.readable_chunk_ids(KNOWLEDGE_BASE_ID, chunk_ids, USER) == set(chunk_ids)
    assert sources.readable_chunk_ids(KNOWLEDGE_BASE_ID, chunk_ids, OTHER) == set(), (
        "撤权之后仍判为可读——历史会话里的原文会继续对他可见"
    )

    # 整份下架：连 USER 也不该再读到
    assert service.update_document_metadata(
        indexed.document_id, {"retrieval_status": "deleted"}, knowledge_base_id=KNOWLEDGE_BASE_ID
    ) is True
    assert sources.readable_chunk_ids(KNOWLEDGE_BASE_ID, chunk_ids, USER) == set()

    # 空输入不该打库
    assert sources.readable_chunk_ids(KNOWLEDGE_BASE_ID, [], USER) == set()


def test_web_evidence_snapshot_is_not_treated_as_a_knowledge_base_chunk() -> None:
    from backend.app.main import _redact_unreadable_sources

    class _UnexpectedLookup:
        def readable_chunk_ids(self, *_args):
            raise AssertionError("Web evidence must not enter the knowledge-base ACL lookup")

    records = [
        {
            "sources": [
                {
                    "chunk_id": "web_0123456789abcdef0123",
                    "evidence_source_type": "web",
                    "text": "受控抓取时保存的网页证据",
                }
            ]
        }
    ]

    _redact_unreadable_sources(records, KNOWLEDGE_BASE_ID, USER, _UnexpectedLookup())

    assert records[0]["sources"][0]["text"] == "受控抓取时保存的网页证据"
    assert records[0]["sources"][0].get("redacted") is not True


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_conversation_sources_are_redacted_after_access_is_revoked(tmp_path: Path) -> None:
    """撤权后历史会话里的引用原文必须被遮蔽，但要保留「引用过这份资料」的痕迹。"""

    from backend.app.history import ConversationRepository
    from backend.app.main import _redact_unreadable_sources

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)
    sources = PostgresDataSourceRepository(database_url)

    indexed = service.index_document("evidence.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
    assert IndexWorker(settings, _FakeEmbedder()).run_once() is True
    with psycopg.connect(database_url) as connection:
        chunk_id, chunk_text = connection.execute(
            "SELECT chunk_id, content FROM chunks WHERE knowledge_base_id = %s LIMIT 1",
            (KNOWLEDGE_BASE_ID,),
        ).fetchone()

    # 提问时把原文快照存进会话记录
    history = ConversationRepository(tmp_path / "records.json")
    conversation = history.resolve_conversation(KNOWLEDGE_BASE_ID, "问题", None, OTHER)
    history.record(
        conversation_id=conversation["conversation_id"],
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        question="问题",
        status="success",
        answer="带引用的答案",
        sources=[{"chunk_id": str(chunk_id), "filename": "evidence.md", "text": str(chunk_text)}],
        latency_ms={"total": 1.0},
        models={},
        model_metadata={},
        prompt_version="v1",
        prompt_hash="h",
        answer_status="answered",
        generation_governance={},
        query_metadata={},
    )
    detail = history.get_conversation(KNOWLEDGE_BASE_ID, conversation["conversation_id"], OTHER)
    assert detail is not None

    # 撤权前：原文可读，不遮蔽
    _redact_unreadable_sources(detail["records"], KNOWLEDGE_BASE_ID, OTHER, sources)
    assert detail["records"][0]["sources"][0]["text"] == str(chunk_text)
    assert detail["records"][0]["sources"][0].get("redacted") is not True

    # 撤权：只留 USER
    assert service.update_document_acl(
        indexed.document_id, [USER], [], knowledge_base_id=KNOWLEDGE_BASE_ID
    ) is not None

    detail = history.get_conversation(KNOWLEDGE_BASE_ID, conversation["conversation_id"], OTHER)
    _redact_unreadable_sources(detail["records"], KNOWLEDGE_BASE_ID, OTHER, sources)
    source = detail["records"][0]["sources"][0]
    assert source["text"] == "", "撤权后历史会话仍能读到原文"
    assert source["redacted"] is True, "前端需要能区分「被遮蔽」与「原文为空」"
    assert source["filename"] == "evidence.md", (
        "文件名与定位信息要保留——否则历史会话变成一段没有出处的答案，看起来像记录损坏"
    )

    # 非 PostgreSQL 运行时（sources 为 None）不遮蔽
    detail2 = history.get_conversation(KNOWLEDGE_BASE_ID, conversation["conversation_id"], OTHER)
    _redact_unreadable_sources(detail2["records"], KNOWLEDGE_BASE_ID, OTHER, None)
    assert detail2["records"][0]["sources"][0]["text"] == str(chunk_text)


# --------------------------------------------------------------------------------------
# fact_lookup_v1 执行链：一轮 KB 检索 → KB 精排 → Preliminary Gate → 最多一次 Web
#                        → 统一精排 → Final Gate
#
# 这组用例**不加 skipif**：它们全在内存里跑，验证的是调用次数与稳定决策，不需要
# PostgreSQL。替身沿用 test_query_understanding.py 已有的 Embedder / Store / Generator
# 形状，只补上仓库里此前完全不存在的假 policy_repository 与假 Web Provider。
# --------------------------------------------------------------------------------------

CHAIN_QUESTION = "索引版本的默认取值是多少"
# 命中 QueryIntentRouter._FRESHNESS 的「最新」，路由会标 requires_freshness=True。
CHAIN_FRESHNESS_QUESTION = "最新的索引版本是多少"
CHAIN_DOMAINS = ("docs.example.com",)
# QueryMetadataFilter.category_ids 有格式校验（schemas.py:597-602，`cat_` + 16 位十六进制），
# 随手写的 "cat_index" 构造 filter 时就抛 ValidationError，测试根本走不到服务层。
CHAIN_CATEGORY_ID = "cat_1234567890abcdef"
# fact_lookup_v1 的固定链，加上链首的 intent.router。顺序变了这条断言就会红。
CHAIN_MODULE_SEQUENCE = (
    "intent.router",
    "query.normalize",
    "query.expand",
    "retrieval.knowledge_base",
    "rerank.knowledge_base",
    "evidence.preliminary_gate",
    "retrieval.web_policy",
    "evidence.fuse",
    "rerank.unified",
    "evidence.final_gate",
    "generation.fact",
    "generation.verify",
)


def _kb_chunk(chunk_id: str) -> RetrievedChunk:
    """引用字段齐全的 KB 候选。

    ``document_version_id`` 与 ``content_sha256`` 不是可选装饰：Evidence Gate 的引用完整性
    判据缺任一项就把候选淘汰，最终证据集会直接变空。
    """

    return RetrievedChunk(
        chunk_id=chunk_id,
        text=f"知识库证据 {chunk_id}",
        metadata={
            "knowledge_base_id": KNOWLEDGE_BASE_ID,
            "document_id": f"doc_{chunk_id}",
            "document_version_id": f"dv_{chunk_id}",
            "content_sha256": f"{chunk_id:_<64}"[:64],
            "filename": f"{chunk_id}.md",
            "paragraph": 0,
            "chunk_index": 0,
            "category_id": CHAIN_CATEGORY_ID,
        },
        retrieval_score=0.9,
        vector_score=0.9,
        retrieval_methods=["vector"],
    )


def _web_result(rank: int = 1) -> WebSearchResult:
    return WebSearchResult(
        url=f"https://docs.example.com/page-{rank}",
        title=f"page-{rank}",
        snippet="摘要",
        content=f"网页证据 {rank}",
        retrieved_at="2026-09-20T00:00:00+00:00",
        content_sha256=f"{rank:0>64}",
        rank=rank,
    )


class _ChainStore:
    def __init__(self, candidates: list[RetrievedChunk]):
        self.candidates = candidates
        self.queries: list[str] = []

    def resolve_active_version(self, knowledge_base_id: str) -> str:
        return "iv_chain"

    def query(self, embedding, limit, knowledge_base_id, query_text=None, filters=None,
              access=None, *, index_version_id=None):
        self.queries.append(query_text)
        return self.candidates[:limit]

    def list_documents(self, knowledge_base_id: str):
        return [
            {"document_id": "doc_ready", "filename": "ready.md", "chunk_count": 1, "status": "ready"}
        ]


class _ChainReranker:
    # 模型名必须在 evidence_gate.RERANKER_THRESHOLDS 里登记过，否则门禁读不懂分数语义，
    # 每次查询都是 RAG_PROFILE_INCOMPATIBLE。CrossEncoder 语义：score >= 0.0 视为相关。
    model_name = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

    def __init__(self, scores: dict[str, float] | None = None, default: float = 1.0):
        self.scores = scores or {}
        self.default = default
        self.calls = 0

    def score(self, question: str, chunks: list[str]) -> list[float]:
        self.calls += 1
        return [self.scores.get(text, self.default) for text in chunks]


class _UnregisteredReranker(_ChainReranker):
    model_name = "test/reranker"


class _ChainGenerator:
    model_name = "chain/generator"
    # ready=False：生成阶段直接回 retrieval_only。这组用例只验证执行链，不验证答案文本；
    # 同时它没有 generate()，Router 的分类器调用会抛异常并安全降级到 fact_lookup。
    ready = False


class _ChainPolicies:
    def __init__(self, policy: RAGPolicy):
        self.policy = policy

    def get(self, knowledge_base_id: str) -> RAGPolicy:
        return self.policy

    def active_capabilities(self, knowledge_base_id: str) -> tuple[str, dict[str, object]]:
        return "iv_chain", {}


class _ChainWebProvider:
    def __init__(
        self,
        results: tuple[WebSearchResult, ...] = (),
        base_url: str = "https://searxng.internal",
        error: Exception | None = None,
    ):
        self.base_url = base_url
        self.results = list(results)
        self.error = error
        self.calls = 0

    def search(self, query: str, allowed_domains: tuple[str, ...], limit: int = 5):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.results[:limit]


def _web_enabled_policy(**overrides) -> RAGPolicy:
    # rollout_stage 保持默认的 shadow：Web 是否执行只看开关、域名、Provider 与门禁。
    return RAGPolicy(
        web_search_enabled=True,
        allowed_domains=CHAIN_DOMAINS,
        **overrides,
    )


def _chain_service(
    store: _ChainStore,
    reranker: _ChainReranker,
    *,
    policy: RAGPolicy | None = None,
    web_provider: _ChainWebProvider | None = None,
) -> RAGService:
    return RAGService(
        Settings(),
        store,
        _FakeEmbedder(),
        reranker,
        _ChainGenerator(),
        policy_repository=_ChainPolicies(policy or RAGPolicy()),
        web_provider=web_provider,
    )


def _module(response: QueryResponse, module_key: str):
    return next(item for item in response.module_executions if item.module_key == module_key)


def test_sufficient_knowledge_base_evidence_runs_one_kb_round_and_no_web() -> None:
    """普通问题 KB 够用：一轮 KB 检索、一次精排、零次联网，跳过的模块仍留在轨迹里。"""

    store = _ChainStore([_kb_chunk("a"), _kb_chunk("b")])
    reranker = _ChainReranker()
    provider = _ChainWebProvider(results=(_web_result(),))
    service = _chain_service(store, reranker, policy=_web_enabled_policy(), web_provider=provider)

    response = service.query(CHAIN_QUESTION, retrieve_k=5, rerank_k=5)

    assert store.queries == [CHAIN_QUESTION], "查询扩展属于同一轮，这个问题没有扩展"
    assert reranker.calls == 1, "未触发 Web 时不应该再精排第二次"
    assert provider.calls == 0
    assert tuple(item.module_key for item in response.module_executions) == CHAIN_MODULE_SEQUENCE

    web_policy = _module(response, "retrieval.web_policy")
    assert web_policy.status == "skipped"
    assert web_policy.metrics["decision"] == "not_needed"
    assert web_policy.metrics["result_count"] == 0
    assert web_policy.metrics["reason_code"] == "kb_evidence_sufficient"
    # 未触发 Web 时融合与统一精排是空操作，但不能从轨迹里消失，否则前端只能靠
    # 「最终有没有 Web 来源」反推是否联网过。
    for module_key in ("evidence.fuse", "rerank.unified"):
        module = _module(response, module_key)
        assert module.status == "skipped"
        assert module.metrics["reason_code"] == "web_not_executed"
    assert [item.chunk_id for item in response.sources] == ["a", "b"]


def test_preliminary_pass_still_runs_the_final_gate() -> None:
    """初步门禁说够了也不能就地作答：终局判定权始终在 Final Gate。"""

    store = _ChainStore([_kb_chunk("a")])
    service = _chain_service(store, _ChainReranker(), policy=_web_enabled_policy())

    response = service.query(CHAIN_QUESTION, retrieve_k=5, rerank_k=5)

    preliminary = _module(response, "evidence.preliminary_gate")
    final = _module(response, "evidence.final_gate")
    assert preliminary.metrics["outcome"] == "pass"
    assert preliminary.metrics["sufficient"] is True
    assert final.metrics["outcome"] == "pass"
    assert final.metrics["selected_count"] == 1
    assert final.metrics["kb_count"] == 1
    assert final.metrics["web_count"] == 0
    assert final.sequence > preliminary.sequence


def test_kb_shortfall_triggers_exactly_one_web_supplement() -> None:
    """KB 合格证据不足最低条数时补一次 Web，且只补一次。"""

    store = _ChainStore([_kb_chunk("a")])
    reranker = _ChainReranker()
    provider = _ChainWebProvider(results=(_web_result(),))
    service = _chain_service(
        store,
        reranker,
        policy=_web_enabled_policy(minimum_evidence_count=2),
        web_provider=provider,
    )

    response = service.query(CHAIN_QUESTION, retrieve_k=5, rerank_k=5)

    assert provider.calls == 1
    assert store.queries == [CHAIN_QUESTION], "Web 补检不得顺带再打一轮 KB"
    assert reranker.calls == 2, "KB 精排一次 + Web 之后的统一精排一次"

    preliminary = _module(response, "evidence.preliminary_gate")
    assert preliminary.metrics["outcome"] == "needs_web"
    assert "kb_evidence_below_minimum" in preliminary.metrics["reason_codes"]

    web_policy = _module(response, "retrieval.web_policy")
    assert web_policy.status == "succeeded"
    assert web_policy.metrics["decision"] == "executed"
    assert web_policy.metrics["result_count"] == 1
    assert web_policy.metrics["reason_code"] is None

    final = _module(response, "evidence.final_gate")
    assert final.metrics["outcome"] == "pass"
    assert final.metrics["kb_count"] == 1
    assert final.metrics["web_count"] == 1
    assert {item.evidence_source_type for item in response.sources} == {"knowledge_base", "web"}


def test_shadow_rollout_still_runs_the_web_supplement_for_freshness_questions() -> None:
    """时效问题无论 KB 是否通过都试一次 Web；shadow 阶段不再阻断已启用的 Web 策略。"""

    policy = _web_enabled_policy()
    assert policy.rollout_stage == "shadow"
    store = _ChainStore([_kb_chunk("a"), _kb_chunk("b")])
    provider = _ChainWebProvider(results=(_web_result(),))
    service = _chain_service(store, _ChainReranker(), policy=policy, web_provider=provider)

    response = service.query(CHAIN_FRESHNESS_QUESTION, retrieve_k=5, rerank_k=5)

    assert provider.calls == 1
    assert response.routing is not None
    assert response.routing.requires_freshness is True

    preliminary = _module(response, "evidence.preliminary_gate")
    assert preliminary.metrics["outcome"] == "needs_web"
    assert preliminary.metrics["reason_codes"][0] == "freshness_required"

    final = _module(response, "evidence.final_gate")
    assert final.metrics["outcome"] == "pass"
    assert final.metrics["freshness_verified"] is True
    assert "freshness_verified" in final.metrics["reason_codes"]


def test_explicit_metadata_filter_blocks_the_web_supplement() -> None:
    """显式分类过滤时 Web 不得突破知识库范围，哪怕问题是时效问题。"""

    store = _ChainStore([_kb_chunk("a"), _kb_chunk("b")])
    provider = _ChainWebProvider(results=(_web_result(),))
    service = _chain_service(
        store, _ChainReranker(), policy=_web_enabled_policy(), web_provider=provider
    )

    response = service.query(
        CHAIN_FRESHNESS_QUESTION,
        retrieve_k=5,
        rerank_k=5,
        filters=QueryMetadataFilter(category_ids=[CHAIN_CATEGORY_ID]),
    )

    assert provider.calls == 0
    web_policy = _module(response, "retrieval.web_policy")
    assert web_policy.metrics["decision"] == "scope_limited"
    assert web_policy.metrics["reason_code"] == "knowledge_base_scope_locked"
    assert {item.evidence_source_type for item in response.sources} == {"knowledge_base"}


def test_web_provider_without_base_url_is_reported_as_unavailable() -> None:
    """Provider 对象存在但 base_url 为空时 search() 会静默返回空列表。

    只检查对象是否存在，技术抽屉就会把「根本没配」显示成「搜了但什么都没搜到」。
    """

    store = _ChainStore([_kb_chunk("a")])
    provider = _ChainWebProvider(results=(_web_result(),), base_url="")
    service = _chain_service(
        store, _ChainReranker(), policy=_web_enabled_policy(), web_provider=provider
    )

    response = service.query(CHAIN_FRESHNESS_QUESTION, retrieve_k=5, rerank_k=5)

    assert provider.calls == 0
    web_policy = _module(response, "retrieval.web_policy")
    assert web_policy.metrics["decision"] == "provider_unavailable"
    assert web_policy.metrics["reason_code"] == "web_provider_not_configured"


def test_web_search_returning_nothing_is_distinguishable_from_never_running() -> None:
    """「搜了但没结果」与「压根没搜」必须是两个不同的 decision。"""

    store = _ChainStore([_kb_chunk("a")])
    provider = _ChainWebProvider(results=())
    service = _chain_service(
        store, _ChainReranker(), policy=_web_enabled_policy(), web_provider=provider
    )

    response = service.query(CHAIN_FRESHNESS_QUESTION, retrieve_k=5, rerank_k=5)

    assert provider.calls == 1
    web_policy = _module(response, "retrieval.web_policy")
    assert web_policy.metrics["decision"] == "no_result"
    assert web_policy.metrics["reason_code"] == "web_no_search_result"
    # 跳过的融合模块要说出真实原因，不能一律写成 web_not_executed。
    assert _module(response, "evidence.fuse").metrics["reason_code"] == "web_no_search_result"


def test_web_failure_keeps_knowledge_base_evidence_and_records_the_reason() -> None:
    """Web 失败只降级，不吞掉已经拿到的 KB 证据。"""

    store = _ChainStore([_kb_chunk("a"), _kb_chunk("b")])
    provider = _ChainWebProvider(error=RuntimeError("searxng unreachable"))
    service = _chain_service(
        store, _ChainReranker(), policy=_web_enabled_policy(), web_provider=provider
    )

    response = service.query(CHAIN_FRESHNESS_QUESTION, retrieve_k=5, rerank_k=5)

    assert provider.calls == 1
    web_policy = _module(response, "retrieval.web_policy")
    assert web_policy.status == "degraded"
    assert web_policy.metrics["decision"] == "failed"
    assert web_policy.metrics["reason_code"] == "web_retrieval_failed"
    assert web_policy.error_code == "WEB_RETRIEVAL_FAILED"

    final = _module(response, "evidence.final_gate")
    # 时效问题 + 有 KB 历史证据 + 拿不到合格 Web 证据 = 时效未验证，不是拒答。
    assert final.metrics["outcome"] == "stale"
    assert final.metrics["freshness_verified"] is False
    assert [item.chunk_id for item in response.sources] == ["a", "b"]


def test_web_disabled_rejects_instead_of_pretending_to_have_evidence() -> None:
    """KB 候选全部低于相关性阈值且 Web 未启用：拒答，且说得出为什么。"""

    store = _ChainStore([_kb_chunk("a")])
    reranker = _ChainReranker(scores={"知识库证据 a": -1.0})
    service = _chain_service(store, reranker)

    response = service.query(CHAIN_QUESTION, retrieve_k=5, rerank_k=5)

    assert response.answer_status == "insufficient_evidence"
    assert response.sources == [], "拒答时不得把没通过门禁的候选当证据展示"

    web_policy = _module(response, "retrieval.web_policy")
    assert web_policy.metrics["decision"] == "disabled"
    assert web_policy.metrics["reason_code"] == "web_search_disabled"

    preliminary = _module(response, "evidence.preliminary_gate")
    assert preliminary.metrics["outcome"] == "reject"
    # R26：初步门禁拒了也必须继续走到 Final Gate，终局判定只有一个出口。
    final = _module(response, "evidence.final_gate")
    assert final.metrics["outcome"] == "reject"
    assert "no_kb_anchor" in final.metrics["reason_codes"]
    assert "relevance_below_threshold" in final.metrics["reason_codes"]
    assert response.generation_governance is not None
    assert response.generation_governance.outcome_reason == "INSUFFICIENT_EVIDENCE"


def test_unregistered_reranker_is_a_stable_configuration_error() -> None:
    """读不懂分数语义时必须报稳定配置错误，不能悄悄跳过相关性门禁。"""

    store = _ChainStore([_kb_chunk("a")])
    service = _chain_service(store, _UnregisteredReranker())

    with pytest.raises(AppError) as raised:
        service.query(CHAIN_QUESTION, retrieve_k=5, rerank_k=5)

    assert raised.value.code == "RAG_PROFILE_INCOMPATIBLE"
    assert raised.value.status_code == 409
    assert raised.value.details["profile_errors"] == ["未登记 Reranker 分数语义：test/reranker"]
    assert raised.value.details["pipeline_profile"] == "fact_lookup_v1"


# --------------------------------------------------------------------------------------
# 执行轨迹完整性：模块清单不随分支变化，跳过的模块必须留在轨迹里并说出原因。
#
# 上面那组用例每条只挑自己关心的两三个模块断言，于是"某个模块整条消失"这类退化谁也抓不到
# ——技术抽屉唯一的联网判据就是 retrieval.web_policy 的轨迹（types.describeWebExecution），
# 模块不在轨迹里时它只能显示"历史记录未保存 Web 执行状态"，而这次查询明明刚刚跑完。
# --------------------------------------------------------------------------------------


def _module_keys(response: QueryResponse) -> tuple[str, ...]:
    return tuple(item.module_key for item in response.module_executions)


def test_module_sequence_is_identical_whether_or_not_web_runs() -> None:
    """联网只改模块的 status 与 metrics，不增删模块。"""

    executed = _chain_service(
        _ChainStore([_kb_chunk("a")]),
        _ChainReranker(),
        policy=_web_enabled_policy(minimum_evidence_count=2),
        web_provider=_ChainWebProvider(results=(_web_result(),)),
    ).query(CHAIN_QUESTION, retrieve_k=5, rerank_k=5)
    skipped = _chain_service(
        _ChainStore([_kb_chunk("a"), _kb_chunk("b")]),
        _ChainReranker(),
        policy=_web_enabled_policy(),
        web_provider=_ChainWebProvider(results=(_web_result(),)),
    ).query(CHAIN_QUESTION, retrieve_k=5, rerank_k=5)

    assert _module_keys(executed) == CHAIN_MODULE_SEQUENCE
    assert _module_keys(skipped) == CHAIN_MODULE_SEQUENCE
    # 真的融合过就不带 reason_code：空字符串、None 与"未执行"是三种不同的展示。
    for module_key in ("evidence.fuse", "rerank.unified"):
        assert _module(executed, module_key).status == "succeeded"
        assert _module(executed, module_key).metrics["reason_code"] is None
        assert _module(skipped, module_key).status == "skipped"
        assert _module(skipped, module_key).metrics["reason_code"] == "web_not_executed"


@pytest.mark.parametrize(
    ("skip_case", "decision", "reason_code"),
    [
        ("disabled", "disabled", "web_search_disabled"),
        ("provider_unavailable", "provider_unavailable", "web_provider_not_configured"),
        ("scope_limited", "scope_limited", "knowledge_base_scope_locked"),
        ("not_needed", "not_needed", "kb_evidence_sufficient"),
    ],
)
def test_every_web_skip_keeps_the_whole_trace_and_says_why(
    skip_case: str, decision: str, reason_code: str
) -> None:
    """四种未联网的理由各自成立，且都不会让模块从轨迹里消失。"""

    store = _ChainStore([_kb_chunk("a"), _kb_chunk("b")])
    policy = _web_enabled_policy()
    provider = _ChainWebProvider(results=(_web_result(),))
    filters: QueryMetadataFilter | None = None
    if skip_case == "disabled":
        policy = RAGPolicy()
    elif skip_case == "provider_unavailable":
        provider = _ChainWebProvider(results=(_web_result(),), base_url="")
    elif skip_case == "scope_limited":
        filters = QueryMetadataFilter(category_ids=[CHAIN_CATEGORY_ID])
    service = _chain_service(store, _ChainReranker(), policy=policy, web_provider=provider)

    response = service.query(CHAIN_QUESTION, retrieve_k=5, rerank_k=5, filters=filters)

    assert provider.calls == 0
    assert _module_keys(response) == CHAIN_MODULE_SEQUENCE
    web_policy = _module(response, "retrieval.web_policy")
    assert web_policy.status == "skipped"
    assert web_policy.metrics["decision"] == decision
    assert web_policy.metrics["reason_code"] == reason_code
    # Web 从未发起时融合侧一律是 web_not_executed：「没搜」与「搜了没结果」不能混成一句
    # （后者由 test_web_search_returning_nothing_is_distinguishable_from_never_running 守着）。
    for module_key in ("evidence.fuse", "rerank.unified"):
        assert _module(response, module_key).status == "skipped"
        assert _module(response, module_key).metrics["reason_code"] == "web_not_executed"


@pytest.mark.parametrize(
    "scope_filter",
    [
        QueryMetadataFilter(category_ids=[CHAIN_CATEGORY_ID]),
        QueryMetadataFilter(categories=["索引治理"]),
        QueryMetadataFilter(tags=["索引配置"]),
        QueryMetadataFilter(source_types=["file"]),
        QueryMetadataFilter(created_from=datetime(2026, 1, 1, tzinfo=UTC)),
        QueryMetadataFilter(created_to=datetime(2027, 1, 1, tzinfo=UTC)),
    ],
)
def test_any_explicit_scope_filter_blocks_the_web_supplement(
    scope_filter: QueryMetadataFilter,
) -> None:
    """全局约束「显式文档、分类、标签或时间范围过滤时禁止 Web 突破范围」逐个过滤项验一遍。

    只验分类那一个不够：`web_filter_allows`（service.py:616-626）是五个条件的合取，
    漏掉其中任何一项都不会报错，只会让那一类过滤下的 Web 静默突破知识库范围。
    """

    chunks = [_kb_chunk("a"), _kb_chunk("b")]
    for chunk in chunks:
        # 候选要同时满足全部过滤项，否则 _filter_candidates 会先把它们清空，
        # 拿到的就是 NO_CANDIDATES 而不是 scope_limited——两种失败看起来一样。
        chunk.metadata.update(
            {
                "category": "索引治理",
                "tags": ["索引配置"],
                "source_type": "file",
                "created_at": "2026-06-01T00:00:00+00:00",
            }
        )
    provider = _ChainWebProvider(results=(_web_result(),))
    service = _chain_service(
        _ChainStore(chunks), _ChainReranker(), policy=_web_enabled_policy(), web_provider=provider
    )

    # 时效问题：不加过滤时它一定会联网，所以"没联网"只可能是范围锁住了。
    response = service.query(
        CHAIN_FRESHNESS_QUESTION, retrieve_k=5, rerank_k=5, filters=scope_filter
    )

    assert provider.calls == 0
    web_policy = _module(response, "retrieval.web_policy")
    assert web_policy.metrics["decision"] == "scope_limited"
    assert web_policy.metrics["reason_code"] == "knowledge_base_scope_locked"
    assert {item.evidence_source_type for item in response.sources} == {"knowledge_base"}


@pytest.mark.parametrize(
    ("blocked_case", "decision"),
    [
        ("all_three", "disabled"),
        ("provider_and_scope", "provider_unavailable"),
        ("scope_only", "scope_limited"),
    ],
)
def test_web_decision_reports_the_most_fundamental_blocker(
    blocked_case: str, decision: str
) -> None:
    """多个阻断条件同时成立时只报最根本那个，顺序固定。

    if/elif 链的次序就是这条规则的全部实现（service.py:725-734）：谁都不报错，换个顺序
    只会让管理员按着"当前检索范围禁止 Web"去改过滤条件，而真正的原因是 Web 开关没开。
    """

    store = _ChainStore([_kb_chunk("a"), _kb_chunk("b")])
    policy = RAGPolicy() if blocked_case == "all_three" else _web_enabled_policy()
    provider = (
        _ChainWebProvider(results=(_web_result(),))
        if blocked_case == "scope_only"
        else _ChainWebProvider(results=(_web_result(),), base_url="")
    )
    service = _chain_service(store, _ChainReranker(), policy=policy, web_provider=provider)

    response = service.query(
        CHAIN_FRESHNESS_QUESTION,
        retrieve_k=5,
        rerank_k=5,
        filters=QueryMetadataFilter(category_ids=[CHAIN_CATEGORY_ID]),
    )

    assert _module(response, "retrieval.web_policy").metrics["decision"] == decision


def test_rejected_query_keeps_the_whole_trace_up_to_the_final_gate() -> None:
    """拒答不是"链断了"：门禁之前的模块一条不少，门禁之后一条不多。"""

    store = _ChainStore([_kb_chunk("a")])
    reranker = _ChainReranker(scores={"知识库证据 a": -1.0})
    service = _chain_service(store, reranker)

    response = service.query(CHAIN_QUESTION, retrieve_k=5, rerank_k=5)

    assert response.answer_status == "insufficient_evidence"
    # 生成模块不该出现：拒答用固定文案，不调用生成模型（设计稿第 240 行）。
    assert _module_keys(response) == CHAIN_MODULE_SEQUENCE[:-2]
    preliminary = _module(response, "evidence.preliminary_gate")
    final = _module(response, "evidence.final_gate")
    assert (preliminary.status, preliminary.error_code) == ("failed", "INSUFFICIENT_EVIDENCE")
    assert (final.status, final.error_code) == ("failed", "INSUFFICIENT_EVIDENCE")
    # 拒答时合格计数照实上报，技术抽屉才说得出"查到了几条、为什么仍然不够"。
    assert final.metrics["kb_count"] == 0
    assert final.metrics["selected_count"] == 0
    assert _module(response, "evidence.fuse").status == "skipped"
