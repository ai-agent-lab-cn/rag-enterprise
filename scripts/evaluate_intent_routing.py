from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from backend.app.config import get_settings
from backend.app.models import get_generator
from backend.app.modular_rag import QueryIntentRouter
from backend.evaluation.intent_routing import (
    IntentEvaluationResult,
    evaluate_intent_router,
    load_intent_dataset,
)

# 正式意图路由评测脚本
def main() -> None:
    parser = argparse.ArgumentParser(description="执行 Modular RAG 正式意图路由评测")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("backend/evaluation/datasets/intent_routing_v1.json"),
    )
    parser.add_argument("--commit", required=True, help="被测代码的 Git commit SHA")
    parser.add_argument("--record", action="store_true", help="通过后写入正式评测运行事实")
    args = parser.parse_args()
    generator = get_generator()
    result = evaluate_intent_router(
        QueryIntentRouter(generator), load_intent_dataset(args.dataset)
    )
    print(json.dumps({**result.__dict__, "passed": result.passed}, ensure_ascii=False, indent=2))
    if not result.passed:
        raise SystemExit(1)
    if args.record:
        settings = get_settings()
        if not settings.database_url:
            raise SystemExit("DATABASE_URL is required when --record is used")
        _record_report(
            settings.database_url,
            commit_sha=args.commit,
            classifier_model=generator.model_name,
            result=result,
        )

# 记录评测报告
def _record_report(
    database_url: str,
    *,
    commit_sha: str,
    classifier_model: str,
    result: IntentEvaluationResult,
) -> None:
    if not re.fullmatch(r"[a-f0-9]{7,64}", commit_sha):
        raise SystemExit("--commit must be a Git commit SHA")
    now = datetime.now(UTC)
    metrics = {
        "macro_f1": result.macro_f1,
        "control_precision": result.control_precision,
        "per_intent_f1": result.per_intent_f1,
        "sample_count": result.sample_count,
        "failure_count": len(result.failures),
    }
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """INSERT INTO evaluation_runs
               (evaluation_run_id,evaluation_type,dataset_id,dataset_version,commit_sha,
                models,metrics,passed,official,run_at,status,attempt_count,max_attempts,
                started_at,finished_at,updated_at)
               VALUES (%s,'intent_routing','intent_routing_v1','1.0.0',%s,%s,%s,true,true,
                       %s,'succeeded',1,1,%s,%s,%s)""",
            (
                f"eval_{uuid4().hex[:16]}",
                commit_sha,
                Jsonb({"classifier": classifier_model}),
                Jsonb(metrics),
                now,
                now,
                now,
                now,
            ),
        )
    print("recorded official intent_routing evaluation")


if __name__ == "__main__":
    main()
