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

    # 周期性回收，不能只靠上面启动时那一次。worker 被 SIGKILL / OOMKill 时
    # locked_at 是刚续过的，新进程几秒后起来算出的 cutoff 判不到它；而循环里不再
    # 调用的话，那行 running 任务就此永远没有回收路径——两个部分唯一索引都把它算作
    # 活动记录，管理员再点「立即同步」永远得到 409，页面一直显示「同步中」，
    # 唯一出路是人想到去点「取消同步」。
    #
    # 间隔取 stale_seconds 的四分之一（默认 900s → 225s）：比租约短得多才能保证
    # 「过期后一个间隔内必被发现」，又不至于频繁到给数据库添无谓的写。
    recover_interval = max(30.0, settings.index_job_stale_seconds / 4)
    next_recover = time.monotonic() + recover_interval

    while not stopping:
        now = time.monotonic()
        if now >= next_recover:
            recovered = worker.recover_stale_jobs()
            if recovered:
                logging.info('{"event":"stale_jobs_recovered","count":%d}', recovered)
            next_recover = now + recover_interval
        if not worker.run_once():
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
