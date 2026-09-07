"""为升级前已存在的索引版本回填治理记录。

**为什么必须回填。** V31 把发布门禁搬进了状态机：``switch_to_version`` 现在要求版本
持有一份 status='pass' 的 validation_report。而升级前上线的版本一份都没有——它们当年
的放行依据是调用方传进来的内存对象，没有落库。不回填的话，一个已经在线上跑着的版本
在回滚之后就再也切不回去，报 VALIDATION_NOT_PASSED。

**回填的是事实，不是结论。** 报告标 ``report_source='legacy_backfill'``，页面显示
「历史回填，非正式验证」。它的 status 确实是 pass——那些版本真的上线过——但来源与
一次真正跑过三层门禁的报告必须能区分开，否则回填就变成了伪造质量门禁通过。

三层结果一律留空并注明无法追溯：当年的完整性与技术指标没有记录，凭空补一份「全部通过」
才是真的伪造。规格要求未知就是未知。

不回填 document_snapshot：无从知道那些版本当年的输入文档集合，而造一条成员为空的
快照会让完整性门禁把它当成「覆盖 0 篇文档」判失败，比 NULL 更糟。
"""

from __future__ import annotations

import argparse
import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

# 需要回填的状态：它们要么正在承载流量，要么随时可能被激活。
# build_failed / validation_failed / cleaned 不回填——它们本就不该被激活。
BACKFILLABLE = ("active", "previous", "ready")

LEGACY_NOTE = "升级前上线的版本，三层门禁结果无法追溯。"


def database_url(argument: str | None) -> str:
    value = argument or os.getenv("DATABASE_URL")
    if not value:
        raise SystemExit("必须通过 --database-url 或 DATABASE_URL 提供数据库连接")
    return value


def find_candidates(connection: psycopg.Connection) -> list[dict]:
    with connection.cursor(row_factory=dict_row) as cursor:
        return [
            dict(row)
            for row in cursor.execute(
                """SELECT index_version_id, knowledge_base_id, status, evaluation_report_id
                   FROM index_versions
                   WHERE status = ANY(%s) AND validation_report_id IS NULL
                   ORDER BY knowledge_base_id, created_at""",
                (list(BACKFILLABLE),),
            ).fetchall()
        ]


def backfill_one(connection: psycopg.Connection, version: dict) -> str:
    validation_report_id = f"vr_{uuid4().hex[:20]}"
    layer = {"status": "unknown", "checks": [], "note": LEGACY_NOTE}
    connection.execute(
        """INSERT INTO validation_reports
           (validation_report_id, index_version_id, status, policy_version,
            evaluation_set_version, integrity_result, technical_result, retrieval_result,
            summary, report_source, started_at, finished_at)
           VALUES (%s, %s, 'pass', 'legacy', %s, %s::jsonb, %s::jsonb, %s::jsonb, %s,
                   'legacy_backfill', now(), now())""",
        (
            validation_report_id,
            version["index_version_id"],
            version["evaluation_report_id"],
            Jsonb(layer),
            Jsonb(layer),
            Jsonb(layer),
            LEGACY_NOTE,
        ),
    )
    connection.execute(
        """UPDATE index_versions
           SET validation_report_id=%s, legacy_migrated=true, config_completeness='unknown'
           WHERE index_version_id=%s""",
        (validation_report_id, version["index_version_id"]),
    )
    return validation_report_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="为升级前的索引版本回填 legacy 验证报告；幂等，可重复执行"
    )
    parser.add_argument("--database-url")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写入。缺省只做 dry-run，打印将要回填的清单",
    )
    args = parser.parse_args()
    url = database_url(args.database_url)

    with psycopg.connect(url) as connection:
        candidates = find_candidates(connection)
        if not candidates:
            print("没有需要回填的索引版本。")
            return
        print(f"待回填 {len(candidates)} 个索引版本：")
        for item in candidates:
            print(
                f"  {item['index_version_id']}  {item['status']:<9}"
                f"  知识库 {item['knowledge_base_id']}"
                f"  原报告标记 {item['evaluation_report_id'] or '无'}"
            )
        if not args.apply:
            print("\n以上为 dry-run，未写入。确认无误后加 --apply 执行。")
            return
        with connection.transaction():
            for item in candidates:
                report_id = backfill_one(connection, item)
                print(f"  {item['index_version_id']} → {report_id}")
        print(f"\n回填完成，共 {len(candidates)} 个版本。")

    # 异常清单：这些版本处在可回填状态之外，回填脚本有意不碰它们，但操作者应当知道。
    with psycopg.connect(url, row_factory=dict_row) as connection:
        stranded = connection.execute(
            """SELECT status, count(*) AS total FROM index_versions
               WHERE validation_report_id IS NULL AND status <> ALL(%s)
               GROUP BY status ORDER BY status""",
            (list(BACKFILLABLE),),
        ).fetchall()
    if stranded:
        print("\n以下版本没有验证报告，且不在回填范围内（它们本就不该被激活）：")
        for row in stranded:
            print(f"  {row['status']}: {row['total']} 个")


if __name__ == "__main__":
    main()
