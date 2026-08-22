from __future__ import annotations

import argparse
import signal
import time

from backend.app.config import get_settings
from backend.app.database import check_schema_version
from backend.app.models import get_embedding_model
from backend.app.postgres_documents import IndexWorker


def main() -> None:
    parser = argparse.ArgumentParser(description="PostgreSQL 异步索引 Worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    check_schema_version(settings.database_url, settings.required_database_schema_version)
    worker = IndexWorker(settings, get_embedding_model())
    worker.recover_stale_jobs()
    if args.once:
        worker.run_once()
        return
    stopping = False

    def stop(*_args: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        if not worker.run_once():
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
