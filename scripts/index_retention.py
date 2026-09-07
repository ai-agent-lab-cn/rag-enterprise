"""预览或执行索引版本保留策略；默认 dry-run。"""

from __future__ import annotations

import argparse

from backend.app.config import get_settings
from backend.app.index_retention import list_retention_candidates, run_retention_sweep


def main() -> None:
    parser = argparse.ArgumentParser(description="索引 retired 版本保留与物理清理")
    parser.add_argument("--knowledge-base-id")
    parser.add_argument("--retain-count", type=int)
    parser.add_argument("--min-age-days", type=int)
    parser.add_argument("--apply", action="store_true", help="实际清理；缺省只预览")
    parser.add_argument(
        "--force",
        action="store_true",
        help="在 INDEX_AUTO_CLEANUP_ENABLED=false 时允许一次性人工执行",
    )
    args = parser.parse_args()
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL 未配置")
    if args.apply and not settings.index_auto_cleanup_enabled and not args.force:
        raise SystemExit(
            "自动清理未启用；定时任务请配置 INDEX_AUTO_CLEANUP_ENABLED=true，"
            "一次性人工执行可加 --force"
        )
    retain_count = (
        args.retain_count if args.retain_count is not None else settings.index_retention_count
    )
    min_age_days = (
        args.min_age_days
        if args.min_age_days is not None
        else settings.index_retention_min_days
    )
    candidates = list_retention_candidates(
        settings.database_url,
        retain_count=retain_count,
        min_age_days=min_age_days,
        knowledge_base_id=args.knowledge_base_id,
    )
    print(
        f"候选 {len(candidates)} 个；每知识库保留最新 {retain_count} 个 retired 版本，"
        f"最短保留 {min_age_days} 天。"
    )
    for item in candidates:
        print(
            f"  {item['knowledge_base_id']} / {item['index_version_id']} "
            f"(v{item['version_no'] or 'legacy'}) / retired_at={item['retired_at']}"
        )
    result = run_retention_sweep(
        settings.database_url,
        retain_count=retain_count,
        min_age_days=min_age_days,
        apply=args.apply,
        knowledge_base_id=args.knowledge_base_id,
    )
    print(
        f"{'已执行' if args.apply else 'Dry-run'}：清理 {result['cleaned']} 个版本，"
        f"删除 {result['deleted_chunks']} 个 Chunks。"
    )


if __name__ == "__main__":
    main()
