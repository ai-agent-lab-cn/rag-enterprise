from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .acceptance_governance import AcceptanceSnapshot, evaluate_acceptance
from .evaluation_governance import BadCaseUpdate, summarize_pipeline_runs, validate_bad_case_transition


class PostgresEvaluationGovernanceRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def pipeline_summary(
        self,
        knowledge_base_id: str | None = None,
        data_source_id: str | None = None,
    ) -> dict[str, object]:
        conditions: list[str] = []
        parameters: list[str] = []
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
    ) -> list[dict[str, object]]:
        conditions: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("knowledge_base_id", knowledge_base_id),
            ("status", status),
            ("severity", severity),
            ("failure_stage", failure_stage),
        ):
            if value:
                conditions.append(f"{column} = %s")
                parameters.append(value)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(limit)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            return list(
                connection.execute(
                    f"SELECT * FROM bad_cases{where} ORDER BY created_at DESC LIMIT %s",  # noqa: S608
                    parameters,
                ).fetchall()
            )

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
                if governed.status == "regression_added":
                    assignments.append("regression_added = true")
                parameters.append(case_id)
                row = connection.execute(
                    f"UPDATE bad_cases SET {', '.join(assignments)} WHERE case_id = %s RETURNING *",  # noqa: S608
                    parameters,
                ).fetchone()
                if governed.status == "regression_added":
                    regression_id = _stable_id("reg", "bad_case", case_id)
                    connection.execute(
                        """INSERT INTO regression_cases
                           (regression_case_id, case_id, dataset_version, last_passed)
                           VALUES (%s, %s, '1.0.0', true)
                           ON CONFLICT (case_id) DO UPDATE
                           SET last_passed = true, updated_at = now()""",
                        (regression_id, case_id),
                    )
                return dict(row) if row else None

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
        retrieval_passed: bool,
        answer_passed: bool,
        acl_leak_count: int,
    ) -> dict[str, object]:
        run_id = f"acc_{uuid4().hex[:16]}"
        evaluation_run_id = f"eval_{uuid4().hex[:16]}"
        now = datetime.now(UTC)
        commit_sha = os.getenv("APP_COMMIT_SHA", "local-working-tree")
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
                       (SELECT count(*) FROM regression_cases r
                          JOIN bad_cases b ON b.case_id=r.case_id
                         WHERE b.knowledge_base_id=%s AND r.last_passed=false)
                           AS regression_failed_count""",
                    (knowledge_base_id,) * 9,
                ).fetchone()
                snapshot = AcceptanceSnapshot(
                    **dict(row or {}),
                    retrieval_report_passed=retrieval_passed,
                    answer_report_passed=answer_passed,
                    acl_leak_count=acl_leak_count,
                    citation_failure_count=0,
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
                       VALUES (%s,%s,%s,%s,14,%s,%s,%s,%s)""",
                    (
                        run_id,
                        knowledge_base_id,
                        result.status,
                        commit_sha,
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
