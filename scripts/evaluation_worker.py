from __future__ import annotations

import argparse
import logging
import signal
import time

from backend.app.config import get_settings
from backend.app.database import check_schema_version
from backend.app.errors import AppError
from backend.app.index_evaluation_runs import (
    claim_evaluation_run,
    fail_evaluation_run,
    finish_evaluation_run,
    recover_stale_evaluation_runs,
)
from backend.app.index_evaluation_worker import (
    ensure_evaluation_database,
    run_evaluation_once,
)
from backend.app.models import get_embedding_model, get_reranker


def run_once(settings, embedder, reranker) -> bool:
    """领取并执行一次正式评测；返回这一轮有没有捡到活。

    异常一律收口成 `fail_evaluation_run`，Worker 不因单个任务失败退出——一个坏掉的
    数据集不该让整个评测队列停摆。稳定错误码来自 AppError，非预期异常统一记
    EVALUATION_RUN_FAILED 并把技术详情写进 error_message（只有管理员能看到）。
    """

    run = claim_evaluation_run(settings.database_url, settings.evaluation_worker_id)
    if run is None:
        return False
    evaluation_run_id = str(run["evaluation_run_id"])
    logging.info(
        '{"event":"evaluation_claimed","evaluation_run_id":"%s","index_version_id":"%s"}',
        evaluation_run_id,
        run["index_version_id"],
    )
    try:
        report = run_evaluation_once(settings, run, embedder, reranker)
        finish_evaluation_run(settings.database_url, evaluation_run_id, report)
        logging.info(
            '{"event":"evaluation_succeeded","evaluation_run_id":"%s",'
            '"official":%s,"passed":%s}',
            evaluation_run_id,
            str(report.official).lower(),
            str(report.passed).lower(),
        )
    except AppError as exc:
        fail_evaluation_run(settings.database_url, evaluation_run_id, exc.code, exc.message)
        logging.warning(
            '{"event":"evaluation_failed","evaluation_run_id":"%s","error_code":"%s"}',
            evaluation_run_id,
            exc.code,
        )
    except Exception as exc:  # noqa: BLE001 - Worker 不能因单个任务失败退出
        fail_evaluation_run(
            settings.database_url, evaluation_run_id, "EVALUATION_RUN_FAILED", str(exc)
        )
        logging.warning(
            '{"event":"evaluation_failed","evaluation_run_id":"%s","error_type":"%s"}',
            evaluation_run_id,
            type(exc).__name__,
        )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="正式检索评测 Worker")
    parser.add_argument("--once", action="store_true")
    # 评测是分钟级任务，轮询不需要像索引那样每秒一次。
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    # 与 index_worker 同一个理由：Worker 不跑在 uvicorn 里，root logger 没有 handler，
    # structured_log 的 INFO 会被 lastResort 的 WARNING 门槛丢掉。格式只留 message，
    # 因为输出的已经是 JSON，加前缀就没法喂给 jq。
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    if not settings.evaluation_database_url:
        # Backend 缺这一项照样启动，只是创建评测任务返回 503；Worker 缺了就没有存在意义。
        raise SystemExit("EVALUATION_DATABASE_URL is required")
    check_schema_version(settings.database_url, settings.required_database_schema_version)
    try:
        ensure_evaluation_database(settings)
    except AppError as exc:
        raise SystemExit(f"{exc.code}: {exc.message}") from exc

    # 模型在启动时加载一次：评测每一轮都要用，惰性加载只会把首个任务的失败推迟到
    # 它已经被标成 running 之后。
    embedder = get_embedding_model()
    reranker = get_reranker()

    recovered = recover_stale_evaluation_runs(
        settings.database_url, settings.evaluation_job_stale_seconds
    )
    if recovered:
        logging.info('{"event":"stale_evaluations_recovered","count":%d}', recovered)
    if args.once:
        run_once(settings, embedder, reranker)
        return

    stopping = False

    def stop(*_args: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    # 与 index_worker 同一个取值理由：间隔取租约的四分之一，保证「过期后一个间隔内
    # 必被发现」，同时不给数据库添无谓的写。被 SIGKILL / OOMKill 的进程留下的 running
    # 记录只有这里能拉回队列——部分唯一索引把它算作活动运行，不回收的话管理员再点
    # 「运行正式评测」永远得到 409。
    recover_interval = max(60.0, settings.evaluation_job_stale_seconds / 4)
    next_recover = time.monotonic() + recover_interval

    while not stopping:
        now = time.monotonic()
        if now >= next_recover:
            recovered = recover_stale_evaluation_runs(
                settings.database_url, settings.evaluation_job_stale_seconds
            )
            if recovered:
                logging.info('{"event":"stale_evaluations_recovered","count":%d}', recovered)
            next_recover = now + recover_interval
        # 单进程串行，max_concurrency 恒为 1：两个评测同时跑会在同一个评测库里
        # 互相看见对方的临时语料，_require_empty_evaluation_database 也就失去意义。
        if not run_once(settings, embedder, reranker):
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
