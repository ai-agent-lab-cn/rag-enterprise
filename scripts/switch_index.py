"""管理知识库索引版本的切换、回滚与清理。

切换必须提供固定数据集评测报告，且报告的配置指纹要与目标索引版本一致，否则拒绝执行。

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
from backend.app.index_versions import (
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
    parser.add_argument("command", choices=("list", "status", "switch", "rollback", "retire"))
    parser.add_argument("--database-url")
    parser.add_argument("--knowledge-base")
    parser.add_argument("--index-version")
    parser.add_argument("--batch")
    parser.add_argument("--report", help="放行报告 JSON 路径，switch 必填")
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
        # status 会顺带把跑完的批次推进到 ready 或 failed。
        _print(rebuild_status(url, arguments.batch))
        return

    if arguments.command == "switch":
        if not arguments.index_version or not arguments.report:
            raise SystemExit("switch 需要 --index-version 与 --report")
        report = RetrievalEvaluationReport.model_validate_json(
            Path(arguments.report).read_text(encoding="utf-8")
        )
        _print(switch_to_version(url, arguments.index_version, report, audit))
        return

    if arguments.command == "rollback":
        if not arguments.knowledge_base:
            raise SystemExit("rollback 需要 --knowledge-base")
        _print(rollback_to_previous(url, arguments.knowledge_base, audit))
        return

    if not arguments.index_version:
        raise SystemExit("retire 需要 --index-version")
    _print({"deleted_chunks": retire_version(url, arguments.index_version)})


if __name__ == "__main__":
    main()
