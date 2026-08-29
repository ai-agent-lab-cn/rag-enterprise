from datetime import UTC, datetime, timedelta

import pytest

from backend.app.evaluation_governance import (
    BadCaseUpdate,
    evaluate_quality_gate,
    summarize_pipeline_runs,
    validate_bad_case_transition,
)


def test_acl_leak_forces_quality_gate_failure() -> None:
    result = evaluate_quality_gate(
        {
            "recall_at_5": {"value": 0.9, "threshold": 0.8, "direction": "minimum"},
            "acl_leak_count": {"value": 1, "threshold": 0, "direction": "maximum"},
        }
    )

    assert result.passed is False
    assert result.failed_metrics == ["acl_leak_count"]
    assert result.security_failed is True


def test_pipeline_summary_aggregates_counts_rates_and_latency() -> None:
    start = datetime(2026, 8, 30, tzinfo=UTC)
    summary = summarize_pipeline_runs(
        [
            {
                "status": "succeeded",
                "added_count": 3,
                "updated_count": 1,
                "deleted_count": 1,
                "skipped_count": 2,
                "failed_count": 0,
                "retry_count": 1,
                "started_at": start,
                "finished_at": start + timedelta(seconds=10),
            },
            {
                "status": "partial_failed",
                "added_count": 1,
                "updated_count": 0,
                "deleted_count": 0,
                "skipped_count": 0,
                "failed_count": 1,
                "retry_count": 2,
                "started_at": start,
                "finished_at": start + timedelta(seconds=30),
            },
        ]
    )

    assert summary.run_count == 2
    assert summary.added_count == 4
    assert summary.failed_count == 1
    assert summary.retry_count == 3
    assert summary.failure_rate == pytest.approx(0.5)
    assert summary.average_duration_ms == pytest.approx(20_000)


def test_bad_case_transition_requires_fix_commit_before_resolved() -> None:
    with pytest.raises(ValueError, match="修复提交"):
        validate_bad_case_transition("fixing", BadCaseUpdate(status="resolved"))

    update = BadCaseUpdate(status="resolved", fix_commit="a78a65d")
    assert validate_bad_case_transition("fixing", update).status == "resolved"


def test_regression_failure_reopens_resolved_bad_case() -> None:
    update = BadCaseUpdate(status="regression_added", regression_passed=False)

    assert validate_bad_case_transition("resolved", update).status == "confirmed"


def test_bad_case_transition_rejects_skipping_confirmation() -> None:
    with pytest.raises(ValueError, match="状态流转"):
        validate_bad_case_transition("new", BadCaseUpdate(status="resolved", fix_commit="a78a65d"))
