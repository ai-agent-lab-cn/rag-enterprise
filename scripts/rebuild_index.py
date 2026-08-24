"""按新的切分配置重建知识库索引。

重建由操作者显式发起，不随应用启动或上传自动触发。任务全部经过既有
``index_jobs`` 队列，因此可以中断、续跑，并与普通索引任务共享重试与租约恢复。
"""

from __future__ import annotations

import argparse
import json
import os

from backend.app.chunking import chunking_version
from backend.app.config import get_settings
from backend.app.database import check_schema_version
from backend.app.postgres_documents import chunking_inventory, enqueue_rebuild, rebuild_status


def database_url(argument: str | None) -> str:
    value = argument or os.getenv("DATABASE_URL")
    if not value:
        raise SystemExit("必须通过 --database-url 或 DATABASE_URL 提供数据库连接")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "status", "inventory"))
    parser.add_argument("--database-url")
    parser.add_argument("--knowledge-base")
    parser.add_argument("--batch")
    parser.add_argument("--chunk-size", type=int)
    parser.add_argument("--chunk-overlap", type=int)
    args = parser.parse_args()

    settings = get_settings()
    url = database_url(args.database_url)
    check_schema_version(url, settings.required_database_schema_version)

    if args.command == "start":
        if not args.knowledge_base:
            raise SystemExit("start 需要 --knowledge-base")
        target = chunking_version(
            args.chunk_size if args.chunk_size is not None else settings.chunk_size,
            args.chunk_overlap if args.chunk_overlap is not None else settings.chunk_overlap,
        )
        result = enqueue_rebuild(
            url,
            args.knowledge_base,
            target,
            settings.index_job_max_attempts,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "status":
        if not args.batch:
            raise SystemExit("status 需要 --batch")
        print(json.dumps(rebuild_status(url, args.batch), ensure_ascii=False, indent=2))
        return

    if not args.knowledge_base:
        raise SystemExit("inventory 需要 --knowledge-base")
    print(
        json.dumps(
            chunking_inventory(url, args.knowledge_base),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
