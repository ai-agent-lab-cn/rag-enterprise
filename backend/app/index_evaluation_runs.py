"""正式检索评测的运行事实与可靠队列。

`evaluation_runs` 在 V39 之前只有一处写入：验收跑完之后补一行结论。候选索引版本要
拿到一份能用于三层门禁的正式报告，只能由人在命令行跑 `run_corpus_baseline` 再把 JSON
提交进仓库——页面上的「执行三层验证」因此永远选不到与当前配置指纹匹配的报告。

这个模块把同一张表当成队列用：入队即冻结候选版本、配置指纹、数据集与基线报告，由
独立的 Evaluation Worker 领取执行。它只管运行事实，不碰候选版本的生命周期状态——
正式评测是 `validating` 阶段的前置证据，不是一个新的版本状态。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .errors import AppError
from .pipeline_governance import create_operation

if TYPE_CHECKING:  # 运行时不导入 backend.evaluation：那一层依赖模型与语料文件。
    from backend.evaluation.report import RetrievalEvaluationReport


# 只有 validating 与 validation_failed 的候选可以发起正式评测：building 还没有产物，
# ready 已经拿到过结论，active/previous 是线上版本，重跑评测不改变它们的发布事实。
EVALUATABLE_VERSION_STATUSES = frozenset({"validating", "validation_failed"})

TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

# 一次正式评测的真实阶段。Worker 按序写入，前端 index_evaluation 流水线逐格对应；
# 新增或改名必须同步 frontend/src/components/ui/PipelineStepper.tsx，
# 否则 test_frontend_pipeline_stages_cover_what_the_backend_writes 会红。
EVALUATION_STAGES: tuple[str, ...] = (
    "prepare_dataset",
    "build_corpus",
    "retrieve",
    "rerank",
    "calculate_metrics",
    "persist_report",
    "complete",
)

_STAGE_PERCENT = {
    "prepare_dataset": 10,
    "build_corpus": 40,
    "retrieve": 60,
    "rerank": 75,
    "calculate_metrics": 85,
    "persist_report": 95,
    "complete": 100,
}

# 评测失败后的重试退避。索引任务用 5 秒，因为单文档解析很快；一次评测要重建整份语料，
# 立刻重试大概率撞上同一个原因（模型没就绪、评测库连不上），退避给它一点缓冲。
RETRY_BACKOFF_SECONDS = 30

# 失败原因的用户文案按错误码映射。`error_message` 存的是技术详情（可能带连接串或
# 宿主绝对路径），不能直接送进响应；但只给一个错误码用户也看不懂，所以这里给每个
# 已知失败模式配一句能指导下一步动作的中文。
FAILURE_MESSAGES: dict[str, str] = {
    "EVALUATION_DATABASE_NOT_CONFIGURED": "本部署未配置正式评测数据库，请联系运维开启后重试。",
    "EVALUATION_DATABASE_NOT_ISOLATED": "评测数据库与业务数据库相同，已中止以保护业务数据。",
    "EVALUATION_DATABASE_NOT_FOUND": "评测数据库不存在，且不是可自动创建的专用库。",
    "EVALUATION_DATASET_MISSING": "评测数据集文件缺失，请检查部署镜像是否完整。",
    "EVALUATION_DATASET_NOT_ALLOWED": "该评测数据集不在服务端允许范围内。",
    "EVALUATION_EMBEDDING_MISMATCH": "评测使用的向量模型与候选版本不一致，请对齐模型配置后重新运行。",
    "EVALUATION_COMPONENTS_MISMATCH": "评测运行时的组件版本与候选版本不一致，请对齐配置后重新运行。",
    "EVALUATION_FINGERPRINT_MISMATCH": "评测结果的配置与候选版本不一致，报告不可用于发布验证。",
    "EVALUATION_WORKER_LEASE_LOST": "执行评测的 Worker 中断，任务已重新排队。",
    "INDEX_CONFIG_CHANGED_AFTER_ENQUEUE": "候选版本的配置在排队期间发生变化，请重新创建评测任务。",
    "INDEX_VERSION_SCOPE_CHANGED": "候选版本已不属于原知识库，该评测任务作废。",
    "INDEX_VERSION_CONFIG_INCOMPLETE": "该版本没有完整配置快照，无法运行可用于发布的正式评测。",
    "INDEX_VERSION_NOT_FOUND": "候选版本已不存在，该评测任务作废。",
    "POSTGRES_REQUIRED": "正式评测需要 PostgreSQL 运行时。",
}

_GENERIC_FAILURE_MESSAGE = "正式评测执行失败，请查看 Evaluation Worker 日志定位原因。"


def failure_message(code: str | None) -> str | None:
    """把稳定错误码翻译成用户文案；未知错误码给通用文案，不回显技术详情。"""

    if not code:
        return None
    return FAILURE_MESSAGES.get(code, _GENERIC_FAILURE_MESSAGE)


_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")

# Git 用全零 SHA 表示「没有这个对象」。报告的 commit 字段有 ^[0-9a-f]{7,40}$ 约束，
# 容器里拿不到构建 commit 时用它明确表达未知，而不是塞一个看起来像真 commit 的哈希。
UNKNOWN_COMMIT = "0000000"


@dataclass(frozen=True)
class EvaluationDataset:
    """服务端允许的评测数据集。

    `slug` 是 API 与页面使用的稳定标识（数据集文件名），`dataset_id` 与
    `dataset_version` 是数据集文件自己声明的值——两者不同：`corpus_v2.json` 里写的是
    `rag-enterprise-corpus`。运行记录存后者，才能和报告对上账。

    值在这里冻结而不是每次读文件，是为了让入队不依赖文件系统；
    `test_evaluation_dataset_allowlist_matches_the_dataset_files` 负责在文件改动时报红。
    """

    slug: str
    filename: str
    dataset_id: str
    dataset_version: str


EVALUATION_DATASETS: dict[str, EvaluationDataset] = {
    "corpus_v2": EvaluationDataset(
        "corpus_v2", "corpus_v2.json", "rag-enterprise-corpus", "2.0.0"
    ),
    "corpus_v2_paraphrased": EvaluationDataset(
        "corpus_v2_paraphrased",
        "corpus_v2_paraphrased.json",
        "rag-enterprise-corpus-paraphrased",
        "1.1.0",
    ),
}


def resolve_dataset(dataset_id: str) -> EvaluationDataset:
    """把请求里的数据集标识翻译成服务端白名单里的冻结条目。

    白名单是必须的：`dataset_id` 决定 Worker 去读哪个语料目录，放任前端传值等于把
    文件路径交给请求方。同时也接受数据集自称的 `dataset_id`，页面上显示的是那个值，
    用户按它复述时不该被判成非法参数。
    """

    candidate = (dataset_id or "").strip()
    if candidate in EVALUATION_DATASETS:
        return EVALUATION_DATASETS[candidate]
    for dataset in EVALUATION_DATASETS.values():
        if candidate == dataset.dataset_id:
            return dataset
    raise AppError(
        "EVALUATION_DATASET_NOT_ALLOWED",
        "该评测数据集不在服务端允许范围内。",
        400,
        {"allowed": sorted(EVALUATION_DATASETS)},
    )


def resolved_commit_sha() -> str:
    raw = (os.getenv("APP_COMMIT_SHA") or "").strip().lower()
    return raw if _COMMIT_PATTERN.match(raw) else UNKNOWN_COMMIT


def create_evaluation_run(
    database_url: str,
    index_version_id: str,
    dataset_id: str,
    requested_by: str,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """为候选版本排一次正式检索评测，并把入队时的事实冻结下来。

    入队证据必须在事务内、锁住版本行之后采集：配置指纹由后端从 `index_versions`
    重新读出，不接受调用方传入——否则「报告跑的是哪套配置」这件事就由请求方说了算，
    三层门禁的指纹比对也就失去意义。
    """

    dataset = resolve_dataset(dataset_id)
    commit_sha = resolved_commit_sha()
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        version = connection.execute(
            """SELECT index_version_id, knowledge_base_id, status, config_fingerprint,
                      chunking_version, parser_version, embedding_model, embedding_dimension,
                      config_snapshot, component_manifest, config_completeness
               FROM index_versions WHERE index_version_id=%s FOR UPDATE""",
            (index_version_id,),
        ).fetchone()
        if version is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
        if str(version["status"]) not in EVALUATABLE_VERSION_STATUSES:
            raise AppError(
                "INDEX_VERSION_NOT_EVALUATABLE",
                "只有等待验证的候选版本可以运行正式评测。",
                409,
                {"status": str(version["status"])},
            )
        if str(version["config_completeness"]) != "complete":
            # 历史版本没有完整配置快照，跑出来的报告无法证明测的是这一版；
            # 这类版本保留查看、退役与清理，但不进入评测与放行。
            raise AppError(
                "INDEX_VERSION_CONFIG_INCOMPLETE",
                "该版本没有完整配置快照，无法运行可用于发布的正式评测。",
                409,
            )
        active = connection.execute(
            """SELECT evaluation_run_id FROM evaluation_runs
               WHERE evaluation_type='retrieval' AND index_version_id=%s
                 AND status IN ('queued','running')
               LIMIT 1""",
            (index_version_id,),
        ).fetchone()
        if active is not None:
            raise AppError(
                "EVALUATION_RUN_IN_PROGRESS",
                "该版本已有正在进行的正式评测。",
                409,
                {"evaluation_run_id": str(active["evaluation_run_id"])},
            )

        baseline = connection.execute(
            """SELECT er.report_payload->>'report_id' AS report_id FROM evaluation_runs er
               JOIN knowledge_bases kb
                 ON kb.active_index_version_id=er.index_version_id
               WHERE kb.knowledge_base_id=%s AND er.evaluation_type='retrieval'
                 AND er.status='succeeded' AND er.official
                 AND er.report_payload IS NOT NULL
               ORDER BY er.run_at DESC LIMIT 1""",
            (version["knowledge_base_id"],),
        ).fetchone()
        baseline_report_id = str(baseline["report_id"]) if baseline else None

        operation_id = create_operation(
            connection,
            operation_type="index_evaluation",
            knowledge_base_id=str(version["knowledge_base_id"]),
            idempotency_key=f"index-evaluation:{index_version_id}:{uuid4().hex[:12]}",
            progress_mode="stages",
        )
        evaluation_run_id = f"eval_{uuid4().hex[:16]}"
        connection.execute(
            """INSERT INTO evaluation_runs
               (evaluation_run_id, evaluation_type, dataset_id, dataset_version, commit_sha,
                knowledge_base_id, index_version_id, chunking_version, parser_version,
                models, parameters, metrics, passed, official, status, operation_id,
                config_fingerprint, baseline_report_id, requested_by, max_attempts,
                run_at, created_at, updated_at)
               VALUES (%s,'retrieval',%s,%s,%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb,NULL,false,
                       'queued',%s,%s,%s,%s,%s,clock_timestamp(),now(),now())""",
            (
                evaluation_run_id,
                dataset.dataset_id,
                dataset.dataset_version,
                commit_sha,
                version["knowledge_base_id"],
                index_version_id,
                version["chunking_version"],
                version["parser_version"],
                Jsonb({"embedding": str(version["embedding_model"])}),
                Jsonb(
                    {
                        "dataset_slug": dataset.slug,
                        "config_snapshot": version["config_snapshot"],
                        "component_manifest": version["component_manifest"],
                        "embedding_dimension": int(version["embedding_dimension"]),
                    }
                ),
                operation_id,
                version["config_fingerprint"],
                baseline_report_id,
                requested_by,
                max_attempts,
            ),
        )
        created = _row(connection, evaluation_run_id)
    return created


def list_evaluation_runs(
    database_url: str, knowledge_base_id: str, index_version_id: str | None = None
) -> list[dict[str, Any]]:
    """列出正式评测运行，最新在前；不给 `index_version_id` 就列整个知识库的。

    JOIN `index_versions` 做归属校验而不是只按 index_version_id 过滤：版本 ID 可以从
    别的知识库猜出来，接口不能因此泄露另一个库的评测历史。

    整库列表是运行记录详情需要的：评测记录挂在版本上，而版本激活之后就不再是候选，
    只按候选版本取的话，**版本一激活，它那次评测的详情就永远打不开了**——页面上
    仍然列着这条运行记录，点开却只有「读取不到这次评测的明细」。
    """

    scoped = index_version_id is not None
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            f"""SELECT {_COLUMNS} FROM evaluation_runs er
                JOIN index_versions iv USING (index_version_id)
                WHERE er.evaluation_type='retrieval'
                  AND (%s::text IS NULL OR er.index_version_id=%s)
                  AND iv.knowledge_base_id=%s
                ORDER BY er.created_at DESC""",
            (index_version_id if scoped else None, index_version_id, knowledge_base_id),
        ).fetchall()
    return [dict(row) for row in rows]


def get_evaluation_run(
    database_url: str, knowledge_base_id: str, evaluation_run_id: str
) -> dict[str, Any] | None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            f"""SELECT {_COLUMNS}, er.report_payload FROM evaluation_runs er
                JOIN index_versions iv USING (index_version_id)
                WHERE er.evaluation_run_id=%s AND iv.knowledge_base_id=%s""",
            (evaluation_run_id, knowledge_base_id),
        ).fetchone()
    return dict(row) if row else None


def claim_evaluation_run(database_url: str, worker_id: str) -> dict[str, Any] | None:
    """领取一个待执行的正式评测。

    `FOR UPDATE SKIP LOCKED` 让多个 Worker 进程可以同时轮询而不互相阻塞；领取与
    `operations.status='running'` 必须在同一事务里更新，否则页面会出现「任务在跑但
    进度还是排队中」这种只能靠重启解释的状态。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        job = connection.execute(
            """SELECT * FROM evaluation_runs
               WHERE evaluation_type='retrieval' AND status='queued' AND available_at <= now()
               ORDER BY created_at
               FOR UPDATE SKIP LOCKED LIMIT 1"""
        ).fetchone()
        if job is None:
            return None
        connection.execute(
            """UPDATE evaluation_runs
               SET status='running', attempt_count=attempt_count+1, locked_at=now(),
                   locked_by=%s, started_at=COALESCE(started_at, now()),
                   error_code=NULL, error_message=NULL, updated_at=now()
               WHERE evaluation_run_id=%s""",
            (worker_id, job["evaluation_run_id"]),
        )
        connection.execute(
            """UPDATE operations SET status='running',
                      started_at=COALESCE(started_at, now()), updated_at=now()
               WHERE operation_id=%s""",
            (job["operation_id"],),
        )
        claimed = _row(connection, str(job["evaluation_run_id"]), include_payload=True)
    return claimed


