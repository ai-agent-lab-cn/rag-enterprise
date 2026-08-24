#!/usr/bin/env python3
"""导出 PostgreSQL 生产迁移核对清单；不输出账号正文、令牌或连接凭据。"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

COUNT_QUERIES = {
    "users": "SELECT count(*) FROM users",
    "knowledge_bases": "SELECT count(*) FROM knowledge_bases",
    "memberships": "SELECT count(*) FROM knowledge_base_memberships",
    "sessions": "SELECT count(*) FROM sessions",
    "data_sources": "SELECT count(*) FROM data_sources",
    "documents": "SELECT count(*) FROM documents",
    "document_versions": "SELECT count(*) FROM document_versions",
    "chunks": "SELECT count(*) FROM chunks",
    "vectors": "SELECT count(*) FROM chunks WHERE embedding IS NOT NULL",
    "index_jobs": "SELECT count(*) FROM index_jobs",
}


def assess(
    counts: dict[str, int], integrity: dict[str, int], expected: dict[str, int] | None
) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {
        name: {"status": "pass" if value == 0 else "fail", "value": value, "expected": 0}
        for name, value in integrity.items()
    }
    if expected is not None:
        aliases = {"memberships": "memberships", "vectors": "chunks"}
        for name, expected_name in aliases.items():
            if expected_name in expected:
                checks[f"count.{name}"] = {
                    "status": "pass" if counts[name] == int(expected[expected_name]) else "fail",
                    "value": counts[name],
                    "expected": int(expected[expected_name]),
                }
        for name in ("users", "knowledge_bases", "sessions", "documents", "document_versions", "chunks"):
            if name in expected:
                checks[f"count.{name}"] = {
                    "status": "pass" if counts[name] == int(expected[name]) else "fail",
                    "value": counts[name],
                    "expected": int(expected[name]),
                }
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "counts": counts,
        "integrity": integrity,
        "checks": checks,
        "verdict": "pass" if all(item["status"] == "pass" for item in checks.values()) else "fail",
        "secrets_included": False,
    }


def inventory(database_url: str, expected: dict[str, int] | None = None) -> dict[str, Any]:
    with psycopg.connect(database_url) as connection:
        counts = {name: int(connection.execute(query).fetchone()[0]) for name, query in COUNT_QUERIES.items()}
        integrity = {
            "documents_without_current_ready_version": int(
                connection.execute(
                    """SELECT count(*) FROM documents d LEFT JOIN document_versions v
                    ON v.document_version_id=d.current_version_id AND v.status='ready'
                    WHERE d.current_version_id IS NULL OR v.document_version_id IS NULL"""
                ).fetchone()[0]
            ),
            "orphan_memberships": int(
                connection.execute(
                    """SELECT count(*) FROM knowledge_base_memberships m
                    LEFT JOIN users u ON u.user_id=m.user_id
                    LEFT JOIN knowledge_bases k ON k.knowledge_base_id=m.knowledge_base_id
                    WHERE u.user_id IS NULL OR k.knowledge_base_id IS NULL"""
                ).fetchone()[0]
            ),
            "ready_versions_without_chunks": int(
                len(connection.execute(
                    """SELECT count(*) FROM document_versions v LEFT JOIN chunks c
                    ON c.document_version_id=v.document_version_id
                    WHERE v.status='ready' GROUP BY v.document_version_id HAVING count(c.chunk_id)=0"""
                ).fetchall())
            ),
        }
    return assess(counts, integrity, expected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, help="legacy_to_postgres.py 输出的计数 JSON")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("必须通过 DATABASE_URL 提供连接；禁止把凭据放入命令参数")
    expected = json.loads(args.expected.read_text(encoding="utf-8")) if args.expected else None
    report = inventory(database_url, expected)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    raise SystemExit(0 if report["verdict"] == "pass" else 1)


if __name__ == "__main__":
    main()
