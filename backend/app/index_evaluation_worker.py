"""一次正式检索评测的业务编排。

Worker 拿到的只是一行入队证据。它要做的事按顺序是：确认评测库真的是隔离的空库、
确认候选版本的冻结配置仍然等于自己即将使用的运行时配置、跑评测、把完整报告写回业务库。

中间任何一条不成立都必须以稳定错误码失败，而不是继续跑完再产出一份指纹对不上的报告
——那种报告在三层验证里会以 `config_fingerprint_matches` 失败告终，用户看到的却是
「评测通过了但验证不过」，真正的原因（Worker 的 reranker 与候选版本不是同一代）
在页面上完全看不出来。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from backend.evaluation.corpus_dataset import load_corpus_dataset
from backend.evaluation.report import RetrievalEvaluationReport
from backend.evaluation.run_corpus_baseline import (
    _require_empty_evaluation_database,  # noqa: PLC2701 - 单一真相，不复制第二份守卫
    run_corpus_baseline,
)

from .chunking import parse_chunking_version
from .config import Settings
from .database import apply_migrations, check_schema_version
from .errors import AppError
from .index_evaluation_runs import (
    resolve_dataset,
    update_evaluation_stage,
)
from .index_versions import component_manifest

DATASETS_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "datasets"

# 只允许自动创建这一个库名。评测会在库里建临时语料再删掉，指到别处等于把删除操作
# 交给一个没人复核过的连接串。
EVALUATION_DATABASE_NAME = "rag_enterprise_evaluation"


def normalized_dsn(url: str) -> tuple[str, str, str]:
    """把连接串收敛成 (host, port, dbname) 用于比较。

    直接比字符串不够：`postgresql://rag@postgres:5432/rag_enterprise` 与
    `postgresql://rag:pw@postgres/rag_enterprise?sslmode=disable` 指的是同一个库，
    但字面量完全不同。密码与参数不参与比较，它们不改变「指向哪个库」。
    """

    info = conninfo_to_dict(url)
    return (
        str(info.get("host") or "localhost"),
        str(info.get("port") or "5432"),
        str(info.get("dbname") or ""),
    )


def require_isolated_evaluation_database(settings: Settings) -> str:
    """返回可用的评测库连接串；配置缺失或与业务库同库时拒绝。"""

    evaluation_url = (settings.evaluation_database_url or "").strip()
    if not evaluation_url:
        raise AppError(
            "EVALUATION_DATABASE_NOT_CONFIGURED",
            "本部署未配置正式评测数据库，无法运行正式评测。",
            503,
        )
    business_url = (settings.database_url or "").strip()
    if business_url and normalized_dsn(business_url) == normalized_dsn(evaluation_url):
        raise AppError(
            "EVALUATION_DATABASE_NOT_ISOLATED",
            "正式评测数据库不能与业务数据库相同。",
            500,
        )
    return evaluation_url


def ensure_evaluation_database(settings: Settings) -> str:
    """准备评测库：不存在就创建专用库，存在就只补迁移，绝不清空。"""

    evaluation_url = require_isolated_evaluation_database(settings)
    _, _, dbname = normalized_dsn(evaluation_url)
    try:
        with psycopg.connect(evaluation_url):
            pass
    except psycopg.OperationalError as exc:
        if dbname != EVALUATION_DATABASE_NAME:
            raise AppError(
                "EVALUATION_DATABASE_NOT_FOUND",
                f"评测数据库不存在；自动创建只支持专用库 {EVALUATION_DATABASE_NAME}。",
                500,
                {"database": dbname},
            ) from exc
        _create_evaluation_database(settings, dbname)

    # 已存在的库只补迁移。apply_migrations 靠 schema_migrations 幂等，重复执行不改数据。
    apply_migrations(evaluation_url)
    check_schema_version(evaluation_url, settings.required_database_schema_version)
    return evaluation_url


def _create_evaluation_database(settings: Settings, dbname: str) -> None:
    business_url = (settings.database_url or "").strip()
    if not business_url:
        raise AppError(
            "EVALUATION_DATABASE_NOT_FOUND",
            "评测数据库不存在，且没有可用于创建它的业务数据库连接。",
            500,
        )
    # CREATE DATABASE / CREATE EXTENSION 不能跑在事务里，必须 autocommit。
    with psycopg.connect(business_url, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{dbname}"')
    with psycopg.connect(
        require_isolated_evaluation_database(settings), autocommit=True
    ) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
    logging.info('{"event":"evaluation_database_created","database":"%s"}', dbname)


def run_evaluation_once(
    settings: Settings, run: dict[str, Any], embedder: Any, reranker: Any
) -> RetrievalEvaluationReport:
    """执行一次已领取的正式评测，返回完整报告。

    ``run`` 是 `claim_evaluation_run()` 返回的那一行，里面的指纹、数据集与配置快照都是
    入队时冻结的。这里不重新推导它们，只核对「现在的运行时是否还能产出同一套配置」。
    """

    business_url = (settings.database_url or "").strip()
    if not business_url:
        raise AppError("POSTGRES_REQUIRED", "正式评测需要 PostgreSQL。", 503)
    evaluation_url = ensure_evaluation_database(settings)
    _require_empty_evaluation_database(evaluation_url)

    evaluation_run_id = str(run["evaluation_run_id"])
    index_version_id = str(run["index_version_id"])
    parameters = dict(run.get("parameters") or {})
    dataset = resolve_dataset(str(parameters.get("dataset_slug") or run["dataset_id"]))
    version = _frozen_version(business_url, index_version_id, str(run["knowledge_base_id"]))
    _assert_runtime_matches_frozen_config(settings, run, version, embedder)

    chunk_size, chunk_overlap = _frozen_chunking(str(version["chunking_version"]))
    dataset_path = DATASETS_PATH / dataset.filename
    if not dataset_path.is_file():
        raise AppError(
            "EVALUATION_DATASET_MISSING",
            "评测数据集文件缺失，无法运行正式评测。",
            500,
            {"dataset_id": dataset.dataset_id},
        )
    corpus, contents = load_corpus_dataset(dataset_path)

    report = run_corpus_baseline(
        corpus,
        contents,
        str(run["commit_sha"]),
        chunk_size,
        chunk_overlap,
        _baseline_report(business_url, run.get("baseline_report_id")),
        evaluation_url,
        embedder,
        reranker,
        retrieval_mode=settings.retrieval_mode,
        official=True,
        on_stage=lambda stage: update_evaluation_stage(business_url, evaluation_run_id, stage),
    )

    # 最后一道防线：报告必须能证明自己测的就是这个候选版本。前面的逐项校验漏掉任何一项
    # 都会在这里露出来，而不是等三层验证时以「验证未通过」的形式甩给用户。
    if report.config_fingerprint != str(version["config_fingerprint"]):
        raise AppError(
            "EVALUATION_FINGERPRINT_MISMATCH",
            "评测运行的配置与候选版本不一致，报告不可用于发布验证。",
            500,
            {
                "expected": str(version["config_fingerprint"])[:12],
                "actual": (report.config_fingerprint or "")[:12],
            },
        )
    update_evaluation_stage(business_url, evaluation_run_id, "persist_report")
    return report


def _frozen_version(
    business_url: str, index_version_id: str, knowledge_base_id: str
) -> dict[str, Any]:
    with psycopg.connect(business_url, row_factory=dict_row) as connection:
        version = connection.execute(
            """SELECT index_version_id, knowledge_base_id, status, config_fingerprint,
                      chunking_version, embedding_model, embedding_dimension, component_manifest
               FROM index_versions WHERE index_version_id=%s""",
            (index_version_id,),
        ).fetchone()
    if version is None:
        raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
    if str(version["knowledge_base_id"]) != knowledge_base_id:
        # 入队时版本属于哪个知识库是冻结事实；对不上说明这行任务已经不可信。
        raise AppError(
            "INDEX_VERSION_SCOPE_CHANGED",
            "候选版本已不属于原知识库，评测任务作废。",
            409,
        )
    return dict(version)


def _assert_runtime_matches_frozen_config(
    settings: Settings, run: dict[str, Any], version: dict[str, Any], embedder: Any
) -> None:
    if str(run["config_fingerprint"] or "") != str(version["config_fingerprint"]):
        raise AppError(
            "INDEX_CONFIG_CHANGED_AFTER_ENQUEUE",
            "候选版本的配置指纹已变化，该评测任务不再有效。",
            409,
        )
    frozen_model = str(version["embedding_model"])
    runtime_model = str(getattr(embedder, "model_name", ""))
    if runtime_model != frozen_model:
        raise AppError(
            "EVALUATION_EMBEDDING_MISMATCH",
            "评测使用的向量模型与候选版本不一致。",
            500,
            {"expected": frozen_model, "actual": runtime_model},
        )
    frozen_manifest = dict(version["component_manifest"] or {})
    runtime_manifest = component_manifest(reranker_model=settings.reranker_model)
    drifted = sorted(
        key
        for key, value in runtime_manifest.items()
        if str(frozen_manifest.get(key, "")) != str(value)
    )
    if drifted:
        # 组件清单进指纹计算。这里不一致就一定产出对不上的报告，提前失败比跑完再说清楚。
        raise AppError(
            "EVALUATION_COMPONENTS_MISMATCH",
            "评测运行时的组件版本与候选版本不一致。",
            500,
            {"fields": drifted},
        )


def _frozen_chunking(chunking_version: str) -> tuple[int, int]:
    try:
        _, chunk_size, chunk_overlap = parse_chunking_version(chunking_version)
    except ValueError as exc:
        raise AppError(
            "INDEX_VERSION_CONFIG_INCOMPLETE",
            "该版本的切片配置无法解析，不能运行正式评测。",
            409,
            {"chunking_version": chunking_version},
        ) from exc
    return chunk_size, chunk_overlap


def _baseline_report(
    business_url: str, baseline_report_id: Any
) -> RetrievalEvaluationReport | None:
    """加载入队时选定的基线报告。

    找不到就返回 None 而不是失败：基线只影响「相对基线不回退」这项判定，缺了它评测
    照样能跑，报告里的 baseline 字段会如实为空。反过来，如果这里因为基线缺失就整个
    任务失败，第一次上线的知识库永远排不出一次评测。
    """

    if not baseline_report_id:
        return None
    with psycopg.connect(business_url, row_factory=dict_row) as connection:
        row = connection.execute(
            """SELECT report_payload FROM evaluation_runs
               WHERE evaluation_type='retrieval' AND status='succeeded'
                 AND report_payload->>'report_id'=%s
               ORDER BY run_at DESC LIMIT 1""",
            (str(baseline_report_id),),
        ).fetchone()
    if row is None or not row["report_payload"]:
        return None
    return RetrievalEvaluationReport.model_validate(row["report_payload"])
