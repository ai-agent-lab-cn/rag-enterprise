"""索引版本的三层发布门禁。

门禁在这里，不在调用方。此前唯一的检查是 ``switch_to_version`` 拿调用方传进来的
``RetrievalEvaluationReport`` 比对指纹与回退——报告不落库，谁调用谁提供，库里查不到
任何一次验证发生过；而完整性与技术两层根本不存在，索引漏掉一半文档、向量维度对不上、
分块跨版本混写，都能一路通过放行。

三层的分工：

- **完整性**回答「这个版本是不是把该建的都建了」。它把冻结的文档快照当作应有的清单，
  逐个核对，而不是比总数——总数相等可以由「漏建 A、多建 B」凑出来。
- **技术**回答「建出来的东西本身是不是对的」。维度、版本归属、必填字段。
- **检索质量**回答「换过去是变好还是变坏」。这一层需要跑评测，成本高，因此复用既有的
  离线评测报告，不在门禁里重跑。

任一 critical 项失败，总判定就是 failed。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .errors import AppError
from .index_versions import COMPONENT_SCHEMA_VERSIONS, record_lifecycle_event, switch_to_version
from .parsers import PARSER_SCHEMA_VERSION

if TYPE_CHECKING:
    from backend.evaluation.report import RetrievalEvaluationReport

# 门禁规则本身的版本。判定规则改变时递增，历史报告因此仍能解释它当时是按哪套规则通过的。
VALIDATION_POLICY_VERSION = "v2"
VALIDATION_POLICY: dict[str, object] = {
    "version": VALIDATION_POLICY_VERSION,
    "comparison_mode": "relative_non_regression",
    "critical_checks": {
        "integrity": [
            "missing_document", "orphan_chunk", "duplicate_chunk",
            "chunk_index_continuity", "acl_consistency", "non_empty_coverage",
        ],
        "technical": [
            "embedding_dimension", "single_knowledge_base", "required_fields",
            "component_manifest", "vector_index_health", "vector_lane_health",
            "keyword_index_health", "metadata_lane_health", "metadata_schema",
            "acl_structure", "citation_structure",
        ],
        "retrieval_quality": [
            "no_metric_regression", "config_fingerprint_matches", "recall_at_10_present",
            "ndcg_at_10_present", "metadata_filter_accuracy_present", "acl_leak_count",
        ],
    },
    "warning_checks": ["legacy_advanced_metric_missing"],
    "legacy_behavior": "unknown configuration is retained as history and advanced checks are warnings",
}


def get_validation_policy() -> dict[str, object]:
    """返回当前不可变门禁策略；历史报告继续按其自身 policy_version 解释。"""

    return VALIDATION_POLICY


def _check(
    key: str, passed: bool, *, expected: object, actual: object, severity: str = "critical"
) -> dict[str, Any]:
    return {
        "check_key": key,
        "status": "pass" if passed else "fail",
        "expected": expected,
        "actual": actual,
        "severity": severity,
    }


def check_integrity(
    connection: psycopg.Connection[Any], index_version_id: str
) -> dict[str, Any]:
    """完整性：该版本是否覆盖了它冻结的那批文档，且没有多余分块。

    没有文档快照的版本（快照机制之前创建的）无法核对清单，明确标记为 unknown 而不是
    默默判过——判过等于凭空宣称一件查不到证据的事。

    **``orphan_chunk`` 只对尚未激活的候选版本成立。** 同步会把新资料的分块写进 active
    版本，而该版本的快照是构建时冻结的——激活之后分块集合必然超出快照。两者不冲突的
    唯一原因是 ``validate_index_version`` 拒绝验证 active 版本。放宽那条状态限制之前，
    先看 ``test_an_activated_version_may_accumulate_chunks_outside_its_snapshot``。
    """

    with connection.cursor(row_factory=dict_row) as cursor:
        version = cursor.execute(
            """SELECT knowledge_base_id, document_snapshot_id, config_completeness
               FROM index_versions WHERE index_version_id = %s""",
            (index_version_id,),
        ).fetchone()
        if version is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
        if not version["document_snapshot_id"]:
            return {
                "layer": "integrity",
                "status": "unknown",
                "checks": [],
                "note": "该版本创建于文档快照机制之前，无法核对输入清单。",
            }
        snapshot_id = str(version["document_snapshot_id"])

        # 应建未建：快照成员里没有任何分块的。
        missing = cursor.execute(
            """SELECT count(*) AS total FROM document_snapshot_members m
               WHERE m.document_snapshot_id = %s AND m.inclusion_status = 'included'
                 AND NOT EXISTS (
                     SELECT 1 FROM chunks c
                     WHERE c.index_version_id = %s
                       AND c.document_version_id = m.document_version_id)""",
            (snapshot_id, index_version_id),
        ).fetchone()

        # 不该建却建了：该版本下有分块，但对应文档版本不在快照里。
        orphan = cursor.execute(
            """SELECT count(DISTINCT c.document_version_id) AS total FROM chunks c
               WHERE c.index_version_id = %s
                 AND NOT EXISTS (
                     SELECT 1 FROM document_snapshot_members m
                     WHERE m.document_snapshot_id = %s
                       AND m.inclusion_status = 'included'
                       AND m.document_version_id = c.document_version_id)""",
            (index_version_id, snapshot_id),
        ).fetchone()

        # 同一文档版本在同一索引版本里出现重复 chunk_index。
        duplicate = cursor.execute(
            """SELECT count(*) AS total FROM (
                   SELECT document_version_id, chunk_index FROM chunks
                   WHERE index_version_id = %s
                   GROUP BY document_version_id, chunk_index HAVING count(*) > 1
               ) AS duplicates""",
            (index_version_id,),
        ).fetchone()

        discontinuous = cursor.execute(
            """SELECT count(*) AS total FROM (
                   SELECT document_version_id
                   FROM chunks WHERE index_version_id=%s
                   GROUP BY document_version_id
                   HAVING min(chunk_index)<>0
                      OR max(chunk_index)+1<>count(DISTINCT chunk_index)
               ) AS broken""",
            (index_version_id,),
        ).fetchone()

        acl_mismatch = {"total": 0}
        if str(version["config_completeness"]) == "complete":
            acl_mismatch = cursor.execute(
                """SELECT count(*) AS total
                   FROM chunks c
                   JOIN document_snapshot_members m
                     ON m.document_snapshot_id=%s
                    AND m.document_version_id=c.document_version_id
                    AND m.inclusion_status='included'
                   JOIN documents d
                     ON d.knowledge_base_id=c.knowledge_base_id
                    AND d.document_id=m.document_id
                   JOIN data_sources s ON s.data_source_id=d.data_source_id
                   WHERE c.index_version_id=%s AND (
                       COALESCE(c.metadata->'allow_user_ids','[]'::jsonb)
                         <> COALESCE(d.metadata->'allow_user_ids','[]'::jsonb)
                       OR COALESCE(c.metadata->'deny_user_ids','[]'::jsonb)
                         <> COALESCE(d.metadata->'deny_user_ids','[]'::jsonb)
                       OR COALESCE(c.metadata->'data_source_acl',
                                   '{"version":1,"allow_user_ids":[],"deny_user_ids":[]}'::jsonb)
                         <> COALESCE(s.acl,
                                   '{"version":1,"allow_user_ids":[],"deny_user_ids":[]}'::jsonb)
                   )""",
                (snapshot_id, index_version_id),
            ).fetchone()

        included = cursor.execute(
            """SELECT count(*) AS total FROM document_snapshot_members
               WHERE document_snapshot_id = %s AND inclusion_status = 'included'""",
            (snapshot_id,),
        ).fetchone()

    checks = [
        _check("missing_document", int(missing["total"]) == 0,
               expected=0, actual=int(missing["total"])),
        _check("orphan_chunk", int(orphan["total"]) == 0,
               expected=0, actual=int(orphan["total"])),
        _check("duplicate_chunk", int(duplicate["total"]) == 0,
               expected=0, actual=int(duplicate["total"])),
        _check("chunk_index_continuity", int(discontinuous["total"]) == 0,
               expected=0, actual=int(discontinuous["total"])),
        _check(
            "acl_consistency",
            int(acl_mismatch["total"]) == 0,
            expected=0,
            actual=int(acl_mismatch["total"]),
            severity=(
                "critical" if str(version["config_completeness"]) == "complete" else "warning"
            ),
        ),
        # 覆盖 0 篇文档在算术上「完整」，但切过去等于把知识库变成空索引。
        _check("non_empty_coverage", int(included["total"]) > 0,
               expected="> 0", actual=int(included["total"])),
    ]
    failed = [
        item for item in checks
        if item["status"] == "fail" and item["severity"] == "critical"
    ]
    return {
        "layer": "integrity",
        "status": "fail" if failed else "pass",
        "checks": checks,
        "document_snapshot_id": snapshot_id,
    }


def check_technical(
    connection: psycopg.Connection[Any], index_version_id: str
) -> dict[str, Any]:
    """技术：建出来的分块本身是否符合版本声明。"""

    with connection.cursor(row_factory=dict_row) as cursor:
        version = cursor.execute(
            """SELECT knowledge_base_id, embedding_dimension, component_manifest,
                      config_completeness
               FROM index_versions WHERE index_version_id = %s""",
            (index_version_id,),
        ).fetchone()
        if version is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
        declared = int(version["embedding_dimension"])

        # 实际维度取自数据本身，而不是再读一次声明值——门禁要比对的正是「声明」与
        # 「实际」是否一致，两边都读声明就永远相等。
        dimensions = cursor.execute(
            """SELECT DISTINCT vector_dims(embedding) AS dimension FROM chunks
               WHERE index_version_id = %s""",
            (index_version_id,),
        ).fetchall()
        actual = sorted(int(row["dimension"]) for row in dimensions)

        # 跨知识库混写：该版本属于某个知识库，它的分块不该来自别的知识库。
        foreign = cursor.execute(
            """SELECT count(*) AS total FROM chunks
               WHERE index_version_id = %s AND knowledge_base_id <> %s""",
            (index_version_id, version["knowledge_base_id"]),
        ).fetchone()

        incomplete = cursor.execute(
            """SELECT count(*) AS total FROM chunks
               WHERE index_version_id = %s
                 AND (document_version_id IS NULL OR knowledge_base_id IS NULL
                      OR content IS NULL OR content = '')""",
            (index_version_id,),
        ).fetchone()

        metadata_invalid = cursor.execute(
            """SELECT count(*) AS total FROM chunks
               WHERE index_version_id=%s AND (
                   jsonb_typeof(metadata) IS DISTINCT FROM 'object'
                   OR NOT (metadata ?& ARRAY['knowledge_base_id','document_id','filename',
                                             'chunk_index','char_count','summary'])
                   OR jsonb_typeof(metadata->'chunk_index') IS DISTINCT FROM 'number'
                   OR jsonb_typeof(metadata->'char_count') IS DISTINCT FROM 'number'
                   OR jsonb_typeof(COALESCE(metadata->'tags','[]'::jsonb)) IS DISTINCT FROM 'array'
               )""",
            (index_version_id,),
        ).fetchone()
        acl_invalid = cursor.execute(
            """SELECT count(*) AS total FROM chunks c
               WHERE c.index_version_id=%s AND (
                   jsonb_typeof(COALESCE(c.metadata->'allow_user_ids','[]'::jsonb)) IS DISTINCT FROM 'array'
                   OR jsonb_typeof(COALESCE(c.metadata->'deny_user_ids','[]'::jsonb)) IS DISTINCT FROM 'array'
                   OR EXISTS (
                       SELECT 1
                       FROM jsonb_array_elements_text(
                           CASE
                               WHEN jsonb_typeof(c.metadata->'allow_user_ids')='array'
                               THEN c.metadata->'allow_user_ids'
                               ELSE '[]'::jsonb
                           END
                       ) allowed(value)
                       WHERE CASE
                           WHEN jsonb_typeof(c.metadata->'deny_user_ids')='array'
                           THEN c.metadata->'deny_user_ids'
                           ELSE '[]'::jsonb
                       END ? allowed.value
                   )
               )""",
            (index_version_id,),
        ).fetchone()
        citation_invalid = cursor.execute(
            """SELECT count(*) AS total FROM chunks
               WHERE index_version_id=%s AND (
                   NOT (metadata ? 'paragraph')
                   OR jsonb_typeof(metadata->'paragraph') IS DISTINCT FROM 'number'
                   OR jsonb_typeof(COALESCE(metadata->'heading_path','[]'::jsonb)) IS DISTINCT FROM 'array'
                   OR (metadata ? 'page' AND jsonb_typeof(metadata->'page') IS DISTINCT FROM 'number')
                   OR (metadata ? 'sheet_name' AND jsonb_typeof(metadata->'sheet_name') IS DISTINCT FROM 'string')
               )""",
            (index_version_id,),
        ).fetchone()
        lane_health = cursor.execute(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE vector_status='ready') AS vector_ready,
                      count(*) FILTER (WHERE keyword_status='ready') AS keyword_ready,
                      count(*) FILTER (WHERE metadata_status='ready') AS metadata_ready
               FROM document_index_states
               WHERE index_build_id=(
                   SELECT index_build_id FROM index_builds
                   WHERE index_version_id=%s ORDER BY attempt_no DESC LIMIT 1
               )""",
            (index_version_id,),
        ).fetchone()
        partial_index_name = f"chunks_hnsw_{index_version_id.replace('-', '_')}"
        vector_index = cursor.execute(
            """SELECT EXISTS (
                   SELECT 1 FROM pg_indexes
                   WHERE schemaname=current_schema() AND tablename='chunks' AND indexname=%s
               ) AS present""",
            (partial_index_name,),
        ).fetchone()

    complete = str(version["config_completeness"]) == "complete"
    manifest = dict(version["component_manifest"] or {})
    required_manifest = {
        **COMPONENT_SCHEMA_VERSIONS,
        "parser_schema_version": PARSER_SCHEMA_VERSION,
    }
    manifest_ok = complete and all(
        manifest.get(key) == value and bool(value) for key, value in required_manifest.items()
    ) and bool(manifest.get("reranker_model"))
    lane_total = int(lane_health["total"])
    checks = [
        _check(
            "embedding_dimension",
            actual in ([declared], []),
            expected=declared,
            actual=actual or "无分块",
        ),
        _check("single_knowledge_base", int(foreign["total"]) == 0,
               expected=0, actual=int(foreign["total"])),
        _check("required_fields", int(incomplete["total"]) == 0,
               expected=0, actual=int(incomplete["total"])),
        _check(
            "component_manifest",
            manifest_ok if complete else True,
            expected=required_manifest if complete else "legacy/unknown",
            actual=manifest if complete else "历史版本没有完整组件清单",
            severity="critical" if complete else "warning",
        ),
        _check(
            "vector_index_health",
            bool(vector_index["present"]) if complete else True,
            expected=True,
            actual=bool(vector_index["present"]),
            severity="critical" if complete else "warning",
        ),
        _check(
            "vector_lane_health",
            lane_total > 0 and int(lane_health["vector_ready"]) == lane_total if complete else True,
            expected=lane_total,
            actual=int(lane_health["vector_ready"]),
            severity="critical" if complete else "warning",
        ),
        _check(
            "keyword_index_health",
            lane_total > 0 and int(lane_health["keyword_ready"]) == lane_total if complete else True,
            expected=lane_total,
            actual=int(lane_health["keyword_ready"]),
            severity="critical" if complete else "warning",
        ),
        _check(
            "metadata_lane_health",
            lane_total > 0 and int(lane_health["metadata_ready"]) == lane_total if complete else True,
            expected=lane_total,
            actual=int(lane_health["metadata_ready"]),
            severity="critical" if complete else "warning",
        ),
        _check(
            "metadata_schema",
            int(metadata_invalid["total"]) == 0 if complete else True,
            expected=0,
            actual=int(metadata_invalid["total"]),
            severity="critical" if complete else "warning",
        ),
        _check(
            "acl_structure",
            int(acl_invalid["total"]) == 0 if complete else True,
            expected=0,
            actual=int(acl_invalid["total"]),
            severity="critical" if complete else "warning",
        ),
        _check(
            "citation_structure",
            int(citation_invalid["total"]) == 0 if complete else True,
            expected=0,
            actual=int(citation_invalid["total"]),
            severity="critical" if complete else "warning",
        ),
    ]
    failed = [
        item for item in checks
        if item["status"] == "fail" and item["severity"] == "critical"
    ]
    return {
        "layer": "technical",
        "status": "fail" if failed else "pass",
        "checks": checks,
    }


