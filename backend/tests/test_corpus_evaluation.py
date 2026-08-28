import hashlib
import json
import os
import shutil
from pathlib import Path

import psycopg
import pytest
from pydantic import ValidationError

from backend.app.chunking import chunking_version, split_sections, stable_document_id
from backend.app.database import apply_migrations
from backend.app.index_versions import config_fingerprint
from backend.app.parsers import parse_document
from backend.evaluation import (
    CorpusEvaluationDataset,
    load_corpus_dataset,
    paragraph_key,
)
from backend.evaluation.report import RetrievalEvaluationReport
from backend.evaluation.run_corpus_baseline import (
    RECALL_AT_5_THRESHOLD,
    RERANK_MRR_THRESHOLD,
    VECTOR_MRR_THRESHOLD,
    run_corpus_baseline,
)

DATASET_PATH = Path("backend/evaluation/datasets/corpus_v2.json")


class _FakeEmbedder:
    model_name = "test/embedding"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text) % 7), float(sum(map(ord, text)) % 11), 1.0] for text in texts]


class _FakeReranker:
    def score(self, query: str, passages: list[str]) -> list[float]:
        return [float(query[:1] in passage) for passage in passages]


def _minimal_report_payload() -> dict:
    """1.0.0 报告的最小字段集，用于验证旧报告不因新增字段而失效。"""

    metric = {"value": 0.8, "threshold": 0.7, "passed": True}
    return {
        "report_id": "corpus-20260101T000000Z",
        "dataset_id": "rag-enterprise-corpus",
        "dataset_version": "2.0.0",
        "commit": "a" * 40,
        "run_at": "2026-01-01T00:00:00Z",
        "official": False,
        "models": {"embedding": "test/embedding", "reranker": "test/reranker"},
        "parameters": {"chunk_size": 700, "chunk_overlap": 100},
        "query_count": 100,
        "recall_at_5": metric,
        "vector_mrr": metric,
        "rerank_mrr": metric,
    }


def test_report_accepts_optional_config_fingerprint() -> None:
    payload = {**_minimal_report_payload(), "config_fingerprint": "a" * 64}

    assert RetrievalEvaluationReport(**payload).config_fingerprint == "a" * 64


def test_report_rejects_malformed_config_fingerprint() -> None:
    payload = {**_minimal_report_payload(), "config_fingerprint": "not-a-sha256"}

    with pytest.raises(ValidationError):
        RetrievalEvaluationReport(**payload)


def test_legacy_report_without_fingerprint_still_loads() -> None:
    """缺该字段的历史报告仍要能反序列化，但不能用于放行索引切换。"""

    assert RetrievalEvaluationReport(**_minimal_report_payload()).config_fingerprint is None


