from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

MIGRATIONS_PATH = Path(__file__).resolve().parents[1] / "migrations"


def migration_files(path: Path = MIGRATIONS_PATH) -> list[Path]:
    files = sorted(path.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    versions = [int(item.name.split("_", maxsplit=1)[0]) for item in files]
    if versions != list(range(1, len(files) + 1)):
        raise RuntimeError("数据库迁移版本必须从 0001 开始连续编号")
    return files


def apply_migrations(database_url: str, path: Path = MIGRATIONS_PATH) -> int:
    files = migration_files(path)
    with psycopg.connect(database_url) as connection:
        with connection.transaction():
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version integer PRIMARY KEY,
                    name text NOT NULL,
                    checksum text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
        for file in files:
            version = int(file.name.split("_", maxsplit=1)[0])
            sql = file.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            existing = connection.execute(
                "SELECT checksum FROM schema_migrations WHERE version = %s", (version,)
            ).fetchone()
            if existing:
                if existing[0] != checksum:
                    raise RuntimeError(f"数据库迁移 {version:04d} 校验和不匹配")
                continue
            with connection.transaction():
                connection.execute(sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, checksum) VALUES (%s, %s, %s)",
                    (version, file.name, checksum),
                )
    return len(files)


def check_schema_version(database_url: str, required_version: int) -> None:
    with psycopg.connect(database_url) as connection:
        row = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()
    current = int(row[0] or 0) if row else 0
    if current != required_version:
        raise RuntimeError(f"数据库 schema 版本为 {current}，应用要求 {required_version}；请显式执行迁移")
