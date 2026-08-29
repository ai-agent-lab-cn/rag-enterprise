from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class QualityGateResult(BaseModel):
    passed: bool
    failed_metrics: list[str] = Field(default_factory=list)
    security_failed: bool = False


class PipelineSummary(BaseModel):
    run_count: int = 0
    added_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    retry_count: int = 0
    failure_rate: float = 0
    average_duration_ms: float = 0


BadCaseStatus = Literal[
    "new", "confirmed", "fixing", "resolved", "regression_added", "ignored"
]


class BadCaseUpdate(BaseModel):
    status: BadCaseStatus
    root_cause: str | None = None
    severity: Literal["low", "medium", "high", "critical"] | None = None
    assignee: str | None = None
    fix_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,40}$")
    regression_passed: bool | None = None


def evaluate_quality_gate(metrics: Mapping[str, Mapping[str, object]]) -> QualityGateResult:
    failed: list[str] = []
    for name, metric in metrics.items():
        value = float(metric["value"])
        threshold = float(metric["threshold"])
        direction = metric.get("direction", "minimum")
        passed = value <= threshold if direction == "maximum" else value >= threshold
        if not passed:
            failed.append(name)
    security_metrics = {"acl_leak_count", "unauthorized_citation_count"}
    return QualityGateResult(
        passed=not failed,
        failed_metrics=failed,
        security_failed=bool(security_metrics.intersection(failed)),
    )


def summarize_pipeline_runs(runs: Sequence[Mapping[str, object]]) -> PipelineSummary:
    summary = PipelineSummary(run_count=len(runs))
    durations: list[float] = []
    for run in runs:
        for field in (
            "added_count",
            "updated_count",
            "deleted_count",
            "skipped_count",
            "failed_count",
            "retry_count",
        ):
            setattr(summary, field, getattr(summary, field) + int(run.get(field) or 0))
        started_at = run.get("started_at")
        finished_at = run.get("finished_at")
        if isinstance(started_at, datetime) and isinstance(finished_at, datetime):
            durations.append((finished_at - started_at).total_seconds() * 1000)
    failed_runs = sum(run.get("status") in {"failed", "partial_failed"} for run in runs)
    summary.failure_rate = failed_runs / len(runs) if runs else 0
    summary.average_duration_ms = sum(durations) / len(durations) if durations else 0
    return summary


_TRANSITIONS: dict[str, set[str]] = {
    "new": {"confirmed", "ignored"},
    "confirmed": {"fixing", "ignored"},
    "fixing": {"resolved", "ignored"},
    "resolved": {"regression_added", "confirmed"},
    "regression_added": {"confirmed"},
    "ignored": {"confirmed"},
}


def validate_bad_case_transition(current: str, update: BadCaseUpdate) -> BadCaseUpdate:
    if update.status == "regression_added" and update.regression_passed is False:
        return update.model_copy(update={"status": "confirmed"})
    if update.status not in _TRANSITIONS.get(current, set()):
        raise ValueError(f"不允许从 {current} 流转到 {update.status}，请按状态流转处理")
    if update.status == "resolved" and not update.fix_commit:
        raise ValueError("标记 resolved 前必须关联修复提交")
    if update.status == "regression_added" and update.regression_passed is not True:
        raise ValueError("加入回归集前必须通过回归验证")
    return update