def _reset_postgres(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    apply_migrations(database_url)


def _copy_dataset(tmp_path: Path) -> Path:
    """把评测集与语料复制到临时目录，便于验证冻结校验会拒绝哪些改动。"""

    shutil.copy(DATASET_PATH, tmp_path / "corpus_v2.json")
    shutil.copytree(DATASET_PATH.parent / "corpus_v2", tmp_path / "corpus_v2")
    return tmp_path / "corpus_v2.json"


def test_corpus_dataset_is_versioned_and_anchored_to_real_documents() -> None:
    dataset, contents = load_corpus_dataset(DATASET_PATH)

    assert dataset.dataset_id == "rag-enterprise-corpus"
    assert dataset.version == "2.0.0"
    assert dataset.language == "zh-CN"
    assert len(dataset.queries) >= 100
    assert len({query.query_id for query in dataset.queries}) == len(dataset.queries)
    assert set(contents) == {document.filename for document in dataset.documents}
    # 标注必须真正指向语料内的段落，而不是 1.0.0 那种写死的分块列表。
    for document in dataset.documents:
        assert len(parse_document(document.filename, contents[document.filename])) == (
            document.paragraph_count
        )


def test_corpus_gate_is_frozen_before_the_first_reproducible_report() -> None:
    assert (RECALL_AT_5_THRESHOLD, VECTOR_MRR_THRESHOLD, RERANK_MRR_THRESHOLD) == (
        0.70,
        0.55,
        0.65,
    )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_corpus_baseline_uses_postgres_pipeline_and_cleans_temporary_data(monkeypatch) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset_postgres(database_url)
    dataset, contents = load_corpus_dataset(DATASET_PATH)
    monkeypatch.setattr(
        "backend.evaluation.run_corpus_baseline.resolved_model", lambda model: f"{model}@test"
    )

    report = run_corpus_baseline(
        dataset,
        contents,
        "a" * 40,
        700,
        100,
        database_url=database_url,
        embedder=_FakeEmbedder(),
        reranker=_FakeReranker(),
    )

    assert report.query_count == len(dataset.queries)
    assert report.parameters["chunk_count"] > 0
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM knowledge_bases").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_corpus_baseline_records_the_indexed_config_fingerprint(monkeypatch) -> None:
    """报告指纹必须与索引 Worker 写入索引版本的那份配置逐位相同，否则切换永不放行。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset_postgres(database_url)
    dataset, contents = load_corpus_dataset(DATASET_PATH)
    # resolved_model 只影响报告的 models 字段；指纹用的是 embedder.model_name。
    monkeypatch.setattr(
        "backend.evaluation.run_corpus_baseline.resolved_model", lambda model: f"{model}@test"
    )
    embedder = _FakeEmbedder()

    report = run_corpus_baseline(
        dataset,
        contents,
        "a" * 40,
        700,
        100,
        database_url=database_url,
        embedder=embedder,
        reranker=_FakeReranker(),
    )

    assert report.config_fingerprint == config_fingerprint(
        chunking_version(700, 100),
        embedder.model_name,
        len(embedder.encode(["维度探测"])[0]),
        {"chunk_size": 700, "chunk_overlap": 100},
    )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
@pytest.mark.parametrize("retrieval_mode", ["lexical", "hybrid"])
def test_corpus_baseline_supports_lexical_and_hybrid_retrieval(retrieval_mode, monkeypatch) -> None:
    """三种召回模式共用同一套精排与指标，报告必须如实记录所用模式。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset_postgres(database_url)
    dataset, contents = load_corpus_dataset(DATASET_PATH)
    monkeypatch.setattr(
        "backend.evaluation.run_corpus_baseline.resolved_model", lambda model: f"{model}@test"
    )

    report = run_corpus_baseline(
        dataset,
        contents,
        "a" * 40,
        700,
        100,
        database_url=database_url,
        embedder=_FakeEmbedder(),
        reranker=_FakeReranker(),
        retrieval_mode=retrieval_mode,
    )

    assert report.query_count == len(dataset.queries)
    assert report.parameters["retrieval_mode"] == retrieval_mode
    assert str(report.parameters["ranking_strategy"]).startswith("rrf_recall_then")
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM knowledge_bases").fetchone()[0] == 0


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_corpus_baseline_rejects_unknown_retrieval_mode() -> None:
    dataset, contents = load_corpus_dataset(DATASET_PATH)

    with pytest.raises(ValueError, match="retrieval_mode"):
        run_corpus_baseline(
            dataset,
            contents,
            "a" * 40,
            700,
            100,
            database_url=os.environ["TEST_DATABASE_URL"],
            retrieval_mode="bm25",
        )


def test_corpus_load_rejects_modified_document(tmp_path: Path) -> None:
    path = _copy_dataset(tmp_path)
    target = tmp_path / "corpus_v2" / "backup-recovery.md"
    target.write_bytes(target.read_bytes() + "\n\n新增段落会同时改变摘要和段落数。\n".encode())

    with pytest.raises(ValueError, match="语料文件已改动"):
        load_corpus_dataset(path)


def test_corpus_load_rejects_paragraph_count_drift(tmp_path: Path) -> None:
    """解析实现变更会让历史基线失去可比性，必须在评测开始前直接失败。"""

    path = _copy_dataset(tmp_path)
    target = tmp_path / "corpus_v2" / "release-rollback.md"
    # 保持 sha256 校验通过，只让记录的段落数与实际解析结果不一致。
    payload = json.loads(path.read_text(encoding="utf-8"))
    for document in payload["documents"]:
        if document["filename"] == "release-rollback.md":
            document["paragraph_count"] += 1
            document["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="解析实现已变更"):
        load_corpus_dataset(path)


def test_corpus_load_reports_missing_corpus_file(tmp_path: Path) -> None:
    path = _copy_dataset(tmp_path)
    (tmp_path / "corpus_v2" / "v4-readiness.md").unlink()

    with pytest.raises(ValueError, match="语料文件缺失"):
        load_corpus_dataset(path)


def test_corpus_dataset_rejects_out_of_range_annotation() -> None:
    with pytest.raises(ValidationError, match="越界的段落"):
        CorpusEvaluationDataset.model_validate(
            {
                "dataset_id": "sample",
                "version": "2.0.0",
                "description": "越界标注样本",
                "corpus_dir": "corpus",
                "documents": [{"filename": "a.md", "sha256": "0" * 64, "paragraph_count": 3}],
                "queries": [
                    {
                        "query_id": f"q{index:03d}",
                        "question": "问题",
                        "relevant": [{"filename": "a.md", "paragraph": 3 if index == 1 else 0}],
                    }
                    for index in range(1, 101)
                ],
            }
        )


def test_corpus_dataset_rejects_unknown_document() -> None:
    with pytest.raises(ValidationError, match="不存在的文档"):
        CorpusEvaluationDataset.model_validate(
            {
                "dataset_id": "sample",
                "version": "2.0.0",
                "description": "未知文档样本",
                "corpus_dir": "corpus",
                "documents": [{"filename": "a.md", "sha256": "0" * 64, "paragraph_count": 3}],
                "queries": [
                    {
                        "query_id": f"q{index:03d}",
                        "question": "问题",
                        "relevant": [
                            {"filename": "b.md" if index == 1 else "a.md", "paragraph": 0}
                        ],
                    }
                    for index in range(1, 101)
                ],
            }
        )


def test_paragraph_key_separates_same_index_across_documents() -> None:
    assert paragraph_key("a.md", 0) != paragraph_key("b.md", 0)
    assert paragraph_key("a.md", 3) == "a.md#3"


def test_chunk_size_changes_reach_the_pipeline_but_paragraph_coverage_holds() -> None:
    """切分参数必须真正作用于评测输入，同时段落级标注不因此失效。"""

    dataset, contents = load_corpus_dataset(DATASET_PATH)

    def build(chunk_size: int, overlap: int) -> list:
        chunks = []
        for document in dataset.documents:
            content = contents[document.filename]
            chunks.extend(
                split_sections(
                    stable_document_id(document.filename, content),
                    document.filename,
                    parse_document(document.filename, content),
                    chunk_size,
                    overlap,
                )
            )
        return chunks

    coarse = build(700, 100)
    fine = build(160, 20)

    assert len(fine) > len(coarse)
    # 无论切多细，标注锚定的段落都仍然被某个分块覆盖，指标才有可比性。
    annotated = {
        paragraph_key(item.filename, item.paragraph)
        for query in dataset.queries
        for item in query.relevant
    }
    for chunks in (coarse, fine):
        covered = {paragraph_key(chunk.filename, chunk.paragraph) for chunk in chunks}
        assert annotated <= covered
