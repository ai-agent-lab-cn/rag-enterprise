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
from backend.app.postgres_documents import IndexWorker, PostgresAsyncRAGService
from backend.app.postgres_repositories import PostgresDataSourceRepository
from backend.app.retrieval_access import RetrievalAccessContext, can_retrieve_metadata

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
        return {key: data.get(key) for key in ("acl_version", "allow_user_ids", "deny_user_ids", "retrieval_status")}

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

    public = service.index_document("public.md", DOCUMENT_TEXT.encode(), KNOWLEDGE_BASE_ID)
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
