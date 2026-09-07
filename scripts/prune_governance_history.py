"""清理治理表里过期的逐条明细。

**为什么需要它。** `sync_resource_runs` 是每个远端对象每次同步一行，其中绝大多数是
「无变化」。一个 1000 份文档、每小时同步一次的数据源，一天 24,000 行，一年 876 万行。
这张表此前没有任何删除路径，其余治理表同样只增不减。

**为什么只删这一张。** 外键的删除行为决定了哪些能删：

    operations   ← index_builds              ON DELETE CASCADE
    index_builds ← document_index_states     ON DELETE CASCADE
    sync_runs    ← sync_resource_runs        ON DELETE CASCADE
    sync_runs    ← document_versions         ON DELETE SET NULL

删一条 `operations` 会级联带走 `index_builds`，再带走 `document_index_states`——
那正是「一个版本多次构建尝试、失败现场保留」要保的东西。删 `sync_runs` 则会把
`document_versions.sync_run_id` 置空，丢掉「这一版是哪次同步产生的」这条来源追踪。

`sync_resource_runs` 是唯一既占了绝大部分增量、又没有任何东西引用它的表。删掉它的逐条
明细之后，`sync_runs` 上的聚合计数（added/updated/deleted/skipped/retry）原样保留，
批次层面的历史完整。

`index_lifecycle_events` 有意不清理：它是 append-only 的审计轨迹，每个版本至多十几行，
不构成增长问题，而它回答的「谁在什么时候做了什么」正是出事时唯一的线索。

**只删已终结批次的明细。** `aggregate_sync_run` 用这些行算 `done = total == completed`，
删掉进行中批次的行会让它把残缺批次判成完成。因此只碰 succeeded / partial_failed /
failed / aborted 且 `finished_at` 早于保留窗口的批次。

与备份、legacy 回填一样：dry-run 缺省，`--apply` 才写入，可重复执行。
"""

from __future__ import annotations

import argparse
import os

import psycopg
from psycopg.rows import dict_row

# 已终结的同步批次。进行中的批次的明细行是聚合判定的依据，不能碰。
TERMINAL_RUN_STATUSES = ("succeeded", "partial_failed", "failed", "aborted")


def database_url(argument: str | None) -> str:
    value = argument or os.getenv("DATABASE_URL")
    if not value:
        raise SystemExit("必须通过 --database-url 或 DATABASE_URL 提供数据库连接")
    return value


def survey(connection: psycopg.Connection, retain_days: int) -> dict[str, int]:
    """统计将要删除的量，以及为什么其余的不删。"""

    with connection.cursor(row_factory=dict_row) as cursor:
        return dict(
            cursor.execute(
                """SELECT
                     (SELECT count(*) FROM sync_resource_runs) AS 明细总数,
                     (SELECT count(*) FROM sync_resource_runs r
                       JOIN sync_runs s USING (sync_run_id)
                      WHERE s.status = ANY(%s)
                        AND s.finished_at < now() - make_interval(days => %s)) AS 可删除,
                     (SELECT count(*) FROM sync_resource_runs r
                       JOIN sync_runs s USING (sync_run_id)
                      WHERE NOT (s.status = ANY(%s))) AS 批次未终结,
                     (SELECT count(*) FROM sync_runs
                       WHERE status = ANY(%s)
                         AND finished_at < now() - make_interval(days => %s)) AS 涉及批次数""",
                (
                    list(TERMINAL_RUN_STATUSES), retain_days,
                    list(TERMINAL_RUN_STATUSES),
                    list(TERMINAL_RUN_STATUSES), retain_days,
                ),
            ).fetchone()
        )


def prune(connection: psycopg.Connection, retain_days: int) -> int:
    with connection.transaction():
        result = connection.execute(
            """DELETE FROM sync_resource_runs r
               USING sync_runs s
               WHERE r.sync_run_id = s.sync_run_id
                 AND s.status = ANY(%s)
                 AND s.finished_at < now() - make_interval(days => %s)""",
            (list(TERMINAL_RUN_STATUSES), retain_days),
        )
    return result.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(
        description="清理已终结同步批次的逐资源明细；幂等，可重复执行"
    )
    parser.add_argument("--database-url")
    parser.add_argument(
        "--retain-days",
        type=int,
        default=30,
        help="保留最近多少天的明细（默认 30）。批次层面的计数与游标始终保留。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际删除。缺省只做 dry-run，打印统计。",
    )
    args = parser.parse_args()
    if args.retain_days < 1:
        raise SystemExit("--retain-days 至少为 1：留出正在收尾的批次的余地")
    url = database_url(args.database_url)

    with psycopg.connect(url) as connection:
        stats = survey(connection, args.retain_days)
        print(f"sync_resource_runs 共 {stats['明细总数']:,} 行")
        print(f"  可删除（批次已终结且早于 {args.retain_days} 天）：{stats['可删除']:,} 行，"
              f"涉及 {stats['涉及批次数']:,} 个批次")
        print(f"  保留（批次尚未终结）：{stats['批次未终结']:,} 行"
              " —— 聚合判定依赖它们，删了会把残缺批次判成完成")
        if not stats["可删除"]:
            print("\n没有需要清理的明细。")
            return
        if not args.apply:
            print("\n以上为 dry-run，未写入。确认无误后加 --apply 执行。")
            return
        deleted = prune(connection, args.retain_days)
        print(f"\n已删除 {deleted:,} 行。批次计数、游标与来源追踪原样保留。")

    # 其余治理表的现状一并报出：它们不该被这个脚本删，但操作者应当知道它们有多大。
    with psycopg.connect(url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """SELECT 'operations' AS 表, count(*) AS 行数 FROM operations
               UNION ALL SELECT 'sync_runs', count(*) FROM sync_runs
               UNION ALL SELECT 'index_lifecycle_events', count(*) FROM index_lifecycle_events
               UNION ALL SELECT 'index_jobs', count(*) FROM index_jobs"""
        ).fetchall()
    print("\n本脚本有意不碰的表（删它们会级联带走构建历史或来源追踪）：")
    for row in rows:
        print(f"  {row['表']:24} {row['行数']:,} 行")


if __name__ == "__main__":
    main()
