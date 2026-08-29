import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

EXPECTED_STATUSES = {
    "answerable": "answered",
    "insufficient_evidence": "insufficient_evidence",
    "source_conflict": "source_conflict",
    "retrieval_empty": "retrieval_empty",
    "generation_unavailable": "retrieval_only",
    "generation_timeout": "generation_failed",
}
CITATION_PATTERN = re.compile(r"\[来源\s+(\d+)\]")


class AnswerEvaluationCase(BaseModel):
    case_id: str = Field(pattern=r"^a\d{3}$")
    scenario: Literal[
        "answerable",
        "insufficient_evidence",
        "source_conflict",
        "retrieval_empty",
        "generation_unavailable",
        "generation_timeout",
    ]
    knowledge_base_id: str = Field(min_length=1)
    question: str = Field(min_length=2)
    reference_points: list[str]
    acceptable_source_ids: list[str]
    forbidden_claims: list[str]
    expected_status: str = Field(min_length=1)
    preserve_sources: bool = False

    @model_validator(mode="after")
    def validate_expected_status(self) -> "AnswerEvaluationCase":
        if self.expected_status != EXPECTED_STATUSES[self.scenario]:
            raise ValueError("场景与期望状态不一致")
        if self.scenario == "answerable" and (
            not self.reference_points or not self.acceptable_source_ids
        ):
            raise ValueError("可回答样本必须包含参考要点和可接受来源")
        return self


class SupplementalEvidence(BaseModel):
    chunk_id: str = Field(min_length=1)
    knowledge_base_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    text: str = Field(min_length=1)


class AnswerEvaluationDataset(BaseModel):
    dataset_id: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    language: str = "zh-CN"
    corpus_dataset_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    supplemental_evidence: list[SupplementalEvidence] = Field(default_factory=list)
    cases: list[AnswerEvaluationCase] = Field(min_length=30)

    @model_validator(mode="after")
    def validate_distribution(self) -> "AnswerEvaluationDataset":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("回答评测集包含重复 case_id")
        answerable = sum(case.scenario == "answerable" for case in self.cases)
        failures = len(self.cases) - answerable
        if answerable < 20 or failures < 10:
            raise ValueError("回答评测集至少需要 20 个可回答样本和 10 个失败样本")
        supplemental_ids = [item.chunk_id for item in self.supplemental_evidence]
        if len(supplemental_ids) != len(set(supplemental_ids)):
            raise ValueError("补充证据包含重复 chunk_id")
        conflict_sources = {
            source_id
            for case in self.cases
            if case.scenario == "source_conflict"
            for source_id in case.acceptable_source_ids
        }
        missing_conflict_sources = conflict_sources - set(supplemental_ids)
        if missing_conflict_sources:
            raise ValueError(f"冲突样本缺少固定证据：{sorted(missing_conflict_sources)}")
        return self


class EvaluatedSource(BaseModel):
    chunk_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    paragraph: int = Field(ge=0)
    text: str = Field(min_length=1)


class ClaimJudgement(BaseModel):
    claim: str = Field(min_length=1)
    citation_indices: list[int]
    supported: bool
    contradicted: bool
    attribution_correct: bool
    reason: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)


class SemanticJudgement(BaseModel):
    correctness: float = Field(ge=0, le=1)
    completeness: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    claims: list[ClaimJudgement]


class AnswerObservation(BaseModel):
    case_id: str = Field(pattern=r"^a\d{3}$")
    answer_status: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    sources: list[EvaluatedSource]
    error_code: str | None = None
    judgement: SemanticJudgement | None = None


class AnswerEvaluationRun(BaseModel):
    dataset_id: str
    dataset_version: str
    commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    run_at: datetime
    prompt_version: str = Field(min_length=1)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    models: dict[str, str]
    parameters: dict[str, int | float | str | bool]
    observations: list[AnswerObservation]


class DeterministicCaseResult(BaseModel):
    case_id: str
    expected_status: str
    actual_status: str
    citation_indices: list[int]
    invalid_citation_indices: list[int]
    unexpected_source_ids: list[str]
    status_correct: bool
    response_stable: bool


class AnswerMetric(BaseModel):
    value: float = Field(ge=0, le=1)
    threshold: float = Field(ge=0, le=1)
    direction: Literal["minimum", "maximum"] = "minimum"
    baseline: float | None = Field(default=None, ge=0, le=1)
    passed: bool
    regressed: bool = False

    @model_validator(mode="after")
    def validate_conclusion(self) -> "AnswerMetric":
        meets_threshold = (
            self.value >= self.threshold
            if self.direction == "minimum"
            else self.value <= self.threshold
        )
        if self.passed != (meets_threshold and not self.regressed):
            raise ValueError("回答指标结论与方向、数值、阈值或回退状态不一致")
        return self


