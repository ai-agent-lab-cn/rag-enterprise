"""无分类资料的检索边界。

两条规则必须同时成立，缺一条都会造成数据事故：

- 不指定分类时，无分类资料要能被检索到。否则一份刚上传、分类还没跑完的资料就等于
  从库里消失了，而用户看到的是「知识库里没有这个内容」。
- 指定分类时，无分类资料不得混进来。用户选了「制度规范」就是明确划定了范围，
  塞进来一份没有分类的资料是在绕过他的过滤条件。

三路召回（vector、lexical、hybrid）必须给出一致的答案——它们各有一套过滤实现，
一处漏改的表现是「换个检索模式结果就不一样」，而这几乎不可能在使用中被发现。
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from backend.app.config import Settings
from backend.app.database import apply_migrations
from backend.app.postgres_documents import IndexWorker, PostgresAsyncRAGService
from backend.app.postgres_repositories import PostgresCategoryRepository
from backend.app.schemas import QueryMetadataFilter
from backend.app.service import count_uncategorized

KNOWLEDGE_BASE_ID = "kb_default"
requires_postgres = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector"
)
GOVERNANCE_TEXT = "\n\n".join(["备份根目录固定覆盖 uploads 与 knowledge_bases 两个目录。" * 4] * 3)
UNSORTED_TEXT = "\n\n".join(["备份策略需要覆盖上传目录与知识库清单目录。" * 4] * 3)


class _FakeEmbedder:
    model_name = "test/embedding"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


def _settings(tmp_path: Path, database_url: str) -> Settings:
    """固定用 hybrid 建服务，具体走哪一路由 retrieve_candidates 的入参决定。

    ``retrieval_mode`` 配置项只接受 vector 与 hybrid；lexical 是召回内部的取值，
    生产不会单独配它，但它仍是一条真实代码路径，过滤漏改一样会出事。
    """

    return Settings(
        database_url=database_url,
        upload_path=tmp_path / "uploads",
        chunk_size=700,
        chunk_overlap=100,
        frontend_origin="http://localhost:5173",
        retrieval_mode="hybrid",
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


def _seed(settings: Settings, database_url: str) -> str:
    """索引两份资料：一份归入真实分类，一份完全没有分类。"""

    service = PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)
    sorted_doc = service.index_document("governance.md", GOVERNANCE_TEXT.encode(), KNOWLEDGE_BASE_ID)
    service.index_document("unsorted.md", UNSORTED_TEXT.encode(), KNOWLEDGE_BASE_ID)
    worker = IndexWorker(settings, _FakeEmbedder())
    processed = 0
    while processed < 30 and worker.run_once():
        processed += 1

    categories = PostgresCategoryRepository(database_url)
    category = categories.create(KNOWLEDGE_BASE_ID, "制度规范", "", 100)
    categories.assign(KNOWLEDGE_BASE_ID, [sorted_doc.document_id], str(category["category_id"]))
    # 另一份显式退回「没有分类」，确保它不是碰巧还没跑分类。
    with psycopg.connect(database_url) as connection:
        others = [
            row[0]
            for row in connection.execute(
                "SELECT document_id FROM documents WHERE document_id <> %s",
                (sorted_doc.document_id,),
            ).fetchall()
        ]
    categories.clear(KNOWLEDGE_BASE_ID, others)
    return str(category["category_id"])


def _filenames(candidates) -> set[str]:
    return {str(item.metadata.get("filename") or item.metadata.get("source")) for item in candidates}


def _service(settings: Settings) -> PostgresAsyncRAGService:
    """用生产类本身：它自带 pgvector 存储与词法索引，三路走的都是真实代码路径。"""

    return PostgresAsyncRAGService(settings, _FakeEmbedder(), None, None)


@requires_postgres
@pytest.mark.parametrize("mode", ["vector", "lexical", "hybrid"])
def test_unfiltered_retrieval_includes_uncategorized(tmp_path: Path, mode: str) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    _seed(settings, database_url)
    service = _service(settings)

    question = "备份覆盖哪些目录？"
    candidates = service.retrieve_candidates(
        question, list(_FakeEmbedder().encode([question])[0]), 10, KNOWLEDGE_BASE_ID,
        retrieval_mode=mode,
    )

    assert "unsorted.md" in _filenames(candidates), f"{mode} 路把无分类资料弄丢了"


@requires_postgres
@pytest.mark.parametrize("mode", ["vector", "lexical", "hybrid"])
def test_category_filter_excludes_uncategorized(tmp_path: Path, mode: str) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    category_id = _seed(settings, database_url)
    service = _service(settings)

    question = "备份覆盖哪些目录？"
    candidates = service.retrieve_candidates(
        question, list(_FakeEmbedder().encode([question])[0]), 10, KNOWLEDGE_BASE_ID,
        retrieval_mode=mode,
        filters=QueryMetadataFilter(category_ids=[category_id]),
    )

    names = _filenames(candidates)
    assert "unsorted.md" not in names, f"{mode} 路让无分类资料绕过了分类过滤"
    assert "governance.md" in names, "指定分类必须仍能召回该分类下的资料"


@requires_postgres
def test_category_name_filter_resolves_to_real_ids(tmp_path: Path) -> None:
    """名称过滤要先解析成真实分类 ID 再执行。

    按名字匹配 metadata 看起来等价，实则不是：分类改名后，历史资料的 metadata 里还留着
    旧名字，于是「按新名字过滤」查不到、「按旧名字过滤」反而查得到——而分类 ID 从不改变。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    category_id = _seed(settings, database_url)
    categories = PostgresCategoryRepository(database_url)
    service = _service(settings)
    question = "备份覆盖哪些目录？"

    def _ask(name: str):
        return service.retrieve_candidates(
            question, list(_FakeEmbedder().encode([question])[0]), 10, KNOWLEDGE_BASE_ID,
            filters=QueryMetadataFilter(categories=[name]),
        )

    assert "governance.md" in _filenames(_ask("制度规范"))

    categories.update(KNOWLEDGE_BASE_ID, category_id, "治理制度", "", 100, True)

    assert "governance.md" in _filenames(_ask("治理制度")), "改名后按新名字必须仍能过滤到"
    assert _ask("制度规范") == [], "旧名字不得继续命中"


