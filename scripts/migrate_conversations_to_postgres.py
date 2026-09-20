from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from backend.app.config import get_settings
from backend.app.postgres_history import PostgresConversationRepository, normalize_legacy_payload

# 显式迁移旧会话 JSON 到 PostgreSQL
def main() -> None:
    parser = argparse.ArgumentParser(description="显式迁移旧会话 JSON 到 PostgreSQL")
    parser.add_argument("--source", type=Path, default=Path("data/conversations/records.json"))
    parser.add_argument("--apply", action="store_true", help="执行导入；默认只校验")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    payload = normalize_legacy_payload(json.loads(args.source.read_text(encoding="utf-8")))
    print(
        f"validated conversations={len(payload.conversations)} answers={len(payload.answers)} "
        f"sha256={payload.sha256}"
    )
    if not args.apply:
        return
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = args.source.with_name(f"{args.source.stem}.{timestamp}.json.bak")
    shutil.copy2(args.source, backup)
    conversations, answers = PostgresConversationRepository(settings.database_url).import_legacy(payload)
    print(
        f"verified source_sha256={payload.sha256} "
        f"database_conversations={conversations} database_answers={answers}"
    )
    print(f"source_backup={backup}")


if __name__ == "__main__":
    main()