def renew_evaluation_lease(database_url: str, evaluation_run_id: str) -> None:
    """续租。一次评测可能跑很久，不续租会被租约恢复判成僵死任务并重新入队。"""

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE evaluation_runs SET locked_at=now(), updated_at=now()
               WHERE evaluation_run_id=%s AND status='running'""",
            (evaluation_run_id,),
        )


def update_evaluation_stage(database_url: str, evaluation_run_id: str, stage: str) -> None:
    """按实际执行点推进 Operation 阶段与进度。"""

    if stage not in _STAGE_PERCENT:
        raise ValueError(f"unsupported evaluation stage: {stage}")
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE operations SET current_stage=%s, progress_percent=%s,
                      started_at=COALESCE(started_at, now()), updated_at=now()
               WHERE operation_id=(SELECT operation_id FROM evaluation_runs
                                   WHERE evaluation_run_id=%s)""",
            (stage, _STAGE_PERCENT[stage], evaluation_run_id),
        )
        connection.execute(
            """UPDATE evaluation_runs SET locked_at=now(), updated_at=now()
               WHERE evaluation_run_id=%s AND status='running'""",
            (evaluation_run_id,),
        )


def finish_evaluation_run(
    database_url: str, evaluation_run_id: str, report: RetrievalEvaluationReport
) -> dict[str, Any]:
    """把一次成功的评测收口成不可变运行事实。

    `official` 与 `passed` 分开落库：前者说明这份报告来自受控的正式运行，可以作为三层
    验证的证据；后者只说明它有没有达到冻结阈值。两者此前在 `run_corpus_baseline`
    里被 `official = passed` 绑在一起，结果是「跑完了但没达标」的报告根本进不了门禁，
    页面只能显示「缺少可用报告」而说不出真实原因。
    """

    payload = report.model_dump(mode="json")
    metrics = {
        key: value
        for key, value in payload.items()
        if isinstance(value, dict) and "threshold" in value
    }
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        run = connection.execute(
            """SELECT operation_id, parameters, status FROM evaluation_runs
               WHERE evaluation_run_id=%s FOR UPDATE""",
            (evaluation_run_id,),
        ).fetchone()
        if run is None:
            raise AppError("EVALUATION_RUN_NOT_FOUND", "未找到该正式评测记录。", 404)
        if str(run["status"]) in TERMINAL_RUN_STATUSES:
            # 取消是操作者确定的终态。迟到的 Worker 可以结束自身，但不能把它改回成功。
            return _row(connection, evaluation_run_id, include_payload=True)
        parameters = dict(run["parameters"] or {})
        parameters["report_parameters"] = payload.get("parameters", {})
        connection.execute(
            """UPDATE evaluation_runs
               SET status='succeeded', passed=%s, official=%s, metrics=%s, models=%s,
                   parameters=%s, report_payload=%s,
                   run_at=%s, finished_at=now(), locked_at=NULL, locked_by=NULL,
                   error_code=NULL, error_message=NULL, updated_at=now()
               WHERE evaluation_run_id=%s""",
            (
                bool(report.passed),
                bool(report.official),
                Jsonb(metrics),
                Jsonb(payload.get("models", {})),
                Jsonb(parameters),
                Jsonb(payload),
                report.run_at,
                evaluation_run_id,
            ),
        )
        connection.execute(
            """UPDATE operations SET status='succeeded', current_stage='complete',
                      progress_percent=100, error_code=NULL, error_message=NULL,
                      started_at=COALESCE(started_at, now()), finished_at=now(), updated_at=now()
               WHERE operation_id=%s""",
            (run["operation_id"],),
        )
        finished = _row(connection, evaluation_run_id, include_payload=True)
    return finished