@requires_postgres
def test_query_reports_uncategorized_candidate_count(tmp_path: Path) -> None:
    """回答要能解释「这次召回里有几条是没有分类的」。

    没有这个数字，用户看到一条无分类资料被引用时无法判断是检索边界的问题还是资料本身
    的问题，运维也无法评估分类覆盖率对回答质量的影响。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    _seed(settings, database_url)
    service = _service(settings)

    question = "备份覆盖哪些目录？"
    candidates = service.retrieve_candidates(
        question, list(_FakeEmbedder().encode([question])[0]), 10, KNOWLEDGE_BASE_ID,
    )

    assert count_uncategorized(candidates) == len(
        [item for item in candidates if item.metadata.get("category_id") is None]
    )
    assert count_uncategorized(candidates) > 0, "样本里必须真的有无分类候选"


@requires_postgres
def test_status_wording_is_never_treated_as_a_category(tmp_path: Path) -> None:
    """「待分类」「分类失败」是状态文案，不是分类名，拿它们过滤必须一无所获。

    伪分类时代这类文案恰好等于某个真实分类的名字，于是过滤会"碰巧有结果"，
    把状态和分类混为一谈。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    settings = _settings(tmp_path, database_url)
    _seed(settings, database_url)
    service = _service(settings)

    question = "备份覆盖哪些目录？"
    for wording in ("未分类", "待分类", "分类失败"):
        candidates = service.retrieve_candidates(
            question, list(_FakeEmbedder().encode([question])[0]), 10, KNOWLEDGE_BASE_ID,
            filters=QueryMetadataFilter(categories=[wording]),
        )
        assert candidates == [], f"状态文案「{wording}」不得召回任何资料"
