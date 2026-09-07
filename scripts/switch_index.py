"""管理知识库索引版本的验证、激活、回滚、退役与清理。

Validate 与 Activate 是两个动作：前者持久化三层门禁报告并把版本推进到 ready，后者只
核验既有 pass 报告并原子移动 active 指针。``switch`` 保留为 ``activate`` 的兼容别名，
但不再现场执行验证。

质量门的口径边界：它验证的是"该配置在冻结语料上不回退"，不代表验证了生产数据的检索
质量——生产语料没有段落标注，算不出 Recall。放行报告由
``python -m backend.evaluation.run_corpus_baseline`` 在隔离评测库上生成。

重建的发起仍由 ``python -m scripts.rebuild_index start`` 承担，此处不重复实现。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from backend.app.audit import AuditRepository
from backend.app.config import get_settings
from backend.app.database import check_schema_version
from backend.app.index_validation import validate_index_version
from backend.app.index_versions import (
    Actor,
    cleanup_version,
    list_versions,
    retire_version,
    rollback_to_previous,
    switch_to_version,
)
from backend.app.postgres_documents import rebuild_status
from backend.evaluation.report import RetrievalEvaluationReport


def database_url(argument: str | None) -> str:
    value = argument or os.getenv("DATABASE_URL")
    if not value:
        raise SystemExit("必须通过 --database-url 或 DATABASE_URL 提供数据库连接")
    return value


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("list", "status", "validate", "activate", "switch", "rollback", "retire", "cleanup"),
    )
    parser.add_argument("--database-url")
    parser.add_argument("--knowledge-base")
    parser.add_argument("--index-version")
    parser.add_argument("--batch")
    parser.add_argument("--report", help="正式评测报告 JSON 路径，validate 必填")
    parser.add_argument("--requested-by", default="cli-operator")
    parser.add_argument(
        "--confirm-content-lag",
        action="store_true",
        help="回滚目标与当前资料集合不一致时，确认接受内容时间点差异",
    )
    arguments = parser.parse_args()

    settings = get_settings()
    url = database_url(arguments.database_url)
    check_schema_version(url, settings.required_database_schema_version)
    audit = AuditRepository(settings.audit_path)

    if arguments.command == "list":
        if not arguments.knowledge_base:
            raise SystemExit("list 需要 --knowledge-base")
        _print(list_versions(url, arguments.knowledge_base))
        return

    if arguments.command == "status":
        if not arguments.batch:
            raise SystemExit("status 需要 --batch")
        # status 会顺带把跑完的批次推进到 validating 或 build_failed。
        _print(rebuild_status(url, arguments.batch))
        return

    actor = Actor(arguments.requested_by, "admin")
    if arguments.command == "validate":
        if not arguments.index_version or not arguments.report:
            raise SystemExit("validate 需要 --index-version 与 --report")
        report = RetrievalEvaluationReport.model_validate_json(
            Path(arguments.report).read_text(encoding="utf-8")
        )
        _print(validate_index_version(url, arguments.index_version, report, actor))
        return

    if arguments.command in {"activate", "switch"}:
        if not arguments.index_version:
            raise SystemExit(f"{arguments.command} 需要 --index-version")
        if arguments.command == "switch":
            print("switch 已弃用，请改用 activate；本次只激活既有 ready 版本。")
        _print(switch_to_version(url, arguments.index_version, audit, actor))
        return

    if arguments.command == "rollback":
        if not arguments.knowledge_base:
            raise SystemExit("rollback 需要 --knowledge-base")
        _print(
            rollback_to_previous(
                url,
                arguments.knowledge_base,
                audit,
                actor,
                confirm_content_lag=arguments.confirm_content_lag,
            )
        )
        return

    if not arguments.index_version:
        raise SystemExit(f"{arguments.command} 需要 --index-version")
    if arguments.command == "retire":
        _print(retire_version(url, arguments.index_version, actor))
        return
    _print({"deleted_chunks": cleanup_version(url, arguments.index_version, actor)})


if __name__ == "__main__":
    main()
