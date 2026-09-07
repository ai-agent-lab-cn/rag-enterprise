"""索引版本物理资源的安全保留策略。

Retire 只表示放弃回滚点，Cleanup 才删除 chunks 与专属 HNSW。这里负责挑选已经 retired、
超过最短保留期、且不在每个知识库最新 N 个保留版本中的候选；默认只预览，不隐式删除。
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from .index_versions import Actor, cleanup_version


def list_retention_candidates(
    database_url: str,
    *,
    retain_count: int,
    min_age_days: int,
    knowledge_base_id: str | None = None,
) -> list[dict[str, Any]]:
    if retain_count < 0:
        raise ValueError("retain_count 不能小于 0")
    if min_age_days < 1:
        raise ValueError("min_age_days 至少为 1")
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """WITH ranked AS (
                   SELECT index_version_id, knowledge_base_id, version_no, retired_at,
                          row_number() OVER (
                              PARTITION BY knowledge_base_id
                              ORDER BY retired_at DESC NULLS LAST, created_at DESC
                          ) AS retention_rank
                   FROM index_versions
                   WHERE status='retired'
                     AND (%s IS NULL OR knowledge_base_id=%s)
               )
               SELECT * FROM ranked
               WHERE retention_rank>%s
                 AND retired_at < now() - make_interval(days => %s)
               ORDER BY retired_at, knowledge_base_id, index_version_id""",
            (knowledge_base_id, knowledge_base_id, retain_count, min_age_days),
        ).fetchall()
    return [dict(row) for row in rows]


def run_retention_sweep(
    database_url: str,
    *,
    retain_count: int,
    min_age_days: int,
    apply: bool,
    knowledge_base_id: str | None = None,
) -> dict[str, int | bool]:
    candidates = list_retention_candidates(
        database_url,
        retain_count=retain_count,
        min_age_days=min_age_days,
        knowledge_base_id=knowledge_base_id,
    )
    if not apply:
        return {
            "apply": False,
            "candidates": len(candidates),
            "cleaned": 0,
            "deleted_chunks": 0,
        }
    deleted_chunks = 0
    cleaned = 0
    for item in candidates:
        deleted_chunks += cleanup_version(
            database_url,
            str(item["index_version_id"]),
            Actor("index-retention-sweep", "system"),
        )
        cleaned += 1
    return {
        "apply": True,
        "candidates": len(candidates),
        "cleaned": cleaned,
        "deleted_chunks": deleted_chunks,
    }
