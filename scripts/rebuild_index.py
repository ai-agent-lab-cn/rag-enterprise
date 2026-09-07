"""按新的切分配置重建知识库索引。

重建由操作者显式发起，不随应用启动或上传自动触发。任务全部经过既有
``index_jobs`` 队列，因此可以中断、续跑，并与普通索引任务共享重试与租约恢复。
"""

from __future__ import annotations

import argparse
import json
import os
from uuid import uuid4

from backend.app.config import get_settings
from backend.app.database import check_schema_version
from backend.app.index_versions import CREATION_REASONS, preview_index_version_candidate
from backend.app.postgres_documents import (
    chunking_inventory,
    create_index_version_candidate,
    rebuild_status,
)


def database_url(argument: str | None) -> str:
    value = argument or os.getenv("DATABASE_URL")
    if not value:
        raise SystemExit("必须通过 --database-url 或 DATABASE_URL 提供数据库连接")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preview", "start", "status", "inventory"))
    parser.add_argument("--database-url")
    parser.add_argument("--knowledge-base")
    parser.add_argument("--batch")
    parser.add_argument("--chunk-size", type=int)
    parser.add_argument("--chunk-overlap", type=int)
    parser.add_argument("--reason", choices=sorted(CREATION_REASONS), default="config_changed")
    parser.add_argument("--force-reason")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--requested-by", default="cli-operator")
    parser.add_argument("--apply", action="store_true", help="start 时实际创建；缺省只预览")
    args = parser.parse_args()

    settings = get_settings()
    url = database_url(args.database_url)
    check_schema_version(url, settings.required_database_schema_version)

    if args.command in {"preview", "start"}:
        if not args.knowledge_base:
            raise SystemExit(f"{args.command} 需要 --knowledge-base")
        chunk_size = args.chunk_size if args.chunk_size is not None else settings.chunk_size
        chunk_overlap = (
            args.chunk_overlap if args.chunk_overlap is not None else settings.chunk_overlap
        )
        force = args.reason in {"consistency_repair", "manual_rebuild"}
        preview = preview_index_version_candidate(
            url,
            args.knowledge_base,
            reason=args.reason,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            force=force,
            force_reason=args.force_reason,
            reranker_model=settings.reranker_model,
            max_concurrent_builds=settings.max_concurrent_index_builds,
            max_documents=settings.max_index_build_documents,
        )
        print(json.dumps(preview, ensure_ascii=False, indent=2, default=str))
        if args.command == "preview" or not args.apply:
            if args.command == "start":
                print("以上为 dry-run；确认后加 --apply 创建候选版本。")
            return
        if not preview["creation_allowed"]:
            raise SystemExit("预览未通过：" + "；".join(preview["blocked_reasons"]))
        result = create_index_version_candidate(
            url,
            args.knowledge_base,
            reason=args.reason,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            force=force,
            force_reason=args.force_reason,
            expected_config_fingerprint=str(preview["config_fingerprint"]),
            expected_document_set_fingerprint=str(preview["document_set_fingerprint"]),
            expected_release_fingerprint=str(preview["release_fingerprint"]),
            requested_by=args.requested_by,
            idempotency_key=args.idempotency_key or f"cli-{uuid4().hex}",
            reranker_model=settings.reranker_model,
            max_concurrent_builds=settings.max_concurrent_index_builds,
            max_documents=settings.max_index_build_documents,
            max_attempts=settings.index_job_max_attempts,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
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
