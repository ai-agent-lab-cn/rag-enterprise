from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class EvaluationMetric(BaseModel):
    value: float = Field(ge=0, le=1)
    threshold: float = Field(ge=0, le=1)
    baseline: float | None = Field(default=None, ge=0, le=1)
    passed: bool
    regressed: bool = False

    @model_validator(mode="after")
    def validate_conclusion(self) -> "EvaluationMetric":
        if self.passed != (self.value >= self.threshold and not self.regressed):
            raise ValueError("指标结论与数值、阈值或回退状态不一致")
        return self


def assess_metric(
    value: float,
    threshold: float,
    baseline: float | None = None,
    max_regression: float = 0.02,
) -> EvaluationMetric:
    """按照冻结阈值和防回退规则生成指标结论。"""

    if max_regression < 0:
        raise ValueError("允许回退幅度不能小于 0")
    regressed = baseline is not None and baseline - value > max_regression
    return EvaluationMetric(
        value=value,
        threshold=threshold,
        baseline=baseline,
        passed=value >= threshold and not regressed,
        regressed=regressed,
    )


class RetrievalEvaluationReport(BaseModel):
    report_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    run_at: datetime
    official: bool
    models: dict[str, str]
    parameters: dict[str, int | float | str | bool]
    query_count: int = Field(ge=1)
    recall_at_5: EvaluationMetric
    vector_mrr: EvaluationMetric
    rerank_mrr: EvaluationMetric
    # 1.0.0 的历史报告没有这一项，保持可选以免旧报告失效。
    rerank_recall_at_5: EvaluationMetric | None = None
    hybrid_mrr: EvaluationMetric | None = None

    @model_validator(mode="after")
    def validate_models(self) -> "RetrievalEvaluationReport":
        required = {"embedding", "reranker"}
        if not required.issubset(self.models):
            raise ValueError("报告必须记录 embedding 和 reranker 模型标识")
        return self

    @property
    def passed(self) -> bool:
        return all((self.recall_at_5.passed, self.vector_mrr.passed, self.rerank_mrr.passed))
