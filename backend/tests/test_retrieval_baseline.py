"""1.0.0 检索基线入口的回归覆盖。

该入口此前没有任何测试或 CI 覆盖，因此 V3 给 Chunk 增加 knowledge_base_id 之后，
``_evaluation_chunk`` 的 TypeError 静默存在了一个大版本，直到迁移到 pgvector 时才被发现。
这些用例保证它至少能跑通、能清理临时数据，不重复验证指标数值——那由真实模型运行负责。
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from backend.app.database import apply_migrations
from backend.evaluation import run_baseline as baseline_module
from backend.evaluation.dataset import load_dataset
from backend.evaluation.run_baseline import _evaluation_chunk, run_baseline

DATASET = Path("backend/evaluation/datasets/retrieval_v1.json")


class _FakeEmbedder:
    """按词元重合度产出可区分的向量，使检索顺序稳定且与问题相关。"""

    model_name = "test/embedding"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        buckets = [0.0] * 16
        for index, character in enumerate(text):
            buckets[ord(character) % 16] += 1.0 + index * 1e-6
        norm = sum(value * value for value in buckets) ** 0.5 or 1.0
        return [value / norm for value in buckets]


class _FakeReranker:
    model_name = "test/reranker"

    def score(self, question: str, texts: list[str]) -> list[float]:
        return [float(len(set(question) & set(text))) for text in texts]


def test_evaluation_chunk_carries_the_knowledge_base_id() -> None:
    """Chunk 自 V3 起要求 knowledge_base_id，缺失会让整个入口 TypeError。"""

    dataset = load_dataset(DATASET)

    chunk = _evaluation_chunk(dataset.chunks[0], 0, "kb_test")

    assert chunk.knowledge_base_id == "kb_test"
    assert chunk.metadata()["document_id"] == dataset.chunks[0].document_id


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_run_baseline_produces_a_report_and_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实 pgvector 读路径能跑通，且临时评测数据不留在库里。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    apply_migrations(database_url)
    monkeypatch.setattr(baseline_module, "resolved_model", lambda name: f"{name}@test")

    report = run_baseline(
        load_dataset(DATASET),
        commit="0123abc",
        database_url=database_url,
        embedder=_FakeEmbedder(),
        reranker=_FakeReranker(),
    )

    assert report.dataset_version == "1.0.0"
    assert report.query_count == 20
    assert report.parameters["vector_store"] == "pgvector"
    with psycopg.connect(database_url) as connection:
        for table in ("chunks", "documents", "document_versions", "data_sources",
                      "knowledge_bases", "index_versions"):
            remaining = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            assert int(remaining) == 0, f"{table} 残留 {remaining} 行临时评测数据"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_run_baseline_requires_a_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有隔离数据库时必须明确失败，而不是悄悄退回某个默认存储。"""

    monkeypatch.setattr(baseline_module, "resolved_model", lambda name: f"{name}@test")
    settings = baseline_module.get_settings()
    monkeypatch.setattr(settings, "database_url", None)

    with pytest.raises(ValueError, match="隔离数据库"):
        run_baseline(load_dataset(DATASET), commit="0123abc", database_url=None)
