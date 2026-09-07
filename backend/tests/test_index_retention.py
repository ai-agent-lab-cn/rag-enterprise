from __future__ import annotations

import os

import psycopg
import pytest

from backend.app.database import apply_migrations
from backend.app.index_retention import list_retention_candidates, run_retention_sweep
from backend.app.index_versions import create_building_version


KB_ID = "kb_default"


def _reset(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO knowledge_bases
               (knowledge_base_id, name, description, is_default, created_at, updated_at)
               VALUES (%s, '默认知识库', '', true, now(), now())""",
            (KB_ID,),
        )


def _retired(database_url: str, name: str, days: int) -> str:
    version_id, _ = create_building_version(
        database_url,
        KB_ID,
        chunking_version="v1-700-100",
        parser_version="structured-1",
        embedding_model="test/embedding",
        embedding_dimension=3,
        processing_options={"chunk_size": 700, "chunk_overlap": 100},
        rebuild_batch_id=f"rbd_{name}",
    )
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE index_versions SET status='retired',
                      retired_at=now() - make_interval(days => %s)
               WHERE index_version_id=%s""",
            (days, version_id),
        )
    return version_id


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_retention_keeps_the_newest_versions_and_honours_minimum_age() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    newest = _retired(database_url, "newest", 1)
    middle = _retired(database_url, "middle", 15)
    oldest = _retired(database_url, "oldest", 30)

    candidates = list_retention_candidates(
        database_url, retain_count=1, min_age_days=7
    )

    assert [item["index_version_id"] for item in candidates] == [oldest, middle]
    assert newest not in {item["index_version_id"] for item in candidates}


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_retention_is_dry_run_until_explicitly_applied() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url)
    version_id = _retired(database_url, "old", 30)

    preview = run_retention_sweep(
        database_url, retain_count=0, min_age_days=7, apply=False
    )
    with psycopg.connect(database_url) as connection:
        before = connection.execute(
            "SELECT status FROM index_versions WHERE index_version_id=%s", (version_id,)
        ).fetchone()[0]

    applied = run_retention_sweep(
        database_url, retain_count=0, min_age_days=7, apply=True
    )
    with psycopg.connect(database_url) as connection:
        after = connection.execute(
            "SELECT status FROM index_versions WHERE index_version_id=%s", (version_id,)
        ).fetchone()[0]

    assert preview == {"apply": False, "candidates": 1, "cleaned": 0, "deleted_chunks": 0}
    assert before == "retired"
    assert applied["cleaned"] == 1
    assert after == "cleaned"