def check_retrieval_quality(
    connection: psycopg.Connection[Any],
    index_version_id: str,
    report: RetrievalEvaluationReport,
) -> dict[str, Any]:
    """检索质量：相对基线不回退，且报告确实评测的是这个版本的配置。

    判定沿用既有实现，不在这里重写比较规则：回退与否由报告自己的 ``regressed`` 给出
    （生成报告时传入上一版本报告作 baseline，见 evaluation/report.py 的 assess_metric）。

    指纹比对是这一层真正的牙齿——报告里的任何布尔字段都可以被伪造，配置指纹不行，
    它必须由被测配置本身算出来。
    """

    with connection.cursor(row_factory=dict_row) as cursor:
        version = cursor.execute(
            """SELECT config_fingerprint, config_completeness
               FROM index_versions WHERE index_version_id = %s""",
            (index_version_id,),
        ).fetchone()
        if version is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
    complete = str(version["config_completeness"]) == "complete"

    regressed = [
        name
        for name, metric in (
            ("recall_at_5", report.recall_at_5),
            ("vector_mrr", report.vector_mrr),
            ("rerank_mrr", report.rerank_mrr),
            ("recall_at_10", report.recall_at_10),
            ("ndcg_at_10", report.ndcg_at_10),
            ("metadata_filter_accuracy", report.metadata_filter_accuracy),
        )
        if metric is not None and metric.regressed
    ]
    fingerprint_matches = (
        report.config_fingerprint is not None
        and report.config_fingerprint == str(version["config_fingerprint"])
    )
    checks = [
        _check("no_metric_regression", not regressed, expected=[], actual=regressed),
        _check(
            "config_fingerprint_matches",
            fingerprint_matches,
            expected=str(version["config_fingerprint"])[:12],
            actual=(report.config_fingerprint or "缺失")[:12],
        ),
        _check(
            "recall_at_10_present",
            report.recall_at_10 is not None,
            expected="Recall@10",
            actual="存在" if report.recall_at_10 else "旧报告缺失",
            severity="critical" if complete else "warning",
        ),
        _check(
            "ndcg_at_10_present",
            report.ndcg_at_10 is not None,
            expected="nDCG@10",
            actual="存在" if report.ndcg_at_10 else "旧报告缺失",
            severity="critical" if complete else "warning",
        ),
        _check(
            "metadata_filter_accuracy_present",
            report.metadata_filter_accuracy is not None,
            expected="metadata_filter_accuracy",
            actual="存在" if report.metadata_filter_accuracy else "旧报告缺失",
            severity="critical" if complete else "warning",
        ),
        _check(
            "acl_leak_count",
            report.acl_leak_count == 0,
            expected=0,
            actual=report.acl_leak_count if report.acl_leak_count is not None else "旧报告缺失",
            severity="critical" if complete or report.acl_leak_count is not None else "warning",
        ),
    ]
    failed = [
        item for item in checks
        if item["status"] == "fail" and item["severity"] == "critical"
    ]
    warnings = [
        item for item in checks
        if item["status"] == "fail" and item["severity"] == "warning"
    ]
    return {
        "layer": "retrieval_quality",
        "status": "fail" if failed else "pass",
        "checks": checks,
        "warnings": warnings,
        # 绝对阈值结论只作提示，不参与放行：它回答的是「这套系统能否上线」，
        # 而切换要回答的是「这次换配置是变好还是变坏」。用绝对阈值当切换门槛会让
        # 功能锁死——某些语料在当前实现下永远达不到冻结阈值，于是一次切换都做不成。
        "meets_frozen_thresholds": report.passed,
        "evaluation_report_id": report.report_id,
    }


