from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .acceptance_governance import AcceptanceSnapshot, evaluate_acceptance
from .evaluation_governance import BadCaseUpdate, summarize_pipeline_runs, validate_bad_case_transition


class PostgresEvaluationGovernanceRepository:
    def __init__(self, database_url: str, required_schema_version: int = 42):
        self.database_url = database_url
        self.required_schema_version = required_schema_version

    def pipeline_summary(
        self,
        knowledge_base_id: str | None = None,
        data_source_id: str | None = None,
        knowledge_base_ids: set[str] | None = None,
    ) -> dict[str, object]:
        if knowledge_base_ids == set():
            return summarize_pipeline_runs([]).model_dump()
        conditions: list[str] = []
        parameters: list[object] = []
        if knowledge_base_ids is not None:
            conditions.append("knowledge_base_id = ANY(%s)")
            parameters.append(list(knowledge_base_ids))
        if knowledge_base_id:
            conditions.append("knowledge_base_id = %s")
            parameters.append(knowledge_base_id)
        if data_source_id:
            conditions.append("data_source_id = %s")
            parameters.append(data_source_id)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                f"""SELECT status, added_count, updated_count, deleted_count, skipped_count,
                           failed_count, retry_count, started_at, finished_at
                    FROM sync_runs{where} ORDER BY created_at DESC LIMIT 1000""",  # noqa: S608
                parameters,
            ).fetchall()
        return summarize_pipeline_runs(rows).model_dump()

    def rag_pipeline_summary(
        self,
        knowledge_base_id: str | None = None,
        knowledge_base_ids: set[str] | None = None,
    ) -> list[dict[str, object]]:
        if knowledge_base_ids == set():
            return []
        conditions: list[str] = []
        parameters: list[object] = []
        if knowledge_base_ids is not None:
            conditions.append("q.knowledge_base_id = ANY(%s)")
            parameters.append(list(knowledge_base_ids))
        if knowledge_base_id:
            conditions.append("q.knowledge_base_id = %s")
            parameters.append(knowledge_base_id)
        condition = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                f"""SELECT q.intent, q.pipeline_profile, q.profile_version,
                           count(*)::integer AS execution_count,
                           count(*) FILTER (
                             WHERE a.answer_status IN ('answered','source_conflict')
                           )::integer AS successful_count,
                           count(*) FILTER (
                             WHERE a.answer_status = 'insufficient_evidence'
                           )::integer AS insufficient_evidence_count,
                           count(*) FILTER (
                             WHERE q.fallback_used OR EXISTS (
                               SELECT 1 FROM module_executions m
                               WHERE m.execution_id=q.execution_id AND m.status='degraded'
                             )
                           )::integer AS fallback_count,
                           COALESCE(percentile_cont(0.95) WITHIN GROUP (
                             ORDER BY q.total_latency_ms
                           ), 0)::double precision AS p95_latency_ms
                    FROM query_executions q
                    LEFT JOIN answer_records a ON a.execution_id=q.execution_id
                    {condition}
                    GROUP BY q.intent,q.pipeline_profile,q.profile_version
                    ORDER BY q.intent,q.pipeline_profile""",  # noqa: S608
                parameters,
            ).fetchall()
        results: list[dict[str, object]] = []
        for row in rows:
            total = int(row["execution_count"])
            results.append(
                {
                    **dict(row),
                    "task_success_rate": int(row["successful_count"]) / total if total else 0,
                    "insufficient_evidence_rate": (
                        int(row["insufficient_evidence_count"]) / total if total else 0
                    ),
                    "fallback_rate": int(row["fallback_count"]) / total if total else 0,
                }
            )
        return results

    def capture_online_bad_case(
        self,
        *,
        record_id: str,
        knowledge_base_id: str,
        question: str,
        category: str,
        failure_stage: str,
        actual_answer_status: str | None,
        actual_answer: str | None,
        actual_source_ids: list[str],
    ) -> str:
        case_id = _stable_id("case", "online", record_id)
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """INSERT INTO bad_cases
                   (case_id, source_type, source_record_id, knowledge_base_id, question,
                    actual_source_ids, actual_answer_status, actual_answer, failure_stage, category)
                   VALUES (%s, 'online', %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (source_type, source_record_id) DO NOTHING""",
                (
                    case_id,
                    record_id,
                    knowledge_base_id,
                    question,
                    Jsonb(actual_source_ids),
                    actual_answer_status,
                    actual_answer,
                    failure_stage,
                    category,
                ),
            )
        return case_id

    def list_bad_cases(
        self,
        *,
        knowledge_base_id: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        failure_stage: str | None = None,
        limit: int = 100,
        knowledge_base_ids: set[str] | None = None,
    ) -> list[dict[str, object]]:
        if knowledge_base_ids == set():
            return []
        conditions: list[str] = []
        parameters: list[object] = []
        if knowledge_base_ids is not None:
            conditions.append("b.knowledge_base_id = ANY(%s)")
            parameters.append(list(knowledge_base_ids))
        for column, value in (
            ("knowledge_base_id", knowledge_base_id),
            ("status", status),
            ("severity", severity),
            ("failure_stage", failure_stage),
        ):
            if value:
                conditions.append(f"b.{column} = %s")
                parameters.append(value)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(limit)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            return list(
                connection.execute(
                    f"""SELECT b.*, r.last_evaluation_run_id AS regression_evaluation_run_id,
                               r.last_passed AS regression_passed,
                               er.run_at AS regression_run_at
                          FROM bad_cases b
                          LEFT JOIN regression_cases r ON r.case_id=b.case_id
                          LEFT JOIN evaluation_runs er
                            ON er.evaluation_run_id=r.last_evaluation_run_id
                          {where}
                         ORDER BY b.created_at DESC LIMIT %s""",  # noqa: S608
                    parameters,
                ).fetchall()
            )

    def get_bad_case(self, case_id: str) -> dict[str, object] | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT b.*, r.last_evaluation_run_id AS regression_evaluation_run_id,
                          r.last_passed AS regression_passed,
                          er.run_at AS regression_run_at
                     FROM bad_cases b
                     LEFT JOIN regression_cases r ON r.case_id=b.case_id
                     LEFT JOIN evaluation_runs er
                       ON er.evaluation_run_id=r.last_evaluation_run_id
                    WHERE b.case_id=%s""",
                (case_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_bad_case(self, case_id: str, update: BadCaseUpdate) -> dict[str, object] | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                current = connection.execute(
                    "SELECT * FROM bad_cases WHERE case_id = %s FOR UPDATE", (case_id,)
                ).fetchone()
                if current is None:
                    return None
                governed = validate_bad_case_transition(str(current["status"]), update)
                now = datetime.now(UTC)
                values = governed.model_dump(exclude_none=True)
                assignments = ["updated_at = %s"]
                parameters: list[object] = [now]
                for field in ("status", "root_cause", "severity", "assignee", "fix_commit"):
                    if field in values:
                        assignments.append(f"{field} = %s")
                        parameters.append(values[field])
                if governed.status == "confirmed":
                    assignments.append("confirmed_at = COALESCE(confirmed_at, %s)")
                    parameters.append(now)
                if governed.status == "resolved":
                    assignments.append("resolved_at = %s")
                    parameters.append(now)
                parameters.append(case_id)
                row = connection.execute(
                    f"UPDATE bad_cases SET {', '.join(assignments)} WHERE case_id = %s RETURNING *",  # noqa: S608
                    parameters,
                ).fetchone()
                return dict(row) if row else None

    def run_bad_case_regression(
        self,
        case_id: str,
        *,
        created_by: str,
        actual_answer_status: str,
        actual_answer: str | None,
        actual_source_ids: list[str],
        actual_chunk_ids: list[str],
        active_index_version_id: str | None,
        models: dict[str, object],
        prompt_version: str | None,
        prompt_hash: str | None,
    ) -> dict[str, object]:
        configured_commit_sha = os.getenv("APP_COMMIT_SHA", "").strip()
        if re.fullmatch(r"[0-9a-f]{7,40}", configured_commit_sha) is None:
            raise ValueError("运行正式回归验证前必须配置可追踪的 APP_COMMIT_SHA")
        if not active_index_version_id:
            raise ValueError("本次查询没有绑定活动索引版本，不能作为回归证据")

        evaluation_run_id = f"eval_{uuid4().hex[:16]}"
        report_id = f"regression-{uuid4().hex[:16]}"
        regression_id = _stable_id("reg", "bad_case", case_id)
        now = datetime.now(UTC)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                current = connection.execute(
                    "SELECT * FROM bad_cases WHERE case_id=%s FOR UPDATE", (case_id,)
                ).fetchone()
                if current is None:
                    raise LookupError(case_id)
                if current["status"] != "resolved":
                    raise ValueError("只有已解决的 Bad Case 才能运行回归验证")
                active = connection.execute(
                    """SELECT index_version_id FROM index_versions
                        WHERE knowledge_base_id=%s AND status='active'
                        ORDER BY activated_at DESC NULLS LAST LIMIT 1""",
                    (current["knowledge_base_id"],),
                ).fetchone()
                if active is None or str(active["index_version_id"]) != active_index_version_id:
                    raise ValueError("回归查询绑定的索引版本已变化，请重新运行")

                expected_status = current["expected_answer_status"]
                expected_sources = {str(item) for item in (current["expected_source_ids"] or [])}
                if not expected_status and not expected_sources:
                    raise ValueError("Bad Case 缺少期望回答状态或期望来源，无法判定回归结论")
                status_passed = not expected_status or actual_answer_status == expected_status
                actual_evidence_ids = set(actual_source_ids) | set(actual_chunk_ids)
                sources_passed = not expected_sources or expected_sources <= actual_evidence_ids
                passed = bool(status_passed and sources_passed)
                dataset_version = str(current["dataset_version"] or "1.0.0")
                evidence = {
                    "report_id": report_id,
                    "case_id": case_id,
                    "question": str(current["question"]),
                    "expected_answer_status": expected_status,
                    "actual_answer_status": actual_answer_status,
                    "expected_source_ids": sorted(expected_sources),
                    "actual_source_ids": actual_source_ids,
                    "actual_chunk_ids": actual_chunk_ids,
                    "status_passed": status_passed,
                    "sources_passed": sources_passed,
                    "passed": passed,
                }
                connection.execute(
                    """INSERT INTO evaluation_runs
                       (evaluation_run_id, evaluation_type, dataset_id, dataset_version,
                        commit_sha, knowledge_base_id, prompt_version, prompt_hash,
                        index_version_id, models, parameters, metrics, passed, official,
                        status, report_payload, requested_by, run_at, started_at, finished_at,
                        updated_at)
                       VALUES (%s,'regression','rag-enterprise-bad-cases',%s,%s,%s,%s,%s,%s,
                               %s,'{}'::jsonb,%s,%s,true,'succeeded',%s,%s,%s,%s,%s,%s)""",
                    (
                        evaluation_run_id,
                        dataset_version,
                        configured_commit_sha,
                        current["knowledge_base_id"],
                        prompt_version,
                        prompt_hash,
                        active_index_version_id,
                        Jsonb(models),
                        Jsonb({"case_id": case_id, "status_passed": status_passed,
                               "sources_passed": sources_passed}),
                        passed,
                        Jsonb(evidence),
                        created_by,
                        now,
                        now,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """INSERT INTO regression_cases
                       (regression_case_id, case_id, dataset_version,
                        last_evaluation_run_id, last_passed)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (case_id) DO UPDATE
                       SET dataset_version=EXCLUDED.dataset_version,
                           last_evaluation_run_id=EXCLUDED.last_evaluation_run_id,
                           last_passed=EXCLUDED.last_passed,
                           updated_at=now()""",
                    (regression_id, case_id, dataset_version, evaluation_run_id, passed),
                )
                connection.execute(
                    """UPDATE bad_cases
                          SET status=%s, regression_added=%s,
                              actual_answer_status=%s, actual_answer=%s,
                              actual_source_ids=%s, updated_at=%s
                        WHERE case_id=%s""",
                    (
                        "regression_added" if passed else "confirmed",
                        passed,
                        actual_answer_status,
                        actual_answer,
                        Jsonb(actual_source_ids),
                        now,
                        case_id,
                    ),
                )
                saved = connection.execute(
                    """SELECT b.*, r.last_evaluation_run_id AS regression_evaluation_run_id,
                              r.last_passed AS regression_passed,
                              er.run_at AS regression_run_at
                         FROM bad_cases b
                         JOIN regression_cases r ON r.case_id=b.case_id
                         JOIN evaluation_runs er
                           ON er.evaluation_run_id=r.last_evaluation_run_id
                        WHERE b.case_id=%s""",
                    (case_id,),
                ).fetchone()
        return dict(saved)

    def list_acceptance_runs(
        self, knowledge_base_id: str | None = None, limit: int = 50
    ) -> list[dict[str, object]]:
        where = " WHERE knowledge_base_id = %s" if knowledge_base_id else ""
        parameters: list[object] = [knowledge_base_id, limit] if knowledge_base_id else [limit]
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            return list(
                connection.execute(
                    f"SELECT * FROM acceptance_runs{where} ORDER BY created_at DESC LIMIT %s",  # noqa: S608
                    parameters,
                ).fetchall()
            )

    def run_acceptance(
        self,
        knowledge_base_id: str,
        created_by: str,
    ) -> dict[str, object]:
        run_id = f"acc_{uuid4().hex[:16]}"
        evaluation_run_id = f"eval_{uuid4().hex[:16]}"
        now = datetime.now(UTC)
        configured_commit_sha = os.getenv("APP_COMMIT_SHA", "").strip()
        commit_sha = configured_commit_sha or "unavailable"
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                row = connection.execute(
                    """SELECT
                       (SELECT count(*) FROM data_sources
                         WHERE knowledge_base_id=%s
                           AND source_type='object_storage' AND enabled)
                           AS external_source_count,
                       (SELECT count(*) FROM sync_runs
                         WHERE knowledge_base_id=%s AND status='succeeded')
                           AS successful_sync_runs,
                       (SELECT COALESCE(sum(added_count + updated_count + deleted_count), 0)
                          FROM sync_runs
                         WHERE knowledge_base_id=%s AND status='succeeded')
                           AS incremental_change_count,
                       (SELECT COALESCE(sum(deleted_count), 0) FROM sync_runs
                         WHERE knowledge_base_id=%s AND status='succeeded') AS deleted_count,
                       ((SELECT count(*) FROM documents
                          WHERE knowledge_base_id=%s
                            AND COALESCE((metadata->>'acl_version')::integer, 1) > 1) +
                        (SELECT count(*) FROM data_sources
                          WHERE knowledge_base_id=%s
                            AND COALESCE((acl->>'version')::integer, 1) > 1)) AS acl_change_count,
                       (SELECT count(*) FROM document_versions v
                          JOIN documents d ON d.document_id=v.document_id
                         WHERE d.knowledge_base_id=%s AND v.parse_status='ready')
                           AS parsed_version_count,
                       (SELECT count(*) FROM index_versions
                         WHERE knowledge_base_id=%s AND status='active') AS active_index_count,
                       (SELECT index_version_id FROM index_versions
                         WHERE knowledge_base_id=%s AND status='active'
                         ORDER BY activated_at DESC NULLS LAST LIMIT 1) AS active_index_version_id,
                       (SELECT count(*) FROM regression_cases r
                          JOIN bad_cases b ON b.case_id=r.case_id
                         WHERE b.knowledge_base_id=%s) AS regression_case_count,
                       (SELECT count(*) FROM regression_cases r
                          JOIN bad_cases b ON b.case_id=r.case_id
                         WHERE b.knowledge_base_id=%s AND r.last_passed IS NULL)
                           AS regression_unverified_count,
                       (SELECT count(*) FROM regression_cases r
                          JOIN bad_cases b ON b.case_id=r.case_id
                         WHERE b.knowledge_base_id=%s AND r.last_passed=false)
                           AS regression_failed_count""",
                    (knowledge_base_id,) * 12,
                ).fetchone()
                schema_row = connection.execute(
                    "SELECT COALESCE(max(version), 0)::integer AS schema_version FROM schema_migrations"
                ).fetchone()
                schema_version = int(schema_row["schema_version"] if schema_row else 0)
                runtime_ready = (
                    schema_version == self.required_schema_version
                    and re.fullmatch(r"[0-9a-f]{7,40}", configured_commit_sha) is not None
                )
                active_index_version_id = row["active_index_version_id"] if row else None
                retrieval_evidence = None
                answer_evidence = None
                if active_index_version_id:
                    retrieval_evidence = connection.execute(
                        """SELECT passed, report_payload->>'report_id' AS report_id,
                                  report_payload->>'acl_leak_count' AS acl_leak_count
                           FROM evaluation_runs
                          WHERE evaluation_type='retrieval'
                            AND knowledge_base_id=%s AND index_version_id=%s
                            AND official AND status='succeeded'
                            AND passed IS NOT NULL AND report_payload IS NOT NULL
                          ORDER BY finished_at DESC NULLS LAST, created_at DESC
                          LIMIT 1""",
                        (knowledge_base_id, active_index_version_id),
                    ).fetchone()
                    answer_evidence = connection.execute(
                        """SELECT passed, report_payload->>'report_id' AS report_id,
                                  CASE
                                    WHEN metrics ? 'citation_failure_count'
                                      THEN metrics->>'citation_failure_count'
                                    WHEN jsonb_typeof(report_payload->'deterministic_results')='array'
                                      THEN (SELECT COALESCE(sum(jsonb_array_length(
                                               item->'invalid_citation_indices'
                                             )), 0)::text
                                              FROM jsonb_array_elements(
                                                report_payload->'deterministic_results'
                                              ) item)
                                    ELSE NULL
                                  END AS citation_failure_count
                           FROM evaluation_runs
                          WHERE evaluation_type='answer'
                            AND knowledge_base_id=%s AND index_version_id=%s
                            AND official AND status='succeeded'
                            AND passed IS NOT NULL AND report_payload IS NOT NULL
                          ORDER BY finished_at DESC NULLS LAST, created_at DESC
                          LIMIT 1""",
                        (knowledge_base_id, active_index_version_id),
                    ).fetchone()
                snapshot = AcceptanceSnapshot(
                    **dict(row or {}),
                    runtime_ready=runtime_ready,
                    schema_version=schema_version,
                    required_schema_version=self.required_schema_version,
                    commit_sha=configured_commit_sha or None,
                    retrieval_report_passed=bool(
                        retrieval_evidence and retrieval_evidence["passed"]
                    ),
                    retrieval_report_id=(
                        str(retrieval_evidence["report_id"])
                        if retrieval_evidence and retrieval_evidence["report_id"]
                        else None
                    ),
                    answer_report_passed=bool(answer_evidence and answer_evidence["passed"]),
                    answer_report_id=(
                        str(answer_evidence["report_id"])
                        if answer_evidence and answer_evidence["report_id"]
                        else None
                    ),
                    acl_leak_count=(
                        int(retrieval_evidence["acl_leak_count"])
                        if retrieval_evidence and retrieval_evidence["acl_leak_count"] is not None
                        else None
                    ),
                    citation_failure_count=(
                        int(answer_evidence["citation_failure_count"])
                        if answer_evidence and answer_evidence["citation_failure_count"] is not None
                        else None
                    ),
                )
                result = evaluate_acceptance(snapshot)
                limitations = [step.summary for step in result.steps if step.status != "passed"]
                connection.execute(
                    """UPDATE bad_cases SET status='confirmed', updated_at=%s
                       WHERE case_id IN (
                           SELECT r.case_id FROM regression_cases r
                           JOIN bad_cases b ON b.case_id=r.case_id
                           WHERE b.knowledge_base_id=%s AND r.last_passed=false
                       )
                         AND status IN ('resolved','regression_added')""",
                    (now, knowledge_base_id),
                )
                connection.execute(
                    """INSERT INTO acceptance_runs
                       (acceptance_run_id, knowledge_base_id, status, commit_sha, schema_version,
                        steps, limitations, created_by, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        run_id,
                        knowledge_base_id,
                        result.status,
                        commit_sha,
                        schema_version,
                        Jsonb([step.model_dump() for step in result.steps]),
                        Jsonb(limitations),
                        created_by,
                        now,
                    ),
                )
                connection.execute(
                    """INSERT INTO evaluation_runs
                       (evaluation_run_id, evaluation_type, dataset_id, dataset_version, commit_sha,
                        knowledge_base_id, metrics, passed, official, run_at)
                       VALUES (%s,'acceptance','rag-enterprise-e2e','1.0.0',%s,%s,%s,%s,false,%s)""",
                    (
                        evaluation_run_id,
                        commit_sha,
                        knowledge_base_id,
                        Jsonb(snapshot.model_dump()),
                        result.status == "passed",
                        now,
                    ),
                )
                saved = connection.execute(
                    "SELECT * FROM acceptance_runs WHERE acceptance_run_id=%s", (run_id,)
                ).fetchone()
        return dict(saved)


def _stable_id(prefix: str, source_type: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{source_type}:{source_id}".encode()).hexdigest()[:16]
    return f"{prefix}_{digest}"
