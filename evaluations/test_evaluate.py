"""覆盖标注问题集评测入口。

这个入口长期没有任何测试：迁到 pgvector 之前它读本地 Chroma 目录，目录为空时不报错，
只打印三行 0.000，链路断掉与检索变差无法区分。下面的用例用假模型跑通完整链路，验证
标注能真正命中、临时数据跑完被清理。
"""

import json
import os

import psycopg
import pytest

from backend.app.database import apply_migrations
from backend.app.parsers import parse_structured_document
from evaluations.evaluate import DOCUMENT_PATH, QUESTIONS_PATH, evaluate, reciprocal_rank


class _FakeEmbedder:
    model_name = "test/embedding"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text) % 7), float(sum(map(ord, text)) % 11), 1.0] for text in texts]


class _FakeReranker:
    def score(self, query: str, passages: list[str]) -> list[float]:
        return [float(query[:1] in passage) for passage in passages]


class _Candidate:
    def __init__(self, filename: str, paragraph: int):
        self.metadata = {"filename": filename, "paragraph": paragraph}


def _reset_postgres(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    apply_migrations(database_url)


def test_every_label_points_at_a_real_paragraph_of_the_corpus() -> None:
    """标注的段落号来自 Markdown 解析器编号；语料改动后标注失效必须立刻暴露。"""

    sections = parse_structured_document(
        DOCUMENT_PATH.name, DOCUMENT_PATH.read_bytes()
    ).sections
    paragraphs = {section.paragraph for section in sections}

    for case in json.loads(QUESTIONS_PATH.read_text(encoding="utf-8")):
        assert case["expected_filename"] == DOCUMENT_PATH.name
        assert case["expected_paragraph"] in paragraphs


def test_reciprocal_rank_matches_on_filename_and_paragraph() -> None:
    results = [_Candidate("other.md", 2), _Candidate("a.md", 9), _Candidate("a.md", 2)]

    assert reciprocal_rank(results, "a.md", 2) == pytest.approx(1 / 3)
    assert reciprocal_rank(results, "a.md", 5) == 0.0
    assert reciprocal_rank([], "a.md", 2) == 0.0


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_evaluation_seeds_queries_and_cleans_its_isolated_knowledge_base() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset_postgres(database_url)

    metrics = evaluate(database_url, embedder=_FakeEmbedder(), reranker=_FakeReranker())

    assert metrics["cases"] == len(json.loads(QUESTIONS_PATH.read_text(encoding="utf-8")))
    # 假向量不保证命中，但三项指标都必须是合法比例，而不是链路断掉后的恒 0 假象。
    for key in ("vector_recall@10", "vector_mrr", "reranked_mrr"):
        assert 0.0 <= metrics[key] <= 1.0
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM knowledge_bases").fetchone()[0] == 0


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_evaluation_fails_loudly_when_seeded_chunks_are_not_retrievable(monkeypatch) -> None:
    """写进去却检索不到时必须报错。旧实现在这种情况下照常打印 0.000。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset_postgres(database_url)
    monkeypatch.setattr(
        "evaluations.evaluate.PostgresVectorStore.count", lambda self, knowledge_base_id=None: 0
    )

    with pytest.raises(RuntimeError, match="可检索分块数"):
        evaluate(database_url, embedder=_FakeEmbedder(), reranker=_FakeReranker())
