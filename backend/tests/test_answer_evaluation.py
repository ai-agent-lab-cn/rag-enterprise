import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.evaluation.answer_quality import (
    AnswerEvaluationRun,
    AnswerMetric,
    AnswerObservation,
    ClaimJudgement,
    EvaluatedSource,
    SemanticJudgement,
    evaluate_answers,
    load_answer_dataset,
)

DATASET_PATH = Path("backend/evaluation/datasets/answer_v1.json")


def _source(chunk_id: str = "architecture:chunk:00000") -> EvaluatedSource:
    return EvaluatedSource(
        chunk_id=chunk_id,
        filename="系统架构.md",
        paragraph=0,
        text="前端通过相对路径 /api 调用后端。",
    )


def _observations(with_judgements: bool = False) -> list[AnswerObservation]:
    dataset = load_answer_dataset(DATASET_PATH)
    observations = []
    for case in dataset.cases:
        sources = [_source(source_id) for source_id in case.acceptable_source_ids]
        if case.scenario == "answerable":
            answer = "回答与参考资料一致。[来源 1]"
        elif case.scenario == "source_conflict":
            answer = "两个来源存在冲突。[来源 1][来源 2]"
        else:
            answer = "当前场景已按稳定策略降级。"
        judgement = None
        if with_judgements and case.scenario == "answerable":
            judgement = SemanticJudgement(
                correctness=1,
                completeness=1,
                reason="覆盖参考要点。",
                evidence=["来源原文支持回答。"],
                claims=[
                    ClaimJudgement(
                        claim="回答与参考资料一致。",
                        citation_indices=[1],
                        supported=True,
                        contradicted=False,
                        attribution_correct=True,
                        reason="来源直接支持。",
                        evidence=["来源 1"],
                    )
                ],
            )
        observations.append(
            AnswerObservation(
                case_id=case.case_id,
                answer_status=case.expected_status,
                answer=answer,
                sources=sources,
                judgement=judgement,
            )
        )
    return observations


def _run(with_judgements: bool = False) -> AnswerEvaluationRun:
    models = {
        "embedding": "embedding@revision",
        "reranker": "reranker@revision",
        "generation": "generation@revision",
    }
    if with_judgements:
        models["judge"] = "independent-judge@revision"
    return AnswerEvaluationRun(
        dataset_id="rag-enterprise-answer-quality",
        dataset_version="1.0.0",
        commit="001ed43",
        run_at=datetime(2026, 8, 11, tzinfo=UTC),
        prompt_version="v3-grounded-answer-1",
        prompt_hash="a" * 64,
        models=models,
        parameters={"retrieve_k": 10, "rerank_k": 5},
        observations=_observations(with_judgements),
    )


def test_answer_dataset_is_versioned_and_covers_frozen_distribution() -> None:
    dataset = load_answer_dataset(DATASET_PATH)

    assert dataset.dataset_id == "rag-enterprise-answer-quality"
    assert dataset.version == "1.0.0"
    assert len(dataset.cases) == 30
    assert len(dataset.supplemental_evidence) == 4
    assert sum(case.scenario == "answerable" for case in dataset.cases) == 20
    assert len({case.case_id for case in dataset.cases}) == 30
    assert {case.knowledge_base_id for case in dataset.cases} >= {
        "kb_architecture",
        "kb_documents",
        "kb_empty",
        "kb_conflict",
    }


def test_fast_evaluation_checks_citations_and_failure_contract_without_judge() -> None:
    report = evaluate_answers(load_answer_dataset(DATASET_PATH), _run(), "fast")

    assert report.mode == "fast"
    assert report.official is False
    assert report.case_count == 30
    assert report.metrics.citation_validity.value == 1
    assert report.metrics.refusal_accuracy.value == 1
    assert report.metrics.failure_strategy_stability.value == 1
    assert report.metrics.answer_correctness is None
    assert report.semantic_judgements == {}
    assert report.passed is True


def test_invalid_citation_and_missing_preserved_sources_are_locatable() -> None:
    run = _run()
    run.observations[0].answer = "引用不存在的来源。[来源 9]"
    timeout = next(item for item in run.observations if item.case_id == "a029")
    timeout.sources = []

    report = evaluate_answers(load_answer_dataset(DATASET_PATH), run, "fast")

    first = report.deterministic_results[0]
    failed_timeout = next(item for item in report.deterministic_results if item.case_id == "a029")
    assert first.invalid_citation_indices == [9]
    assert first.response_stable is False
    assert failed_timeout.response_stable is False
    assert report.metrics.citation_validity.passed is False
    assert report.metrics.failure_strategy_stability.passed is False
    assert report.passed is False


def test_formal_evaluation_requires_independent_judge_and_claim_evidence() -> None:
    run = _run(with_judgements=True)
    report = evaluate_answers(load_answer_dataset(DATASET_PATH), run, "formal")

    assert report.metrics.answer_correctness.value == 1
    assert report.metrics.faithfulness.value == 1
    assert report.metrics.citation_support.value == 1
    assert report.metrics.claim_citation_coverage.value == 1
    assert report.metrics.unsupported_claim_rate.value == 0
    assert report.metrics.contradiction_rate.value == 0
    assert len(report.semantic_judgements) == 20
    assert report.semantic_judgements["a001"].reason == "覆盖参考要点。"
    assert report.passed is True

    run.models["judge"] = run.models["generation"]
    with pytest.raises(ValueError, match="不能作为唯一裁判"):
        evaluate_answers(load_answer_dataset(DATASET_PATH), run, "formal")


def test_run_rejects_invalid_prompt_hash_and_metric_direction_is_validated() -> None:
    payload = _run().model_dump(mode="json")
    payload["prompt_hash"] = "short"
    with pytest.raises(ValidationError):
        AnswerEvaluationRun.model_validate(payload)

    with pytest.raises(ValidationError, match="回答指标结论"):
        AnswerMetric(value=0.1, threshold=0.05, direction="maximum", passed=True)


def test_dataset_json_remains_human_reviewable() -> None:
    payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    assert all(
        set(case) >= {
            "knowledge_base_id",
            "reference_points",
            "acceptable_source_ids",
            "forbidden_claims",
            "expected_status",
        }
        for case in payload["cases"]
    )
