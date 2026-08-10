import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.evaluation import (
    EvaluationMetric,
    EvaluationQuery,
    RetrievalEvaluationReport,
    assess_metric,
    evaluate_rankings,
    load_dataset,
    recall_at_k,
    reciprocal_rank,
)

DATASET_PATH = Path("backend/evaluation/datasets/retrieval_v1.json")
BASELINE_REPORT_PATH = Path("backend/evaluation/reports/retrieval_v1_baseline.json")
OPTIMIZED_REPORT_PATH = Path("backend/evaluation/reports/retrieval_v1_optimized.json")


def test_retrieval_dataset_is_versioned_and_reviewable() -> None:
    dataset = load_dataset(DATASET_PATH)

    assert dataset.dataset_id == "rag-enterprise-retrieval"
    assert dataset.version == "1.0.0"
    assert dataset.language == "zh-CN"
    assert len(dataset.queries) == 20
    assert len(dataset.chunks) == 20
    assert len({query.query_id for query in dataset.queries}) == 20
    assert all(query.relevant_chunk_ids for query in dataset.queries)


def test_official_baseline_report_is_complete_and_passes_frozen_gate() -> None:
    # 将提交进仓库的报告重新走一遍 Pydantic 校验，防止手工编辑造成字段或结论不一致。
    report = RetrievalEvaluationReport.model_validate(
        json.loads(BASELINE_REPORT_PATH.read_text(encoding="utf-8"))
    )

    assert report.official is True
    assert report.dataset_version == "1.0.0"
    assert report.query_count == 20
    assert all("@" in model for model in report.models.values())
    assert report.recall_at_5.threshold == 0.80
    assert report.vector_mrr.threshold == 0.60
    assert report.rerank_mrr.threshold == 0.70
    assert report.passed is True


def test_optimized_report_improves_rerank_mrr_without_regression() -> None:
    """正式优化报告必须引用被测提交，并满足相对基线的无回退契约。"""

    report = RetrievalEvaluationReport.model_validate(
        json.loads(OPTIMIZED_REPORT_PATH.read_text(encoding="utf-8"))
    )

    assert report.official is True
    assert report.commit == "56fb090aba680172c8ce7a324422fa78a05f8bfb"
    assert report.parameters["ranking_strategy"] == "minmax_weighted_fusion"
    assert report.parameters["vector_score_weight"] == 0.15
    assert report.recall_at_5.value == report.recall_at_5.baseline == 1.0
    assert report.vector_mrr.value == report.vector_mrr.baseline
    assert report.rerank_mrr.value == pytest.approx(0.975)
    assert report.rerank_mrr.value > report.rerank_mrr.baseline
    assert all(
        metric.regressed is False
        for metric in (report.recall_at_5, report.vector_mrr, report.rerank_mrr)
    )
    assert report.passed is True


def test_recall_at_k_covers_relevant_chunks_without_counting_duplicates() -> None:
    ranked = ["chunk-a", "chunk-a", "chunk-b", "chunk-c"]

    assert recall_at_k(ranked, {"chunk-a", "chunk-b"}, 2) == 0.5
    assert recall_at_k(ranked, {"chunk-a", "chunk-b"}, 3) == 1.0


def test_recall_at_k_validates_boundary() -> None:
    with pytest.raises(ValueError, match="k 必须"):
        recall_at_k(["chunk-a"], {"chunk-a"}, 0)
    with pytest.raises(ValueError, match="相关分块不能为空"):
        recall_at_k(["chunk-a"], set(), 5)


def test_reciprocal_rank_uses_first_relevant_result() -> None:
    assert reciprocal_rank(["other", "relevant", "relevant-2"], {"relevant", "relevant-2"}) == 0.5
    assert reciprocal_rank(["other"], {"relevant"}) == 0.0


def test_evaluate_rankings_preserves_vector_and_rerank_order() -> None:
    queries = [
        EvaluationQuery(query_id="q001", question="第一个问题", relevant_chunk_ids=["a"]),
        EvaluationQuery(query_id="q002", question="第二个问题", relevant_chunk_ids=["b"]),
    ]
    vector_rankings = {"q001": ["x", "a"], "q002": ["b", "x"]}
    reranked_rankings = {"q001": ["a", "x"], "q002": ["x", "b"]}

    metrics = evaluate_rankings(queries, vector_rankings, reranked_rankings)

    assert metrics.query_count == 2
    assert metrics.recall_at_5 == 1.0
    assert metrics.vector_mrr == 0.75
    assert metrics.rerank_mrr == 0.75


def test_evaluate_rankings_rejects_missing_query_results() -> None:
    query = EvaluationQuery(query_id="q001", question="测试问题", relevant_chunk_ids=["a"])

    with pytest.raises(ValueError, match="向量召回缺少问题结果"):
        evaluate_rankings([query], {}, {"q001": ["a"]})
    with pytest.raises(ValueError, match="精排缺少问题结果"):
        evaluate_rankings([query], {"q001": ["a"]}, {})


def test_report_records_required_context_and_quality_gate() -> None:
    report = RetrievalEvaluationReport(
        report_id="retrieval-20260807-001",
        dataset_id="rag-enterprise-retrieval",
        dataset_version="1.0.0",
        commit="1f1eb3a",
        run_at=datetime(2026, 8, 7, tzinfo=UTC),
        official=True,
        models={"embedding": "embedding-model@revision", "reranker": "reranker-model@revision"},
        parameters={"retrieve_k": 10, "rerank_k": 5},
        query_count=20,
        recall_at_5=EvaluationMetric(value=0.85, threshold=0.80, passed=True),
        vector_mrr=EvaluationMetric(value=0.65, threshold=0.60, passed=True),
        rerank_mrr=EvaluationMetric(value=0.75, threshold=0.70, passed=True),
    )

    assert report.passed is True
    assert report.model_dump(mode="json")["dataset_version"] == "1.0.0"


def test_metric_assessment_applies_threshold_and_regression_boundary() -> None:
    accepted_drop = assess_metric(value=0.80, threshold=0.80, baseline=0.82)
    rejected_drop = assess_metric(value=0.80, threshold=0.80, baseline=0.821)

    assert accepted_drop.passed is True
    assert accepted_drop.regressed is False
    assert rejected_drop.passed is False
    assert rejected_drop.regressed is True


def test_report_rejects_inconsistent_metric_conclusion_and_missing_model() -> None:
    with pytest.raises(ValidationError, match="指标结论"):
        EvaluationMetric(value=0.79, threshold=0.80, passed=True)

    with pytest.raises(ValidationError, match="embedding 和 reranker"):
        RetrievalEvaluationReport(
            report_id="retrieval-20260807-002",
            dataset_id="rag-enterprise-retrieval",
            dataset_version="1.0.0",
            commit="1f1eb3a",
            run_at=datetime(2026, 8, 7, tzinfo=UTC),
            official=False,
            models={"embedding": "fake"},
            parameters={"retrieve_k": 10},
            query_count=20,
            recall_at_5=EvaluationMetric(value=0.0, threshold=0.80, passed=False),
            vector_mrr=EvaluationMetric(value=0.0, threshold=0.60, passed=False),
            rerank_mrr=EvaluationMetric(value=0.0, threshold=0.70, passed=False),
        )