def fail_evaluation_run(
    database_url: str, evaluation_run_id: str, code: str, message: str
) -> None:
    """记录一次失败。未超过重试上限的回到队列，超过的进入 failed 终态。

    `error_message` 保存技术详情供管理员定位；接口层只输出稳定错误码对应的用户文案，
    不把宿主路径或连接串透给浏览器。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        run = connection.execute(
            """SELECT operation_id, attempt_count, max_attempts, status
               FROM evaluation_runs WHERE evaluation_run_id=%s FOR UPDATE""",
            (evaluation_run_id,),
        ).fetchone()
        if run is None or str(run["status"]) in TERMINAL_RUN_STATUSES:
            return
        terminal = int(run["attempt_count"]) >= int(run["max_attempts"])
        connection.execute(
            """UPDATE evaluation_runs
               SET status=%s, error_code=%s, error_message=%s,
                   available_at=now() + make_interval(secs => %s),
                   locked_at=NULL, locked_by=NULL,
                   finished_at=CASE WHEN %s THEN now() ELSE NULL END, updated_at=now()
               WHERE evaluation_run_id=%s""",
            (
                "failed" if terminal else "queued",
                code,
                message[:1000],
                0 if terminal else RETRY_BACKOFF_SECONDS,
                terminal,
                evaluation_run_id,
            ),
        )
        # 非终态失败保留最后到达的阶段：那是定位问题的唯一线索，覆盖成 'retry_wait'
        # 只会让「卡在哪一步」这个问题失去答案。
        connection.execute(
            """UPDATE operations
               SET status=%s, current_stage=CASE WHEN %s THEN 'failed' ELSE current_stage END,
                   error_code=%s, error_message=%s,
                   finished_at=CASE WHEN %s THEN now() ELSE NULL END, updated_at=now()
               WHERE operation_id=%s""",
            (
                "failed" if terminal else "queued",
                terminal,
                code,
                message[:1000],
                terminal,
                run["operation_id"],
            ),
        )


def retry_evaluation_run(
    database_url: str, knowledge_base_id: str, evaluation_run_id: str, requested_by: str
) -> dict[str, Any]:
    """重新排入一次已失败的评测。不新建记录，因此重试历史留在同一行的 attempt_count 上。"""

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        run = connection.execute(
            """SELECT er.operation_id, er.status, er.attempt_count, er.max_attempts,
                      er.index_version_id
               FROM evaluation_runs er JOIN index_versions iv USING (index_version_id)
               WHERE er.evaluation_run_id=%s AND iv.knowledge_base_id=%s FOR UPDATE OF er""",
            (evaluation_run_id, knowledge_base_id),
        ).fetchone()
        if run is None:
            raise AppError("EVALUATION_RUN_NOT_FOUND", "未找到该正式评测记录。", 404)
        if str(run["status"]) != "failed":
            raise AppError(
                "EVALUATION_RUN_NOT_RETRIABLE",
                "只有失败的正式评测可以重新运行。",
                409,
                {"status": str(run["status"])},
            )
        if int(run["attempt_count"]) >= int(run["max_attempts"]):
            raise AppError(
                "EVALUATION_RUN_RETRY_EXHAUSTED",
                "该评测已达到最大重试次数，请创建新的评测任务。",
                409,
                {"attempt_count": int(run["attempt_count"])},
            )
        blocking = connection.execute(
            """SELECT evaluation_run_id FROM evaluation_runs
               WHERE evaluation_type='retrieval' AND index_version_id=%s
                 AND status IN ('queued','running') LIMIT 1""",
            (run["index_version_id"],),
        ).fetchone()
        if blocking is not None:
            raise AppError(
                "EVALUATION_RUN_IN_PROGRESS",
                "该版本已有正在进行的正式评测。",
                409,
                {"evaluation_run_id": str(blocking["evaluation_run_id"])},
            )
        connection.execute(
            """UPDATE evaluation_runs
               SET status='queued', available_at=now(), error_code=NULL, error_message=NULL,
                   locked_at=NULL, locked_by=NULL, finished_at=NULL, requested_by=%s,
                   updated_at=now()
               WHERE evaluation_run_id=%s""",
            (requested_by, evaluation_run_id),
        )
        connection.execute(
            """UPDATE operations SET status='queued', current_stage='queued',
                      progress_percent=NULL, error_code=NULL, error_message=NULL,
                      finished_at=NULL, updated_at=now()
               WHERE operation_id=%s""",
            (run["operation_id"],),
        )
        retried = _row(connection, evaluation_run_id)
    return retried


def cancel_evaluation_run(
    database_url: str, knowledge_base_id: str, evaluation_run_id: str
) -> dict[str, Any]:
    """取消尚未开始的评测。

    运行中的取消本轮不实现，返回 409 而不是把记录改成 cancelled：Worker 进程没有接收
    取消信号的通道，改了状态只会让页面显示已取消而语料仍在评测库里跑，
    到时候 finish 还会撞上一个已终态的记录。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        run = connection.execute(
            """SELECT er.operation_id, er.status FROM evaluation_runs er
               JOIN index_versions iv USING (index_version_id)
               WHERE er.evaluation_run_id=%s AND iv.knowledge_base_id=%s FOR UPDATE OF er""",
            (evaluation_run_id, knowledge_base_id),
        ).fetchone()
        if run is None:
            raise AppError("EVALUATION_RUN_NOT_FOUND", "未找到该正式评测记录。", 404)
        if str(run["status"]) == "cancelled":
            return _row(connection, evaluation_run_id)
        if str(run["status"]) != "queued":
            raise AppError(
                "EVALUATION_RUN_NOT_CANCELLABLE",
                "已经开始或已结束的正式评测无法取消。",
                409,
                {"status": str(run["status"])},
            )
        connection.execute(
            """UPDATE evaluation_runs SET status='cancelled', finished_at=now(),
                      locked_at=NULL, locked_by=NULL, updated_at=now()
               WHERE evaluation_run_id=%s""",
            (evaluation_run_id,),
        )
        connection.execute(
            """UPDATE operations SET status='cancelled', current_stage='cancelled',
                      finished_at=now(), updated_at=now()
               WHERE operation_id=%s""",
            (run["operation_id"],),
        )
        cancelled = _row(connection, evaluation_run_id)
    return cancelled