class AnswerQualityMetrics(BaseModel):
    answer_correctness: AnswerMetric | None = None
    completeness: AnswerMetric | None = None
    faithfulness: AnswerMetric | None = None
    citation_validity: AnswerMetric
    citation_support: AnswerMetric | None = None
    claim_citation_coverage: AnswerMetric | None = None
    unsupported_claim_rate: AnswerMetric | None = None
    contradiction_rate: AnswerMetric | None = None
    refusal_accuracy: AnswerMetric
    source_conflict_accuracy: AnswerMetric | None = None
    failure_strategy_stability: AnswerMetric


class AnswerEvaluationReport(BaseModel):
    report_id: str
    mode: Literal["fast", "formal"]
    official: bool = False
    dataset_id: str
    dataset_version: str
    commit: str
    run_at: datetime
    prompt_version: str
    prompt_hash: str
    models: dict[str, str]
    parameters: dict[str, int | float | str | bool]
    case_count: int
    deterministic_results: list[DeterministicCaseResult]
    semantic_judgements: dict[str, SemanticJudgement]
    metrics: AnswerQualityMetrics
    passed: bool


class HumanReviewItem(BaseModel):
    case_id: str = Field(pattern=r"^a\d{3}$")
    accepted: bool
    reviewer: str = Field(min_length=1)
    reviewed_at: datetime
    notes: str = Field(min_length=1)


class HumanReviewRecord(BaseModel):
    report_id: str = Field(min_length=1)
    reviews: list[HumanReviewItem]


def promote_official_report(
    dataset: AnswerEvaluationDataset,
    report: AnswerEvaluationReport,
    review: HumanReviewRecord,
) -> AnswerEvaluationReport:
    """人工复核满足冻结比例后，才把通过的正式候选报告标为 official。"""

    if report.mode != "formal" or not report.passed:
        raise ValueError("只有通过全部质量门的正式候选报告可以放行")
    if review.report_id != report.report_id:
        raise ValueError("人工复核记录与报告不匹配")
    reviews = {item.case_id: item for item in review.reviews}
    if len(reviews) != len(review.reviews):
        raise ValueError("人工复核包含重复 case_id")
    failure_ids = {case.case_id for case in dataset.cases if case.scenario != "answerable"}
    answerable_ids = {case.case_id for case in dataset.cases if case.scenario == "answerable"}
    missing_failures = failure_ids - set(reviews)
    reviewed_answerable = answerable_ids & set(reviews)
    required_answerable = max(1, (len(answerable_ids) + 4) // 5)
    if missing_failures:
        raise ValueError(f"人工复核缺少失败样本：{sorted(missing_failures)}")
    if len(reviewed_answerable) < required_answerable:
        raise ValueError(f"人工复核至少需要 {required_answerable} 个通过样本")
    rejected = sorted(item.case_id for item in review.reviews if not item.accepted)
    if rejected:
        raise ValueError(f"人工复核未通过：{rejected}")
    return report.model_copy(update={"official": True})


def load_answer_dataset(path: Path) -> AnswerEvaluationDataset:
    return AnswerEvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))


def load_answer_run(path: Path) -> AnswerEvaluationRun:
    return AnswerEvaluationRun.model_validate_json(path.read_text(encoding="utf-8"))


def evaluate_answers(
    dataset: AnswerEvaluationDataset,
    run: AnswerEvaluationRun,
    mode: Literal["fast", "formal"],
) -> AnswerEvaluationReport:
    """生成快速确定性报告或带语义裁判明细的正式候选报告。"""

    if (run.dataset_id, run.dataset_version) != (dataset.dataset_id, dataset.version):
        raise ValueError("运行记录与评测集版本不一致")
    expected = {case.case_id: case for case in dataset.cases}
    observed = {item.case_id: item for item in run.observations}
    if len(observed) != len(run.observations):
        raise ValueError("运行记录包含重复 case_id")
    missing = set(expected) - set(observed)
    extra = set(observed) - set(expected)
    if missing or extra:
        raise ValueError(f"运行记录与评测 case 不一致：missing={sorted(missing)}, extra={sorted(extra)}")
    if mode == "formal":
        _validate_formal_judgements(dataset, run)

    deterministic = [_evaluate_case(expected[case_id], observed[case_id]) for case_id in expected]
    citation_total = sum(len(item.citation_indices) for item in deterministic)
    citation_invalid = sum(len(item.invalid_citation_indices) for item in deterministic)
    citation_validity = 1.0 if citation_total == 0 else 1 - citation_invalid / citation_total
    failure_results = [
        result
        for result in deterministic
        if expected[result.case_id].scenario != "answerable"
    ]
    refusal_accuracy = _average([result.status_correct for result in failure_results])
    conflict_results = [
        result
        for result in deterministic
        if expected[result.case_id].scenario == "source_conflict"
    ]
    source_conflict_accuracy = _average(
        [result.status_correct for result in conflict_results]
    )
    failure_stability = _average([result.response_stable for result in failure_results])

    semantic_metrics = _semantic_metrics(run.observations) if mode == "formal" else {}
    metrics = AnswerQualityMetrics(
        citation_validity=_assess_minimum(citation_validity, 1.0),
        refusal_accuracy=_assess_minimum(refusal_accuracy, 0.90),
        source_conflict_accuracy=_assess_minimum(source_conflict_accuracy, 0.90),
        failure_strategy_stability=_assess_minimum(failure_stability, 1.0),
        **semantic_metrics,
    )
    metric_values = [
        metrics.citation_validity,
        metrics.refusal_accuracy,
        metrics.source_conflict_accuracy,
        metrics.failure_strategy_stability,
    ]
    if mode == "formal":
        metric_values.extend(
            metric
            for metric in (
                metrics.answer_correctness,
                metrics.completeness,
                metrics.faithfulness,
                metrics.citation_support,
                metrics.claim_citation_coverage,
                metrics.unsupported_claim_rate,
                metrics.contradiction_rate,
            )
            if metric is not None
        )
    return AnswerEvaluationReport(
        report_id=f"answer-{mode}-{run.run_at:%Y%m%dT%H%M%SZ}",
        mode=mode,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        commit=run.commit,
        run_at=run.run_at,
        prompt_version=run.prompt_version,
        prompt_hash=run.prompt_hash,
        models=run.models,
        parameters=run.parameters,
        case_count=len(dataset.cases),
        deterministic_results=deterministic,
        semantic_judgements={
            observation.case_id: observation.judgement
            for observation in run.observations
            if mode == "formal" and observation.judgement is not None
        },
        metrics=metrics,
        passed=all(metric.passed for metric in metric_values),
    )


