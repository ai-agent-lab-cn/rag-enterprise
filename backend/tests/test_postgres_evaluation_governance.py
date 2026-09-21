from __future__ import annotations

import os
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg.types.json import Jsonb

from backend.app.database import apply_migrations
from backend.app.postgres_evaluation import PostgresEvaluationGovernanceRepository

requires_database = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector"
)


def _reset(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    assert apply_migrations(database_url) == 42


def _seed_scope(database_url: str, suffix: str, *, bad_case: bool = False) -> tuple[str, str]:
    knowledge_base_id = f"kb_{suffix}"
    index_version_id = f"iv_{suffix}"
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """INSERT INTO knowledge_bases
               (knowledge_base_id,name,name_normalized,description,is_default,created_at,updated_at)
               VALUES (%s,%s,%s,'',false,now(),now())""",
            (knowledge_base_id, f"知识库 {suffix}", f"知识库 {suffix}"),
        )
        connection.execute(
            """INSERT INTO index_versions
               (index_version_id,knowledge_base_id,status,chunking_version,parser_version,
                embedding_model,embedding_dimension,processing_options,config_fingerprint,
                evaluation_report_id,activated_at)
               VALUES (%s,%s,'active','v1-700-100','structured-v2','test/embedding',3,
                       '{"chunk_size":700,"chunk_overlap":100}'::jsonb,%s,'vr_seed',now())""",
            (index_version_id, knowledge_base_id, suffix[0] * 64),
        )
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id=%s WHERE knowledge_base_id=%s",
            (index_version_id, knowledge_base_id),
        )
        if bad_case:
            connection.execute(
                """INSERT INTO bad_cases
                   (case_id,source_type,source_record_id,knowledge_base_id,question,
                    expected_answer_status,actual_answer_status,failure_stage,category,
                    fix_commit,status,resolved_at)
                   VALUES ('case_regression','online','ans_regression',%s,'问题？',
                           'answered','insufficient_evidence','retrieval','没召回',
                           'abcdef1','resolved',now())""",
                (knowledge_base_id,),
            )
    return knowledge_base_id, index_version_id


@requires_database
def test_bad_case_regression_persists_run_and_derives_status_from_observation(monkeypatch) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    knowledge_base_id, index_version_id = _seed_scope(database_url, "a", bad_case=True)
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """INSERT INTO users
               (user_id,username,username_normalized,display_name,role,password_hash,
                created_at,updated_at)
               VALUES ('usr_regression','runner','runner','Runner','admin','x',now(),now())"""
        )
    monkeypatch.setenv("APP_COMMIT_SHA", "abcdef1234567890")

    item = PostgresEvaluationGovernanceRepository(database_url, 42).run_bad_case_regression(
        "case_regression",
        created_by="usr_regression",
        actual_answer_status="answered",
        actual_answer="已恢复回答。",
        actual_source_ids=[],
        actual_chunk_ids=[],
        active_index_version_id=index_version_id,
        models={"generation": "test/model"},
        prompt_version="prompt-v1",
        prompt_hash="a" * 64,
    )

    assert item["knowledge_base_id"] == knowledge_base_id
    assert item["status"] == "regression_added"
    assert item["regression_passed"] is True
    assert item["regression_evaluation_run_id"]
    with psycopg.connect(database_url) as connection:
        run = connection.execute(
            """SELECT evaluation_type, knowledge_base_id, index_version_id, official, passed,
                      metrics->>'case_id' AS case_id
                 FROM evaluation_runs WHERE evaluation_run_id=%s""",
            (item["regression_evaluation_run_id"],),
        ).fetchone()
    assert run == ("regression", knowledge_base_id, index_version_id, True, True, "case_regression")


@requires_database
def test_acceptance_does_not_borrow_report_from_another_knowledge_base(monkeypatch) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    knowledge_base_a, _ = _seed_scope(database_url, "a")
    knowledge_base_b, index_version_b = _seed_scope(database_url, "b")
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """INSERT INTO users
               (user_id,username,username_normalized,display_name,role,password_hash,
                created_at,updated_at)
               VALUES ('usr_acceptance','acceptance','acceptance','Acceptance','admin','x',now(),now())"""
        )
        connection.execute(
            """INSERT INTO evaluation_runs
               (evaluation_run_id,evaluation_type,dataset_id,dataset_version,commit_sha,
                knowledge_base_id,index_version_id,metrics,passed,official,status,
                report_payload,run_at,finished_at)
               VALUES ('eval_other','retrieval','corpus','1.0.0','abcdef1',%s,%s,
                       '{}'::jsonb,true,true,'succeeded',%s,%s,%s)""",
            (
                knowledge_base_b,
                index_version_b,
                Jsonb({"report_id": "report_other", "acl_leak_count": 0}),
                datetime.now(UTC),
                datetime.now(UTC),
            ),
        )
    monkeypatch.setenv("APP_COMMIT_SHA", "abcdef1234567890")

    run = PostgresEvaluationGovernanceRepository(database_url, 42).run_acceptance(
        knowledge_base_a, "usr_acceptance"
    )
    retrieval = next(step for step in run["steps"] if step["step_key"] == "retrieval_and_acl")

    assert retrieval["status"] == "blocked"
    assert "retrieval_report_id" not in retrieval["evidence"]