def recover_stale_evaluation_runs(database_url: str, stale_seconds: int) -> int:
    """把租约过期的运行拉回队列。

    没有这一步，被 SIGKILL / OOMKill 掉的 Worker 会让记录永远停在 running：部分唯一
    索引把它算作活动运行，管理员再点「运行正式评测」只会一直得到 409，页面显示评测中
    却没有任何进程在跑。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        stale = connection.execute(
            """UPDATE evaluation_runs
               SET status='queued', locked_at=NULL, locked_by=NULL, available_at=now(),
                   error_code='EVALUATION_WORKER_LEASE_LOST',
                   error_message='Worker 租约过期，任务已重新入队。', updated_at=now()
               WHERE evaluation_type='retrieval' AND status='running'
                 AND locked_at < now() - make_interval(secs => %s)
               RETURNING operation_id""",
            (stale_seconds,),
        ).fetchall()
        operation_ids = [str(row["operation_id"]) for row in stale if row["operation_id"]]
        if operation_ids:
            connection.execute(
                """UPDATE operations SET status='queued', updated_at=now()
                   WHERE operation_id = ANY(%s) AND status='running'""",
                (operation_ids,),
            )
    return len(stale)


# 显式列出返回字段而不是 SELECT *：report_payload 是整份报告，列表接口带上它会让
# 一次「运行记录」请求拖着几十 KB 的指标明细；需要它的地方显式加。
_COLUMNS = """er.evaluation_run_id, er.evaluation_type, er.dataset_id, er.dataset_version,
              er.commit_sha, er.knowledge_base_id, er.index_version_id, er.chunking_version,
              er.parser_version, er.models, er.parameters, er.metrics, er.passed, er.official,
              er.status, er.operation_id, er.config_fingerprint, er.baseline_report_id,
              er.requested_by, er.attempt_count, er.max_attempts, er.available_at,
              er.locked_at, er.locked_by, er.error_code, er.error_message,
              er.run_at, er.started_at, er.finished_at, er.created_at, er.updated_at"""


def _row(
    connection: psycopg.Connection[Any], evaluation_run_id: str, *, include_payload: bool = False
) -> dict[str, Any]:
    payload = ", er.report_payload" if include_payload else ""
    row = connection.execute(
        f"SELECT {_COLUMNS}{payload} FROM evaluation_runs er WHERE er.evaluation_run_id=%s",  # noqa: S608
        (evaluation_run_id,),
    ).fetchone()
    if row is None:
        raise AppError("EVALUATION_RUN_NOT_FOUND", "未找到该正式评测记录。", 404)
    return dict(row)
