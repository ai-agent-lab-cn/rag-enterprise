from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

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


def _stable_id(prefix: str, source_type: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{source_type}:{source_id}".encode()).hexdigest()[:16]
    return f"{prefix}_{digest}"
