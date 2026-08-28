from __future__ import annotations

import argparse
import logging
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
    # Worker 不跑在 uvicorn 里，root logger 没有任何 handler，structured_log 的 INFO
    # 记录会被 lastResort 的 WARNING 门槛直接丢掉。少了这行，同步跳过的对象在任何地方
    # 都查不到：不入队、不软删、不进对象记录，只剩这条日志。
    # 格式只留 message，因为 structured_log 输出的已经是 JSON，加前缀就没法喂给 jq。
    logging.basicConfig(level=logging.INFO, format="%(message)s")
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