def validate_index_version(
    database_url: str,
    index_version_id: str,
    report: RetrievalEvaluationReport,
    actor: Any = None,
) -> dict[str, Any]:
    """执行三层门禁，落一份不可变报告，并按结果推进版本状态。

    通过 → ``ready``（生产链里 ready 的唯一入口）；未通过 → ``validation_failed``，
    它与 ``build_failed`` 分开，因为两者的恢复路径不同：一个重新验证，一个重新构建。

    重新验证会新建报告，不覆盖历史。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        version = connection.execute(
            """SELECT status, knowledge_base_id FROM index_versions
               WHERE index_version_id = %s FOR UPDATE""",
            (index_version_id,),
        ).fetchone()
        if version is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
        if str(version["status"]) not in {"validating", "validation_failed", "ready"}:
            raise AppError(
                "INDEX_VERSION_NOT_VALIDATABLE",
                f"索引版本状态为 {version['status']}，只有构建成功的版本可以验证。",
                409,
            )

        integrity = check_integrity(connection, index_version_id)
        technical = check_technical(connection, index_version_id)
        retrieval = check_retrieval_quality(connection, index_version_id, report)

        failure_items = [
            {**item, "layer": layer["layer"]}
            for layer in (integrity, technical, retrieval)
            for item in layer.get("checks", [])
            if item["status"] == "fail" and item.get("severity") == "critical"
        ]
        passed = not failure_items
        baseline = connection.execute(
            """SELECT index_version_id FROM index_versions
               WHERE knowledge_base_id = %s AND status = 'active'""",
            (version["knowledge_base_id"],),
        ).fetchone()
        build = connection.execute(
            """SELECT index_build_id FROM index_builds
               WHERE index_version_id = %s ORDER BY attempt_no DESC LIMIT 1""",
            (index_version_id,),
        ).fetchone()

        validation_report_id = f"vr_{uuid4().hex[:20]}"
        connection.execute(
            """INSERT INTO validation_reports
               (validation_report_id, index_version_id, index_build_id, status,
                policy_version, evaluation_set_version, baseline_version_id,
                integrity_result, technical_result, retrieval_result,
                summary, failure_items, started_at, finished_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s::jsonb,
                       now(), now())""",
            (
                validation_report_id,
                index_version_id,
                build["index_build_id"] if build else None,
                "pass" if passed else "failed",
                VALIDATION_POLICY_VERSION,
                report.report_id,
                baseline["index_version_id"] if baseline else None,
                Jsonb(integrity),
                Jsonb(technical),
                Jsonb(retrieval),
                "三层门禁全部通过。" if passed else f"{len(failure_items)} 项未通过。",
                Jsonb(failure_items),
            ),
        )
        connection.execute(
            """UPDATE index_versions
               SET status = %s, validation_report_id = %s
               WHERE index_version_id = %s""",
            ("ready" if passed else "validation_failed", validation_report_id, index_version_id),
        )
        record_lifecycle_event(
            connection, knowledge_base_id=str(version["knowledge_base_id"]),
            index_version_id=index_version_id,
            event_type="validation_passed" if passed else "validation_failed",
            from_status=str(version["status"]),
            to_status="ready" if passed else "validation_failed",
            actor=actor, validation_report_id=validation_report_id,
            reason=(
                None if passed
                else "、".join(str(item["check_key"]) for item in failure_items)
            ),
        )

    return {
        "validation_report_id": validation_report_id,
        "status": "pass" if passed else "failed",
        "integrity": integrity,
        "technical": technical,
        "retrieval_quality": retrieval,
        "failure_items": failure_items,
    }


def get_report(database_url: str, validation_report_id: str) -> dict[str, Any] | None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT * FROM validation_reports WHERE validation_report_id = %s",
            (validation_report_id,),
        ).fetchone()
    return dict(row) if row else None


def list_reports(
    database_url: str,
    index_version_id: str,
    knowledge_base_id: str | None = None,
) -> list[dict[str, Any]]:
    """列出某个版本的验证报告，并可在数据库层约束知识库归属。

    API 路径同时携带 knowledge_base_id 与 index_version_id。只校验调用者有权访问前者，
    再按后者直接查询，会允许用一个有权限的知识库路径读取另一个知识库的报告。CLI 等
    已经持有可信 Version ID 的内部调用仍可省略范围参数。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        if knowledge_base_id is None:
            rows = connection.execute(
                """SELECT * FROM validation_reports WHERE index_version_id = %s
                   ORDER BY created_at DESC""",
                (index_version_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT vr.*
                   FROM validation_reports vr
                   JOIN index_versions iv ON iv.index_version_id = vr.index_version_id
                   WHERE vr.index_version_id = %s AND iv.knowledge_base_id = %s
                   ORDER BY vr.created_at DESC""",
                (index_version_id, knowledge_base_id),
            ).fetchall()
    return [dict(row) for row in rows]


def activate_with_report(
    database_url: str,
    index_version_id: str,
    report: RetrievalEvaluationReport,
    audit: Any = None,
    actor: Any = None,
) -> dict[str, object]:
    """验证并激活：先跑三层门禁落一份报告，通过才切换。

    对调用方来说仍是一个动作，但判定结果从此有据可查——不通过时报告已经落库，
    页面能列出具体哪一项没过，而不是只拿到一句错误消息。
    """

    result = validate_index_version(database_url, index_version_id, report, actor)
    if result["status"] != "pass":
        keys = "、".join(
            f"{item['check_key']}={item['actual']}" for item in result["failure_items"]
        )
        raise AppError(
            "VALIDATION_NOT_PASSED",
            f"发布门禁未通过（{keys}），报告 {result['validation_report_id']}。",
            409,
        )
    activated = switch_to_version(database_url, index_version_id, audit, actor)
    return {**activated, "validation_report_id": result["validation_report_id"]}
