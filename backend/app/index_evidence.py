"""索引版本的横向质量证据链。

这里只投影既有运行事实，不创建新的真相来源：索引版本、正式评测运行、三层验证报告
和生命周期事件仍分别由原表负责。证据链只把同一版本上的事实按发布顺序串起来。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _value(row: Mapping[str, Any] | None, key: str) -> Any:
    return row.get(key) if row else None


def _report_id(
    index_version: Mapping[str, Any],
    evaluation_run: Mapping[str, Any] | None,
    validation_report: Mapping[str, Any] | None,
) -> str | None:
    retrieval = _value(validation_report, "retrieval_result") or {}
    payload = _value(evaluation_run, "report_payload") or {}
    candidates = (
        retrieval.get("evaluation_report_id"),
        _value(validation_report, "evaluation_set_version"),
        index_version.get("evaluation_report_id"),
        payload.get("report_id"),
    )
    return next((str(candidate) for candidate in candidates if candidate), None)


def _reason(reasons: list[dict[str, str]], code: str, message: str) -> None:
    if not any(item["code"] == code for item in reasons):
        reasons.append({"code": code, "message": message})


def _validation_check(validation_report: Mapping[str, Any] | None, check_key: str) -> str | None:
    retrieval = _value(validation_report, "retrieval_result") or {}
    for check in retrieval.get("checks", []):
        if check.get("check_key") == check_key:
            return str(check.get("status")) if check.get("status") else None
    return None


def derive_evidence_governance(
    *,
    index_version: Mapping[str, Any],
    evaluation_run: Mapping[str, Any] | None,
    validation_report: Mapping[str, Any] | None,
    activation_event: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """从不可变事实派生证据语义；时间戳只展示，不参与有效性判断。"""

    reasons: list[dict[str, str]] = []
    report_id = _report_id(index_version, evaluation_run, validation_report)
    nodes = (
        evaluation_run is not None,
        report_id is not None,
        validation_report is not None,
        activation_event is not None,
    )
    traceability = "complete" if all(nodes) else "missing" if not any(nodes) else "partial"

    evaluation_report_id = None
    if evaluation_run:
        evaluation_report_id = (evaluation_run.get("report_payload") or {}).get("report_id")
    validation_report_id = None
    if validation_report:
        validation_report_id = (validation_report.get("retrieval_result") or {}).get(
            "evaluation_report_id"
        ) or validation_report.get("evaluation_set_version")
    report_ids = {
        str(item)
        for item in (evaluation_report_id, validation_report_id, index_version.get("evaluation_report_id"))
        if item
    }
    activation_validation_id = _value(activation_event, "validation_report_id")
    linked_validation_id = _value(validation_report, "validation_report_id")
    if len(report_ids) > 1 or (
        activation_validation_id and linked_validation_id and activation_validation_id != linked_validation_id
    ):
        traceability = "partial"
        _reason(reasons, "EVIDENCE_LINK_MISMATCH", "证据节点指向的报告不一致。")

    if evaluation_run is None:
        _reason(reasons, "EVALUATION_RUN_MISSING", "缺少该索引版本的正式评测运行记录。")
    if report_id is None:
        _reason(reasons, "FORMAL_REPORT_MISSING", "缺少可绑定的正式评测报告。")
    if validation_report is None:
        _reason(reasons, "VALIDATION_REPORT_MISSING", "缺少三层验证报告。")
    if (
        index_version.get("status") in {"active", "previous", "retired", "cleaned"}
        and activation_event is None
    ):
        _reason(reasons, "ACTIVATION_EVIDENCE_MISSING", "版本存在发布状态，但缺少激活事件证据。")

    version_fingerprint = index_version.get("config_fingerprint")
    config_completeness = index_version.get("config_completeness", "unknown")
    evaluation_fingerprint = _value(evaluation_run, "config_fingerprint")
    report_fingerprint = (_value(evaluation_run, "report_payload") or {}).get("config_fingerprint")
    config_check = _validation_check(validation_report, "config_fingerprint_matches")
    if config_completeness != "complete" or not version_fingerprint:
        configuration = "unknown"
        _reason(reasons, "CONFIG_FINGERPRINT_UNKNOWN", "历史版本缺少完整配置指纹，无法核对一致性。")
    elif config_check == "fail" or any(
        fingerprint and fingerprint != version_fingerprint
        for fingerprint in (evaluation_fingerprint, report_fingerprint)
    ):
        configuration = "mismatch"
        _reason(reasons, "CONFIG_FINGERPRINT_MISMATCH", "正式评测配置与索引版本不一致。")
    elif config_check == "pass" or evaluation_fingerprint or report_fingerprint:
        configuration = "match"
    else:
        configuration = "unknown"
        _reason(reasons, "CONFIG_FINGERPRINT_UNKNOWN", "缺少可用于配置一致性核对的证据。")

    if validation_report is None:
        validation = "missing"
    elif validation_report.get("report_source") != "standard" or config_completeness != "complete":
        validation = "historical"
        _reason(reasons, "HISTORICAL_EVIDENCE", "该结论来自历史证据，只可追溯，不能替代当前验证。")
    elif validation_report.get("status") == "pass":
        validation = "passed"
    elif validation_report.get("status") in {"failed", "cancelled"}:
        validation = "failed"
        _reason(reasons, "VALIDATION_FAILED", "三层验证未通过。")
    else:
        validation = "pending"
        _reason(reasons, "VALIDATION_PENDING", "三层验证尚未形成最终结论。")

    status = str(index_version.get("status") or "")
    if validation == "historical":
        release = "historical"
    elif (
        configuration == "mismatch"
        or validation == "failed"
        or status
        in {
            "build_failed",
            "validation_failed",
        }
    ):
        release = "blocked"
    elif status in {"active", "previous", "retired", "cleaned"}:
        release = (
            "released"
            if activation_event is not None and validation == "passed" and configuration == "match"
            else "blocked"
        )
    elif status == "ready" and validation == "passed" and configuration == "match":
        release = "eligible"
    else:
        release = "pending"

    return {
        "traceability": traceability,
        "configuration": configuration,
        "validation": validation,
        "release": release,
        "reasons": reasons,
    }


def assemble_index_evidence_chain(
    *,
    knowledge_base_id: str,
    index_version: Mapping[str, Any],
    evaluation_run: Mapping[str, Any] | None,
    validation_report: Mapping[str, Any] | None,
    activation_event: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """把已限定到同一知识库和版本的事实组装成稳定 API 结构。"""

    report_id = _report_id(index_version, evaluation_run, validation_report)
    report_payload = _value(evaluation_run, "report_payload") or {}
    retrieval = _value(validation_report, "retrieval_result") or {}

    evaluation_node = None
    if evaluation_run:
        evaluation_node = {
            "evaluation_run_id": evaluation_run["evaluation_run_id"],
            "status": evaluation_run["status"],
            "official": evaluation_run.get("official"),
            "passed": evaluation_run.get("passed"),
            "config_fingerprint": evaluation_run.get("config_fingerprint"),
            "created_at": evaluation_run.get("created_at"),
        }

    formal_report_node = None
    if report_id:
        formal_report_node = {
            "report_id": report_id,
            "official": _value(evaluation_run, "official"),
            "passed": _value(evaluation_run, "passed"),
            "config_fingerprint": (
                report_payload.get("config_fingerprint") or _value(evaluation_run, "config_fingerprint")
            ),
            "run_at": report_payload.get("run_at") or _value(evaluation_run, "run_at"),
        }

    validation_node = None
    if validation_report:
        validation_node = {
            "validation_report_id": validation_report["validation_report_id"],
            "status": validation_report["status"],
            "report_source": validation_report["report_source"],
            "evaluation_report_id": (
                retrieval.get("evaluation_report_id") or validation_report.get("evaluation_set_version")
            ),
            "created_at": validation_report.get("created_at"),
        }

    activation_node = None
    if activation_event:
        activation_node = {
            "event_id": activation_event["event_id"],
            "event_type": activation_event["event_type"],
            "actor_id": activation_event.get("actor_id"),
            "validation_report_id": activation_event.get("validation_report_id"),
            "created_at": activation_event.get("created_at"),
        }

    return {
        "knowledge_base_id": knowledge_base_id,
        "index_version_id": str(index_version["index_version_id"]),
        "version": {
            "index_version_id": index_version["index_version_id"],
            "version_no": index_version.get("version_no"),
            "status": index_version["status"],
            "config_fingerprint": index_version.get("config_fingerprint"),
        },
        "evaluation_run": evaluation_node,
        "formal_report": formal_report_node,
        "validation_report": validation_node,
        "activation": activation_node,
        "governance": derive_evidence_governance(
            index_version=index_version,
            evaluation_run=evaluation_run,
            validation_report=validation_report,
            activation_event=activation_event,
        ),
    }


def load_index_evidence_chain(
    database_url: str,
    knowledge_base_id: str,
    index_version_id: str,
) -> dict[str, Any] | None:
    """读取一个版本的证据链；每个查询都受知识库与版本边界约束。"""

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        version = connection.execute(
            """SELECT index_version_id, knowledge_base_id, version_no, status,
                      config_fingerprint, evaluation_report_id, validation_report_id,
                      config_completeness, activated_at
               FROM index_versions
               WHERE knowledge_base_id=%s AND index_version_id=%s""",
            (knowledge_base_id, index_version_id),
        ).fetchone()
        if version is None:
            return None

        validation = connection.execute(
            """SELECT vr.validation_report_id, vr.status, vr.report_source,
                      vr.evaluation_set_version, vr.retrieval_result, vr.created_at
               FROM validation_reports vr
               JOIN index_versions iv ON iv.index_version_id=vr.index_version_id
               WHERE iv.knowledge_base_id=%s AND vr.index_version_id=%s
               ORDER BY (vr.validation_report_id=%s) DESC, vr.created_at DESC
               LIMIT 1""",
            (knowledge_base_id, index_version_id, version.get("validation_report_id") or ""),
        ).fetchone()

        report_id = _report_id(version, None, validation)
        if report_id:
            evaluation = connection.execute(
                """SELECT er.evaluation_run_id, er.status, er.official, er.passed,
                          er.config_fingerprint, er.report_payload, er.run_at, er.created_at
                   FROM evaluation_runs er
                   WHERE er.knowledge_base_id=%s AND er.index_version_id=%s
                     AND er.evaluation_type='retrieval'
                     AND er.report_payload->>'report_id'=%s
                   ORDER BY er.finished_at DESC NULLS LAST, er.created_at DESC
                   LIMIT 1""",
                (knowledge_base_id, index_version_id, report_id),
            ).fetchone()
        else:
            evaluation = connection.execute(
                """SELECT er.evaluation_run_id, er.status, er.official, er.passed,
                          er.config_fingerprint, er.report_payload, er.run_at, er.created_at
                   FROM evaluation_runs er
                   WHERE er.knowledge_base_id=%s AND er.index_version_id=%s
                     AND er.evaluation_type='retrieval'
                   ORDER BY er.finished_at DESC NULLS LAST, er.created_at DESC
                   LIMIT 1""",
                (knowledge_base_id, index_version_id),
            ).fetchone()

        activation = connection.execute(
            """SELECT event_id, event_type, actor_id, validation_report_id, created_at
               FROM index_lifecycle_events
               WHERE knowledge_base_id=%s AND index_version_id=%s
                 AND event_type IN ('activated', 'rolled_back')
               ORDER BY created_at DESC, event_id DESC
               LIMIT 1""",
            (knowledge_base_id, index_version_id),
        ).fetchone()

    return assemble_index_evidence_chain(
        knowledge_base_id=knowledge_base_id,
        index_version=version,
        evaluation_run=evaluation,
        validation_report=validation,
        activation_event=activation,
    )
