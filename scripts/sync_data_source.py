"""管理本地目录数据源的增量同步。

同步由操作者显式发起，不随应用启动或定时触发——项目的既定原则是不引入隐式定时任务
（见 scripts/validate_kubernetes.py 里「禁止隐式定时备份」那条检查）。

任务经过既有的 index_jobs 队列，因此可以中断、续跑，并与普通索引任务共享重试与租约恢复。
入队之后需要有 Worker 在跑（scripts.index_worker）才会真正执行。

删除是软删除：对象在数据源里消失后，它的分块不再进检索，但文档记录与向量全部保留，
物理删除仍只能由人显式执行。单次同步的删除量同时超过绝对下限与比例阈值时会熔断中止，
不执行任何写入。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.config import get_settings
from backend.app.data_source_sync import enqueue_sync
from backend.app.database import check_schema_version
from backend.app.errors import AppError
from backend.app.postgres_repositories import PostgresDataSourceRepository

DEFAULT_SUFFIXES = (".md", ".txt", ".pdf")


def database_url(argument: str | None) -> str:
    value = argument or os.getenv("DATABASE_URL")
    if not value:
        raise SystemExit("必须通过 --database-url 或 DATABASE_URL 提供数据库连接")
    return value


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _create(url: str, knowledge_base_id: str, name: str, root: str, suffixes: str | None) -> None:
    resolved = Path(root).expanduser()
    if not resolved.is_dir():
        raise SystemExit(f"根目录不存在或不是目录：{resolved}")
    include = tuple(
        item.strip() for item in (suffixes or ",".join(DEFAULT_SUFFIXES)).split(",") if item.strip()
    )
    data_source_id = f"ds_{uuid4().hex[:16]}"
    with psycopg.connect(url) as connection, connection.transaction():
        if not connection.execute(
            "SELECT 1 FROM knowledge_bases WHERE knowledge_base_id = %s", (knowledge_base_id,)
        ).fetchone():
            raise SystemExit(f"未找到知识库：{knowledge_base_id}")
        connection.execute(
            """INSERT INTO data_sources
               (data_source_id, knowledge_base_id, source_type, name, configuration,
                created_at, updated_at)
               VALUES (%s, %s, 'local_directory', %s, %s, now(), now())""",
            (
                data_source_id,
                knowledge_base_id,
                name,
                Jsonb({"root": str(resolved.resolve()), "include_suffixes": list(include)}),
            ),
        )
    _print(
        {
            "data_source_id": data_source_id,
            "knowledge_base_id": knowledge_base_id,
            "root": str(resolved.resolve()),
            "include_suffixes": list(include),
        }
    )


def _status(url: str, data_source_id: str) -> None:
    with psycopg.connect(url, row_factory=dict_row) as connection:
        source = connection.execute(
            """SELECT data_source_id, knowledge_base_id, name, source_type, configuration,
                      last_sync_at, last_sync_status, sync_failure_reason
               FROM data_sources WHERE data_source_id = %s""",
            (data_source_id,),
        ).fetchone()
        if source is None:
            raise SystemExit(f"未找到数据源：{data_source_id}")
        objects = connection.execute(
            "SELECT count(*) AS total FROM data_source_objects WHERE data_source_id = %s",
            (data_source_id,),
        ).fetchone()
        pending = connection.execute(
            """SELECT count(*) AS total FROM index_jobs
               WHERE data_source_id = %s AND status IN ('queued', 'running')""",
            (data_source_id,),
        ).fetchone()
    _print({**dict(source), "known_objects": int(objects["total"]), "pending_jobs": int(pending["total"])})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "list", "sync", "status"))
    parser.add_argument("--database-url")
    parser.add_argument("--knowledge-base")
    parser.add_argument("--data-source")
    parser.add_argument("--name")
    parser.add_argument("--root")
    parser.add_argument("--suffixes", help=f"逗号分隔，默认 {','.join(DEFAULT_SUFFIXES)}")
    arguments = parser.parse_args()

    settings = get_settings()
    url = database_url(arguments.database_url)
    check_schema_version(url, settings.required_database_schema_version)

    try:
        if arguments.command == "create":
            if not (arguments.knowledge_base and arguments.name and arguments.root):
                raise SystemExit("create 需要 --knowledge-base、--name 与 --root")
            _create(url, arguments.knowledge_base, arguments.name, arguments.root, arguments.suffixes)
            return

        if arguments.command == "list":
            sources = PostgresDataSourceRepository(url).list()
            if arguments.knowledge_base:
                sources = [
                    item for item in sources if item["knowledge_base_id"] == arguments.knowledge_base
                ]
            _print(sources)
            return

        if arguments.command == "sync":
            if not arguments.data_source:
                raise SystemExit("sync 需要 --data-source")
            result = enqueue_sync(url, arguments.data_source)
            _print({**result, "note": "任务已入队，需要 scripts.index_worker 在运行才会执行"})
            return

        if not arguments.data_source:
            raise SystemExit("status 需要 --data-source")
        _status(url, arguments.data_source)
    except AppError as error:
        # 透出稳定错误码，便于运行手册按码处置。
        raise SystemExit(f"{error.code}: {error.message}") from None


if __name__ == "__main__":
    main()