def _evaluate_case(case: AnswerEvaluationCase, observation: AnswerObservation) -> DeterministicCaseResult:
    citations = [int(value) for value in CITATION_PATTERN.findall(observation.answer)]
    invalid = [index for index in citations if index < 1 or index > len(observation.sources)]
    unexpected_sources = sorted(
        {
            source.chunk_id
            for source in observation.sources
            if case.acceptable_source_ids and source.chunk_id not in case.acceptable_source_ids
        }
    )
    status_correct = observation.answer_status == case.expected_status
    requires_citation = case.scenario in {"answerable", "source_conflict"}
    response_stable = bool(observation.answer.strip()) and not invalid
    if requires_citation:
        response_stable = response_stable and bool(citations)
    if case.preserve_sources:
        response_stable = response_stable and bool(observation.sources)
    return DeterministicCaseResult(
        case_id=case.case_id,
        expected_status=case.expected_status,
        actual_status=observation.answer_status,
        citation_indices=citations,
        invalid_citation_indices=invalid,
        unexpected_source_ids=unexpected_sources,
        status_correct=status_correct,
        response_stable=response_stable,
    )


def _validate_formal_judgements(
    dataset: AnswerEvaluationDataset,
    run: AnswerEvaluationRun,
) -> None:
    generation_model = run.models.get("generation")
    judge_model = run.models.get("judge")
    if not judge_model:
        raise ValueError("正式评测必须记录 judge 模型")
    if judge_model == generation_model:
        raise ValueError("生成模型不能作为唯一裁判")
    answerable = {case.case_id for case in dataset.cases if case.scenario == "answerable"}
    missing = [item.case_id for item in run.observations if item.case_id in answerable and not item.judgement]
    if missing:
        raise ValueError(f"正式评测缺少语义裁判结果：{missing}")


def _semantic_metrics(observations: list[AnswerObservation]) -> dict[str, AnswerMetric]:
    judgements = [item.judgement for item in observations if item.judgement is not None]
    claims = [claim for judgement in judgements for claim in judgement.claims]
    if not judgements or not claims:
        raise ValueError("正式评测必须包含可审计的语义裁判和声明明细")
    supported = _average([claim.supported and claim.attribution_correct for claim in claims])
    cited_claims = _average([bool(claim.citation_indices) for claim in claims])
    citation_claims = [claim for claim in claims if claim.citation_indices]
    citation_support = _average(
        [claim.supported and claim.attribution_correct for claim in citation_claims]
    )
    contradiction = _average([claim.contradicted for claim in claims])
    return {
        "answer_correctness": _assess_minimum(
            _average([item.correctness for item in judgements]), 0.80
        ),
        "completeness": _assess_minimum(
            _average([item.completeness for item in judgements]), 0.80
        ),
        "faithfulness": _assess_minimum(supported, 0.90),
        "citation_support": _assess_minimum(citation_support, 0.95),
        "claim_citation_coverage": _assess_minimum(cited_claims, 0.90),
        "unsupported_claim_rate": _assess_maximum(1 - supported, 0.05),
        "contradiction_rate": _assess_maximum(contradiction, 0.0),
    }


def _average(values: list[bool | float]) -> float:
    return sum(float(value) for value in values) / len(values) if values else 1.0


def _assess_minimum(value: float, minimum: float) -> AnswerMetric:
    return AnswerMetric(
        value=value,
        threshold=minimum,
        direction="minimum",
        passed=value >= minimum,
    )


def _assess_maximum(value: float, maximum: float) -> AnswerMetric:
    return AnswerMetric(
        value=value,
        threshold=maximum,
        direction="maximum",
        passed=value <= maximum,
    )


def write_report(path: Path, report: AnswerEvaluationReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
