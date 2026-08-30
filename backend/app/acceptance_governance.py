from typing import Literal

from pydantic import BaseModel, Field

AcceptanceStatus = Literal["passed", "failed", "blocked"]


class AcceptanceSnapshot(BaseModel):
    external_source_count: int = 0
    successful_sync_runs: int = 0
    incremental_change_count: int = 0
    deleted_count: int = 0
    acl_change_count: int = 0
    parsed_version_count: int = 0
    active_index_count: int = 0
    retrieval_report_passed: bool = False
    answer_report_passed: bool = False
    acl_leak_count: int = 0
    citation_failure_count: int = 0
    regression_failed_count: int = 0


class AcceptanceStep(BaseModel):
    step_key: str
    title: str
    status: AcceptanceStatus
    summary: str
    evidence: dict[str, object] = Field(default_factory=dict)


class AcceptanceResult(BaseModel):
    status: AcceptanceStatus
    steps: list[AcceptanceStep]


def evaluate_acceptance(snapshot: AcceptanceSnapshot) -> AcceptanceResult:
    external_ready = snapshot.external_source_count > 0
    sync_ready = snapshot.successful_sync_runs >= 2 and snapshot.incremental_change_count > 0
    parse_ready = snapshot.parsed_version_count > 0 and snapshot.active_index_count > 0
    retrieval_status: AcceptanceStatus = (
        "failed"
        if snapshot.acl_leak_count > 0
        else "passed"
        if snapshot.retrieval_report_passed
        else "blocked"
    )
    answer_status: AcceptanceStatus = (
        "failed"
        if snapshot.citation_failure_count > 0
        else "passed"
        if snapshot.answer_report_passed
        else "blocked"
    )
    regression_status: AcceptanceStatus = "failed" if snapshot.regression_failed_count > 0 else "passed"
    steps = [
        AcceptanceStep(
            step_key="runtime",
            title="运行环境",
            status="passed",
            summary="PostgreSQL Schema 与应用运行时可用。",
        ),
        AcceptanceStep(
            step_key="external_source",
            title="真实数据源",
            status="passed" if external_ready else "blocked",
            summary="已发现真实外部数据源。" if external_ready else "缺少 S3 兼容外部数据源。",
            evidence={"external_source_count": snapshot.external_source_count},
        ),
        AcceptanceStep(
            step_key="incremental_sync",
            title="增量同步",
            status="passed" if sync_ready else "blocked",
            summary="全量与增量同步证据完整。"
            if sync_ready
            else "至少需要两次成功同步及新增、更新或删除证据。",
            evidence={
                "successful_sync_runs": snapshot.successful_sync_runs,
                "incremental_change_count": snapshot.incremental_change_count,
                "deleted_count": snapshot.deleted_count,
                "acl_change_count": snapshot.acl_change_count,
            },
        ),
        AcceptanceStep(
            step_key="parse_and_index",
            title="解析与索引",
            status="passed" if parse_ready else "blocked",
            summary="解析版本与活动索引均可用。" if parse_ready else "缺少可用解析版本或活动 Index Version。",
            evidence={
                "parsed_version_count": snapshot.parsed_version_count,
                "active_index_count": snapshot.active_index_count,
            },
        ),
        AcceptanceStep(
            step_key="retrieval_and_acl",
            title="检索与 ACL",
            status=retrieval_status,
            summary="检索质量门通过且 ACL 泄漏为 0。"
            if retrieval_status == "passed"
            else "ACL 泄漏或检索正式报告尚未通过。",
            evidence={"acl_leak_count": snapshot.acl_leak_count},
        ),
        AcceptanceStep(
            step_key="trusted_answer",
            title="可信回答",
            status=answer_status,
            summary="回答与 Citation 质量门通过。"
            if answer_status == "passed"
            else "可信回答报告缺失或 Citation 安全门失败。",
            evidence={"citation_failure_count": snapshot.citation_failure_count},
        ),
        AcceptanceStep(
            step_key="evaluation_and_regression",
            title="评测与回归",
            status=regression_status,
            summary="回归集没有失败案例。" if regression_status == "passed" else "回归失败已阻止放行。",
            evidence={"regression_failed_count": snapshot.regression_failed_count},
        ),
    ]
    preliminary = _overall_status(steps)
    steps.append(
        AcceptanceStep(
            step_key="acceptance_report",
            title="验收报告",
            status=preliminary,
            summary="八阶段证据已汇总。" if preliminary == "passed" else "报告保留失败或阻塞步骤，不能放行。",
        )
    )
    return AcceptanceResult(status=_overall_status(steps), steps=steps)


def _overall_status(steps: list[AcceptanceStep]) -> AcceptanceStatus:
    if any(step.status == "failed" for step in steps):
        return "failed"
    if any(step.status == "blocked" for step in steps):
        return "blocked"
    return "passed"
