from typing import Literal

from pydantic import BaseModel, Field

AcceptanceStatus = Literal["passed", "failed", "blocked"]


class AcceptanceSnapshot(BaseModel):
    runtime_ready: bool = False
    schema_version: int = 0
    required_schema_version: int = 0
    commit_sha: str | None = None
    external_source_count: int = 0
    successful_sync_runs: int = 0
    incremental_change_count: int = 0
    deleted_count: int = 0
    acl_change_count: int = 0
    parsed_version_count: int = 0
    active_index_count: int = 0
    active_index_version_id: str | None = None
    retrieval_report_passed: bool = False
    retrieval_report_id: str | None = None
    answer_report_passed: bool = False
    answer_report_id: str | None = None
    acl_leak_count: int | None = None
    citation_failure_count: int | None = None
    regression_case_count: int = 0
    regression_unverified_count: int = 0
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
    if snapshot.retrieval_report_id is None:
        retrieval_status: AcceptanceStatus = "blocked"
    elif not snapshot.retrieval_report_passed:
        retrieval_status = "failed"
    elif snapshot.acl_leak_count is None:
        retrieval_status = "blocked"
    elif snapshot.acl_leak_count > 0:
        retrieval_status = "failed"
    else:
        retrieval_status = "passed"
    if snapshot.answer_report_id is None:
        answer_status: AcceptanceStatus = "blocked"
    elif not snapshot.answer_report_passed:
        answer_status = "failed"
    elif snapshot.citation_failure_count is None:
        answer_status = "blocked"
    elif snapshot.citation_failure_count > 0:
        answer_status = "failed"
    else:
        answer_status = "passed"
    if snapshot.regression_failed_count > 0:
        regression_status: AcceptanceStatus = "failed"
    elif snapshot.regression_case_count == 0 or snapshot.regression_unverified_count > 0:
        regression_status = "blocked"
    else:
        regression_status = "passed"
    steps = [
        AcceptanceStep(
            step_key="runtime",
            title="运行环境",
            status="passed" if snapshot.runtime_ready else "blocked",
            summary=_runtime_summary(snapshot),
            evidence={
                "schema_version": snapshot.schema_version,
                "required_schema_version": snapshot.required_schema_version,
                **({"commit_sha": snapshot.commit_sha} if snapshot.commit_sha else {}),
            },
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
                **(
                    {"active_index_version_id": snapshot.active_index_version_id}
                    if snapshot.active_index_version_id
                    else {}
                ),
            },
        ),
        AcceptanceStep(
            step_key="retrieval_and_acl",
            title="检索与 ACL",
            status=retrieval_status,
            summary=_retrieval_summary(snapshot),
            evidence={
                "acl_leak_count": snapshot.acl_leak_count,
                **(
                    {"retrieval_report_id": snapshot.retrieval_report_id}
                    if snapshot.retrieval_report_id
                    else {}
                ),
            },
        ),
        AcceptanceStep(
            step_key="trusted_answer",
            title="可信回答",
            status=answer_status,
            summary=_answer_summary(snapshot),
            evidence={
                "citation_failure_count": snapshot.citation_failure_count,
                **({"answer_report_id": snapshot.answer_report_id} if snapshot.answer_report_id else {}),
            },
        ),
        AcceptanceStep(
            step_key="evaluation_and_regression",
            title="评测与回归",
            status=regression_status,
            summary=_regression_summary(snapshot),
            evidence={
                "regression_case_count": snapshot.regression_case_count,
                "regression_unverified_count": snapshot.regression_unverified_count,
                "regression_failed_count": snapshot.regression_failed_count,
            },
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


def _runtime_summary(snapshot: AcceptanceSnapshot) -> str:
    if snapshot.runtime_ready:
        return "Schema 与应用 Commit 均可追踪。"
    schema_ready = snapshot.schema_version == snapshot.required_schema_version
    if not schema_ready and not snapshot.commit_sha:
        return (
            f"Schema V{snapshot.schema_version} 未达到要求 V{snapshot.required_schema_version}，"
            "且应用 Commit 不可追踪。"
        )
    if not schema_ready:
        return f"Schema V{snapshot.schema_version} 未达到要求 V{snapshot.required_schema_version}。"
    return "应用 Commit 不可追踪；请配置有效的 APP_COMMIT_SHA。"


def _retrieval_summary(snapshot: AcceptanceSnapshot) -> str:
    if snapshot.retrieval_report_id is None:
        return "缺少绑定当前活动索引的正式检索报告。"
    if not snapshot.retrieval_report_passed:
        return "正式检索报告未达到冻结阈值。"
    if snapshot.acl_leak_count is None:
        return "正式检索报告缺少 ACL 泄漏指标。"
    if snapshot.acl_leak_count > 0:
        return f"检测到 {snapshot.acl_leak_count} 条 ACL 泄漏。"
    return "检索质量门通过且 ACL 泄漏为 0。"


def _answer_summary(snapshot: AcceptanceSnapshot) -> str:
    if snapshot.answer_report_id is None:
        return "缺少绑定当前知识库与活动索引的正式回答报告。"
    if not snapshot.answer_report_passed:
        return "正式回答报告未通过质量门。"
    if snapshot.citation_failure_count is None:
        return "正式回答报告缺少 Citation 失败计数。"
    if snapshot.citation_failure_count > 0:
        return f"检测到 {snapshot.citation_failure_count} 个 Citation 失败。"
    return "回答与 Citation 质量门通过。"


def _regression_summary(snapshot: AcceptanceSnapshot) -> str:
    if snapshot.regression_failed_count > 0:
        return f"存在 {snapshot.regression_failed_count} 个回归失败案例。"
    if snapshot.regression_case_count == 0:
        return "当前知识库尚未建立回归案例。"
    if snapshot.regression_unverified_count > 0:
        return f"还有 {snapshot.regression_unverified_count} 个回归案例未完成验证。"
    return "回归集没有失败案例。"


def _overall_status(steps: list[AcceptanceStep]) -> AcceptanceStatus:
    if any(step.status == "failed" for step in steps):
        return "failed"
    if any(step.status == "blocked" for step in steps):
        return "blocked"
    return "passed"
