from __future__ import annotations

import argparse
import os

from backend.app.database import apply_migrations, check_schema_version


def database_url(argument: str | None) -> str:
    value = argument or os.getenv("DATABASE_URL")
    if not value:
        raise SystemExit("必须通过 --database-url 或 DATABASE_URL 提供数据库连接")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="显式执行或校验 PostgreSQL schema 版本")
    parser.add_argument("command", choices=("apply", "check"))
    parser.add_argument("--database-url")
    parser.add_argument("--required-version", type=int, default=1)
    args = parser.parse_args()
    url = database_url(args.database_url)
    if args.command == "apply":
        version = apply_migrations(url)
        print(f"数据库迁移完成，schema 版本：{version}")
    else:
        check_schema_version(url, args.required_version)
        print(f"数据库 schema 版本校验通过：{args.required_version}")


if __name__ == "__main__":
    main()
