"""正式检索评测的队列仓储（`index_evaluation_runs`）与 Worker 编排（`index_evaluation_worker`）。

这两个模块把「正式评测」从「人在命令行跑完再把 JSON 提交进仓库」变成产品内的可靠队列，
因此需要守住的是三类事实：入队时冻结了什么、Worker 拿到之后还允不允许跑、跑完之后
落库的结论是不是原样。

**这里不用模块级 pytestmark。** 本文件有七条测试完全不碰数据库——数据集白名单与数据集
文件的一致性、连接串归一化、评测库隔离守卫——它们恰恰是最该在任何机器上都真的跑起来的
那几条：白名单里的 dataset_id/version 是从数据集文件抄下来的冻结副本，文件改了它不会
自己发现。挂模块级 skipif 会让它们在没有 TEST_DATABASE_URL 的机器上一起消失，而跳过在
日志里和通过长得一模一样（CLAUDE.md 第五条）。需要数据库的用例逐个挂 `requires_database`。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.chunking import chunking_version
from backend.app.config import Settings
from backend.app.errors import AppError
from backend.app.index_evaluation_runs import (
    EVALUATION_DATASETS,
    EVALUATION_STAGES,
    cancel_evaluation_run,
    claim_evaluation_run,
    create_evaluation_run,
    fail_evaluation_run,
    finish_evaluation_run,
    get_evaluation_run,
    list_evaluation_runs,
    recover_stale_evaluation_runs,
    resolve_dataset,
    retry_evaluation_run,
    update_evaluation_stage,
)
from backend.app.index_evaluation_worker import (
    DATASETS_PATH,
    normalized_dsn,
    require_isolated_evaluation_database,
    run_evaluation_once,
)
from backend.app.index_validation import activate_with_report
from backend.app.index_versions import (
    component_manifest,
    create_building_version,
    finalize_building_version,
    get_version,
)
from backend.tests.test_index_versions import (
    EMBEDDING_DIMENSION,
    KNOWLEDGE_BASE_ID,
    _add_chunks,
    _add_document,
    _create,
    _fingerprint_of,
    _matching_report,
    _passing_report,
    _ready_version,
    _reset,
)

requires_database = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector"
)

# 冻结在候选版本 component_manifest 里的精排模型。Worker 的漂移检查比的是
# `component_manifest(reranker_model=settings.reranker_model)` 与这份冻结清单，
# 所以造版本和造 Settings 必须用同一个值，否则测的就成了漂移路径本身。
RERANKER_MODEL = "test/reranker"

BUSINESS_DATABASE_URL = "postgresql://rag@postgres:5432/rag_enterprise"
EVALUATION_DATABASE_URL = "postgresql://rag@postgres:5432/rag_enterprise_evaluation"


def _database_url() -> str:
    return os.environ["TEST_DATABASE_URL"]


def _worker_settings(**overrides: Any) -> Settings:
    """构造一份不受本机环境影响的 Settings。

    `frontend_origin` 是 `validate_security_boundaries` 的必需项；其余每个参与断言的
    字段都显式传入，免得 shell 里恰好有同名环境变量时测的是别人的配置。
    """

    return Settings(frontend_origin="http://localhost:5173", **overrides)


def _candidate(
    database_url: str,
    name: str,
    document_version_id: str,
    *,
    chunk_size: int = 700,
    chunk_overlap: int = 100,
    complete: bool = True,
) -> str:
    """建一个 validating 的候选索引版本，返回它的 index_version_id。

    `complete=True` 时必须传 `components`：`create_building_version_in_transaction` 里写的是
    `config_completeness = "complete" if manifest else "unknown"`（index_versions.py），
    不传 manifest 的版本会被 `create_evaluation_run` 以 INDEX_VERSION_CONFIG_INCOMPLETE
    直接拒绝。所以除了专门验证那条拒绝的用例，候选一律带上 manifest。
    """

    index_version_id, _ = create_building_version(
        database_url,
        KNOWLEDGE_BASE_ID,
        chunking_version=chunking_version(chunk_size, chunk_overlap),
        parser_version="structured-1",
        embedding_model="test/embedding",
        embedding_dimension=EMBEDDING_DIMENSION,
        processing_options={"chunk_size": chunk_size, "chunk_overlap": chunk_overlap},
        rebuild_batch_id=f"rbd_{name}",
        components=component_manifest(reranker_model=RERANKER_MODEL) if complete else None,
    )
    _add_chunks(database_url, index_version_id, document_version_id, count=2)
    assert finalize_building_version(database_url, index_version_id) == "validating"
    return index_version_id


def _prepared_candidate(
    database_url: str,
    *,
    chunk_size: int = 700,
    chunk_overlap: int = 100,
    complete: bool = True,
) -> str:
    """清库 → 一份文档 → 一个 validating 候选版本。绝大多数队列用例的共同前置。"""

    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    return _candidate(
        database_url,
        "first",
        document_version_id,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        complete=complete,
    )


def _run_row(database_url: str, evaluation_run_id: str) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT * FROM evaluation_runs WHERE evaluation_run_id=%s", (evaluation_run_id,)
        ).fetchone()
    assert row is not None
    return dict(row)


def _operation_row(database_url: str, operation_id: str) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT * FROM operations WHERE operation_id=%s", (operation_id,)
        ).fetchone()
    assert row is not None
    return dict(row)


def _set_version_status(database_url: str, index_version_id: str, status: str) -> None:
    """直接改候选版本状态。

    validation_failed 这个前置条件不走 `validate_index_version`：那条路对 complete 配置的
    候选是必然失败（没有 document_index_states、分块没有 metadata），把本文件的前置条件
    绑在另一个模块的失败判定上，它一变这里就会以看不懂的方式红。
    """

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE index_versions SET status=%s WHERE index_version_id=%s",
            (status, index_version_id),
        )


def _force_failed(database_url: str, evaluation_run_id: str) -> None:
    """把运行直接改成「已失败但还有重试次数」。

    `fail_evaluation_run` 只在 attempt_count >= max_attempts 时才置 failed，所以这个组合
    目前造不出来（见文件末尾说明）；重试的成功路径只能这样构造前置状态。
    error_code 必须一起写：evaluation_runs_terminal_evidence_check 要求 failed 有错误码。
    """

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE evaluation_runs
               SET status='failed', attempt_count=1, error_code='EVALUATION_RUN_FAILED',
                   error_message='构造的失败态', finished_at=now()
               WHERE evaluation_run_id=%s""",
            (evaluation_run_id,),
        )
        connection.execute(
            """UPDATE operations SET status='failed', current_stage='failed',
                      error_code='EVALUATION_RUN_FAILED', finished_at=now()
               WHERE operation_id=(SELECT operation_id FROM evaluation_runs
                                   WHERE evaluation_run_id=%s)""",
            (evaluation_run_id,),
        )


def _expire_lease(database_url: str, evaluation_run_id: str, *, seconds: int) -> None:
    """把租约时间推到过去。用 UPDATE 而不是 sleep——过期与否只取决于 locked_at。"""

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE evaluation_runs SET locked_at=now() - make_interval(secs => %s)
               WHERE evaluation_run_id=%s""",
            (seconds, evaluation_run_id),
        )


def _official_succeeded_run(database_url: str, index_version_id: str, report_id: str) -> None:
    """写一行已完成的正式检索评测，供 baseline 自动选取。"""

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO evaluation_runs
               (evaluation_run_id, evaluation_type, dataset_id, dataset_version, commit_sha,
                knowledge_base_id, index_version_id, metrics, passed, official, status,
                report_payload, run_at)
               VALUES (%s,'retrieval','rag-enterprise-corpus','2.0.0','0000000',%s,%s,
                       '{}'::jsonb, true, true, 'succeeded', %s, now())""",
            (
                f"eval_baseline_{report_id}",
                KNOWLEDGE_BASE_ID,
                index_version_id,
                Jsonb({"report_id": report_id}),
            ),
        )


class _FakeEmbedder:
    model_name = "test/embedding"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text) % 7), float(sum(map(ord, text)) % 11), 1.0] for text in texts]


class _DriftedEmbedder(_FakeEmbedder):
    model_name = "test/other-embedding"


class _FakeReranker:
    def score(self, query: str, passages: list[str]) -> list[float]:
        return [float(query[:1] in passage) for passage in passages]


def _forbidden_run_corpus_baseline(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("配置漂移必须在跑评测之前失败，不该走到 run_corpus_baseline")


def _patch_evaluation_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """挡掉真的去建评测库与探测评测库是否为空的两步。

    `run_evaluation_once` 一进来就调这两个函数，不替换的话每条 Worker 用例都会尝试
    CREATE DATABASE。它们各自的行为由 B 组的 `require_isolated_evaluation_database`
    用例单独覆盖。
    """

    monkeypatch.setattr(
        "backend.app.index_evaluation_worker.ensure_evaluation_database",
        lambda _config: EVALUATION_DATABASE_URL,
    )
    monkeypatch.setattr(
        "backend.app.index_evaluation_worker._require_empty_evaluation_database",
        lambda _url: None,
    )


# ---------------------------------------------------------------------------
# A 组：队列与仓储
# ---------------------------------------------------------------------------


@requires_database
def test_enqueue_rejects_a_candidate_that_is_still_building() -> None:
    """building 版本还没有产物，评测无从测起。"""

    database_url = _database_url()
    _reset(database_url)
    _add_document(database_url, "first")
    index_version_id = _create(database_url)
    assert get_version(database_url, index_version_id)["status"] == "building"

    with pytest.raises(AppError) as excinfo:
        create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")

    assert excinfo.value.code == "INDEX_VERSION_NOT_EVALUATABLE"
    assert excinfo.value.status_code == 409


@requires_database
def test_enqueue_rejects_ready_and_active_versions() -> None:
    """ready 已经拿到过结论，active 是线上版本：重跑评测不改变它们的发布事实。"""

    database_url = _database_url()
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    index_version_id = _ready_version(
        database_url, "first", document_version_id, chunking="v1-700-100"
    )

    with pytest.raises(AppError) as excinfo:
        create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")
    assert excinfo.value.code == "INDEX_VERSION_NOT_EVALUATABLE"

    activate_with_report(
        database_url, index_version_id, _matching_report(database_url, index_version_id)
    )
    assert get_version(database_url, index_version_id)["status"] == "active"

    with pytest.raises(AppError) as excinfo:
        create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")
    assert excinfo.value.code == "INDEX_VERSION_NOT_EVALUATABLE"


@requires_database
def test_enqueue_accepts_validating_and_validation_failed_candidates() -> None:
    """正式评测是 validating 阶段的前置证据，验证失败后重跑评测也必须被允许。"""

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)

    first = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")
    assert first["status"] == "queued"
    # 同版本只允许一个未完成运行，先把名额让出来再验证 validation_failed 这一支。
    cancel_evaluation_run(database_url, KNOWLEDGE_BASE_ID, str(first["evaluation_run_id"]))

    _set_version_status(database_url, index_version_id, "validation_failed")
    second = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")

    assert second["status"] == "queued"
    assert second["evaluation_run_id"] != first["evaluation_run_id"]


@requires_database
def test_enqueue_rejects_a_version_without_a_complete_config_snapshot() -> None:
    """没有完整配置快照的版本，跑出来的报告证明不了自己测的是哪一版配置。"""

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url, complete=False)
    assert get_version(database_url, index_version_id)["config_completeness"] == "unknown"

    with pytest.raises(AppError) as excinfo:
        create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")

    assert excinfo.value.code == "INDEX_VERSION_CONFIG_INCOMPLETE"
    assert excinfo.value.status_code == 409


@requires_database
def test_enqueue_rejects_a_second_run_for_the_same_version() -> None:
    """同一候选版本同时只能有一个未完成运行，第二次入队必须说得出撞上了谁。"""

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    first = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")

    with pytest.raises(AppError) as excinfo:
        create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")

    assert excinfo.value.code == "EVALUATION_RUN_IN_PROGRESS"
    assert excinfo.value.status_code == 409
    assert excinfo.value.details == {"evaluation_run_id": str(first["evaluation_run_id"])}


@requires_database
def test_database_rejects_a_second_active_run_for_the_same_version() -> None:
    """并发保护落在数据库上：绕过仓储直接 INSERT 也插不进第二条未完成运行。

    只有应用层判断的话，两个请求同时通过 SELECT 再各自 INSERT 就会留下两条 queued，
    Worker 会把同一个候选版本跑两遍。
    """

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")

    with pytest.raises(UniqueViolation):
        with psycopg.connect(database_url) as connection, connection.transaction():
            connection.execute(
                """INSERT INTO evaluation_runs
                   (evaluation_run_id, evaluation_type, dataset_id, dataset_version,
                    commit_sha, knowledge_base_id, index_version_id, status, run_at)
                   VALUES ('eval_duplicate','retrieval','rag-enterprise-corpus','2.0.0',
                           '0000000',%s,%s,'queued',now())""",
                (KNOWLEDGE_BASE_ID, index_version_id),
            )


@requires_database
def test_enqueue_recomputes_the_config_fingerprint_from_the_index_version() -> None:
    """指纹由后端在锁住版本行之后重读，调用方连传都传不了。

    指纹要是能由请求方给，「这份报告跑的是哪套配置」就成了请求方的一面之词，
    三层门禁的指纹比对也就失去意义。
    """

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)

    created = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")

    assert created["config_fingerprint"] == _fingerprint_of(database_url, index_version_id)
    assert len(str(created["config_fingerprint"])) == 64


@requires_database
def test_enqueue_creates_a_stage_tracked_operation() -> None:
    """一次评测要建语料、跑召回、跑精排、算指标，阶段是真实存在的，进度必须能投影出来。"""

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)

    created = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")

    operation = _operation_row(database_url, str(created["operation_id"]))
    assert operation["operation_type"] == "index_evaluation"
    assert operation["progress_mode"] == "stages"
    assert operation["knowledge_base_id"] == KNOWLEDGE_BASE_ID
    assert operation["status"] == "queued"


def test_dataset_allowlist_accepts_both_the_slug_and_the_self_declared_id() -> None:
    """白名单决定 Worker 去读哪个语料目录，放任传值等于把文件路径交给请求方。

    页面上显示的是数据集自称的 dataset_id，用户按它复述时不该被判成非法参数，
    所以两种写法都要解析得出同一条冻结记录。
    """

    assert resolve_dataset("corpus_v2").dataset_id == "rag-enterprise-corpus"
    assert resolve_dataset("rag-enterprise-corpus").slug == "corpus_v2"
    assert resolve_dataset("  corpus_v2  ").slug == "corpus_v2"

    with pytest.raises(AppError) as excinfo:
        resolve_dataset("../../etc/passwd")

    assert excinfo.value.code == "EVALUATION_DATASET_NOT_ALLOWED"
    assert excinfo.value.status_code == 400
    assert excinfo.value.details == {"allowed": sorted(EVALUATION_DATASETS)}


@requires_database
def test_enqueue_records_the_dataset_identity_the_report_will_carry() -> None:
    """落库的是数据集自称的 id 与版本，slug 另存在 parameters 里。

    报告里写的是 `rag-enterprise-corpus`/`2.0.0`；运行记录存 slug 的话，两边对不上账。
    """

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)

    created = create_evaluation_run(
        database_url, index_version_id, "rag-enterprise-corpus", "admin"
    )

    assert created["dataset_id"] == "rag-enterprise-corpus"
    assert created["dataset_version"] == "2.0.0"
    assert created["parameters"]["dataset_slug"] == "corpus_v2"
    assert created["parameters"]["embedding_dimension"] == EMBEDDING_DIMENSION
    assert created["models"] == {"embedding": "test/embedding"}
    assert created["requested_by"] == "admin"


@requires_database
def test_enqueue_picks_the_active_versions_official_report_as_baseline() -> None:
    """基线取当前 active 版本最近一次 official 且 succeeded 的报告。

    没有基线时「相对基线不回退」这一项就无从判断，而它是切换放行的实际牙齿。
    """

    database_url = _database_url()
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    active_version_id = _ready_version(
        database_url, "first", document_version_id, chunking="v1-700-100"
    )
    activate_with_report(
        database_url, active_version_id, _matching_report(database_url, active_version_id)
    )
    _official_succeeded_run(database_url, active_version_id, "corpus-20260101T000000Z")
    candidate_id = _candidate(
        database_url, "second", document_version_id, chunk_size=320, chunk_overlap=40
    )

    created = create_evaluation_run(database_url, candidate_id, "corpus_v2", "admin")

    assert created["baseline_report_id"] == "corpus-20260101T000000Z"


@requires_database
def test_enqueue_leaves_the_baseline_empty_when_there_is_no_official_report() -> None:
    """第一次上线的知识库没有任何正式报告，基线为空不该阻止排队。"""

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)

    created = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")

    assert created["baseline_report_id"] is None


@requires_database
def test_claim_marks_the_run_running_and_counts_the_attempt() -> None:
    """领取与 operations 转 running 必须一起发生。

    分开写就会出现「任务在跑但进度还是排队中」，那种状态只能靠重启解释。
    """

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    created = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")

    claimed = claim_evaluation_run(database_url, "worker-a")

    assert claimed is not None
    assert claimed["evaluation_run_id"] == created["evaluation_run_id"]
    assert claimed["status"] == "running"
    assert claimed["attempt_count"] == 1
    assert claimed["locked_by"] == "worker-a"
    assert claimed["locked_at"] is not None
    assert claimed["started_at"] is not None
    assert _operation_row(database_url, str(created["operation_id"]))["status"] == "running"
    # 队列里已经没有 queued 记录，第二个 Worker 空手而归而不是抢同一条。
    assert claim_evaluation_run(database_url, "worker-b") is None


@requires_database
def test_finish_stores_an_official_report_that_did_not_pass() -> None:
    """official 与 passed 是两件事，「跑完了但没达标」必须能原样落库。

    两者此前在 `run_corpus_baseline` 里被 `official = passed` 绑在一起，未达标的报告
    一律不标 official，于是三层验证永远选不到它，页面只能显示「缺少可用报告」，
    真正的原因（指标没到冻结阈值）在任何地方都看不到。
    """

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    created = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")
    evaluation_run_id = str(created["evaluation_run_id"])
    assert claim_evaluation_run(database_url, "worker-a") is not None
    report = _passing_report(
        _fingerprint_of(database_url, index_version_id), official=True, below_threshold=True
    )
    assert report.official is True
    assert report.passed is False

    finished = finish_evaluation_run(database_url, evaluation_run_id, report)

    assert finished["status"] == "succeeded"
    assert finished["official"] is True
    assert finished["passed"] is False
    assert finished["report_payload"]["report_id"] == "rep_switch_test"
    # run_at 对齐报告时间，而不是收口时间：否则运行记录与报告说的不是同一次运行。
    assert finished["run_at"] == datetime(2026, 8, 27, tzinfo=UTC)
    assert set(finished["metrics"]) == {
        "recall_at_5",
        "recall_at_10",
        "vector_mrr",
        "rerank_mrr",
        "ndcg_at_10",
        "metadata_filter_accuracy",
    }
    assert finished["metrics"]["recall_at_5"]["passed"] is False
    assert finished["models"] == {"embedding": "test/embedding", "reranker": "test/reranker"}
    assert finished["parameters"]["report_parameters"] == {"chunk_size": 700}
    # 入队时冻结的参数不被报告参数覆盖。
    assert finished["parameters"]["dataset_slug"] == "corpus_v2"
    assert finished["locked_by"] is None
    assert finished["finished_at"] is not None

    operation = _operation_row(database_url, str(created["operation_id"]))
    assert operation["status"] == "succeeded"
    assert operation["current_stage"] == "complete"
    assert float(operation["progress_percent"]) == 100.0


@requires_database
def test_failure_below_the_attempt_limit_returns_the_run_to_the_queue() -> None:
    """没到重试上限的失败回队列，并保留最后到达的阶段。

    把阶段覆盖成 'retry_wait' 之类的值，「卡在哪一步」这个问题就再也没有答案。
    """

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    created = create_evaluation_run(
        database_url, index_version_id, "corpus_v2", "admin", max_attempts=3
    )
    evaluation_run_id = str(created["evaluation_run_id"])
    assert claim_evaluation_run(database_url, "worker-a") is not None
    update_evaluation_stage(database_url, evaluation_run_id, "retrieve")

    fail_evaluation_run(
        database_url, evaluation_run_id, "EVALUATION_DATASET_MISSING", "语料文件缺失"
    )

    row = _run_row(database_url, evaluation_run_id)
    assert row["status"] == "queued"
    assert row["error_code"] == "EVALUATION_DATASET_MISSING"
    assert row["finished_at"] is None
    assert row["locked_by"] is None
    assert row["available_at"] > datetime.now(UTC)
    # 退避没到期之前不该被重新领取，否则「退避」只是写在字段里的说法。
    assert claim_evaluation_run(database_url, "worker-b") is None

    operation = _operation_row(database_url, str(created["operation_id"]))
    assert operation["status"] == "queued"
    assert operation["current_stage"] == "retrieve"


@requires_database
def test_failure_at_the_attempt_limit_is_terminal() -> None:
    """达到上限的失败进 failed 终态，错误码必须同时落在运行与 Operation 上。"""

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    created = create_evaluation_run(
        database_url, index_version_id, "corpus_v2", "admin", max_attempts=1
    )
    evaluation_run_id = str(created["evaluation_run_id"])
    assert claim_evaluation_run(database_url, "worker-a") is not None

    fail_evaluation_run(
        database_url, evaluation_run_id, "EVALUATION_DATABASE_NOT_ISOLATED", "评测库与业务库同库"
    )

    row = _run_row(database_url, evaluation_run_id)
    assert row["status"] == "failed"
    assert row["error_code"] == "EVALUATION_DATABASE_NOT_ISOLATED"
    assert row["finished_at"] is not None
    assert row["locked_by"] is None

    operation = _operation_row(database_url, str(created["operation_id"]))
    assert operation["status"] == "failed"
    assert operation["current_stage"] == "failed"
    assert operation["error_code"] == "EVALUATION_DATABASE_NOT_ISOLATED"


@requires_database
def test_retry_rejects_a_run_that_is_not_failed() -> None:
    """排队中的运行没什么可重试的，重试它只会把队列语义搅乱。"""

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    created = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")

    with pytest.raises(AppError) as excinfo:
        retry_evaluation_run(
            database_url, KNOWLEDGE_BASE_ID, str(created["evaluation_run_id"]), "admin"
        )

    assert excinfo.value.code == "EVALUATION_RUN_NOT_RETRIABLE"
    assert excinfo.value.status_code == 409
    assert excinfo.value.details == {"status": "queued"}


@requires_database
def test_retry_rejects_a_run_that_used_up_its_attempts() -> None:
    """重试不新建记录，因此次数用尽后只能创建新任务，而不是无限点同一行。"""

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    created = create_evaluation_run(
        database_url, index_version_id, "corpus_v2", "admin", max_attempts=1
    )
    evaluation_run_id = str(created["evaluation_run_id"])
    assert claim_evaluation_run(database_url, "worker-a") is not None
    fail_evaluation_run(database_url, evaluation_run_id, "EVALUATION_RUN_FAILED", "跑挂了")
    assert _run_row(database_url, evaluation_run_id)["status"] == "failed"

    with pytest.raises(AppError) as excinfo:
        retry_evaluation_run(database_url, KNOWLEDGE_BASE_ID, evaluation_run_id, "admin")

    assert excinfo.value.code == "EVALUATION_RUN_RETRY_EXHAUSTED"
    assert excinfo.value.status_code == 409
    assert excinfo.value.details == {"attempt_count": 1}


@requires_database
def test_retry_does_not_reach_across_knowledge_bases() -> None:
    """运行 ID 可以从别的知识库猜出来，接口不能因此让人重跑另一个库的评测。"""

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    created = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")

    with pytest.raises(AppError) as excinfo:
        retry_evaluation_run(
            database_url, "kb_other", str(created["evaluation_run_id"]), "admin"
        )

    assert excinfo.value.code == "EVALUATION_RUN_NOT_FOUND"
    assert excinfo.value.status_code == 404


@requires_database
def test_retry_requeues_a_failed_run_that_still_has_attempts() -> None:
    """重试把同一行放回队列并清掉上一次的失败痕迹，Operation 一并回到排队态。"""

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    created = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")
    evaluation_run_id = str(created["evaluation_run_id"])
    _force_failed(database_url, evaluation_run_id)

    retried = retry_evaluation_run(
        database_url, KNOWLEDGE_BASE_ID, evaluation_run_id, "operator"
    )

    assert retried["status"] == "queued"
    assert retried["error_code"] is None
    assert retried["finished_at"] is None
    assert retried["requested_by"] == "operator"
    # 重试历史留在同一行的 attempt_count 上，不清零。
    assert retried["attempt_count"] == 1
    assert claim_evaluation_run(database_url, "worker-a") is not None

    operation = _operation_row(database_url, str(created["operation_id"]))
    assert operation["current_stage"] == "queued"
    assert operation["error_code"] is None
    assert operation["finished_at"] is None


@requires_database
def test_cancel_is_idempotent_for_a_queued_run() -> None:
    """取消是操作者确定的终态，重复点第二次不该报错。"""

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    created = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")
    evaluation_run_id = str(created["evaluation_run_id"])

    first = cancel_evaluation_run(database_url, KNOWLEDGE_BASE_ID, evaluation_run_id)
    second = cancel_evaluation_run(database_url, KNOWLEDGE_BASE_ID, evaluation_run_id)

    assert first["status"] == "cancelled"
    assert first["finished_at"] is not None
    assert second["status"] == "cancelled"
    operation = _operation_row(database_url, str(created["operation_id"]))
    assert operation["status"] == "cancelled"
    assert operation["current_stage"] == "cancelled"
    # 取消之后名额释放，Worker 不该再领到它。
    assert claim_evaluation_run(database_url, "worker-a") is None


@requires_database
def test_cancel_rejects_a_running_run() -> None:
    """Worker 没有接收取消信号的通道，改成 cancelled 只会让页面撒谎。

    页面显示已取消而语料仍在评测库里跑，收口时 finish 还会撞上一个已终态的记录。
    """

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    created = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")
    assert claim_evaluation_run(database_url, "worker-a") is not None

    with pytest.raises(AppError) as excinfo:
        cancel_evaluation_run(
            database_url, KNOWLEDGE_BASE_ID, str(created["evaluation_run_id"])
        )

    assert excinfo.value.code == "EVALUATION_RUN_NOT_CANCELLABLE"
    assert excinfo.value.status_code == 409
    assert excinfo.value.details == {"status": "running"}


@requires_database
def test_recover_requeues_only_the_runs_whose_lease_expired() -> None:
    """被 SIGKILL 的 Worker 留下的 running 记录必须能回队列，正在跑的不能被抢走。

    不回收的话部分唯一索引把它算作活动运行，管理员再点「运行正式评测」永远得到 409，
    页面显示评测中却没有任何进程在跑。
    """

    database_url = _database_url()
    _reset(database_url)
    document_version_id = _add_document(database_url, "first")
    stale_version_id = _candidate(
        database_url, "stale", document_version_id, chunk_size=320, chunk_overlap=40
    )
    fresh_version_id = _candidate(
        database_url, "fresh", document_version_id, chunk_size=360, chunk_overlap=40
    )
    stale = create_evaluation_run(database_url, stale_version_id, "corpus_v2", "admin")
    fresh = create_evaluation_run(database_url, fresh_version_id, "corpus_v2", "admin")
    stale_id = str(stale["evaluation_run_id"])
    fresh_id = str(fresh["evaluation_run_id"])
    first = claim_evaluation_run(database_url, "worker-a")
    second = claim_evaluation_run(database_url, "worker-b")
    assert first is not None
    assert second is not None
    assert {str(first["evaluation_run_id"]), str(second["evaluation_run_id"])} == {
        stale_id,
        fresh_id,
    }
    _expire_lease(database_url, stale_id, seconds=7200)

    assert recover_stale_evaluation_runs(database_url, 3600) == 1

    stale_row = _run_row(database_url, stale_id)
    assert stale_row["status"] == "queued"
    assert stale_row["error_code"] == "EVALUATION_WORKER_LEASE_LOST"
    assert stale_row["locked_by"] is None
    assert _operation_row(database_url, str(stale["operation_id"]))["status"] == "queued"

    fresh_row = _run_row(database_url, fresh_id)
    assert fresh_row["status"] == "running"
    assert fresh_row["error_code"] is None
    assert _operation_row(database_url, str(fresh["operation_id"]))["status"] == "running"


@requires_database
def test_listing_and_reading_are_scoped_to_the_owning_knowledge_base() -> None:
    """版本 ID 能从别的知识库猜出来，接口不能因此泄露另一个库的评测历史。"""

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    created = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")
    evaluation_run_id = str(created["evaluation_run_id"])

    rows = list_evaluation_runs(database_url, KNOWLEDGE_BASE_ID, index_version_id)
    assert [str(row["evaluation_run_id"]) for row in rows] == [evaluation_run_id]
    assert list_evaluation_runs(database_url, "kb_other", index_version_id) == []

    detail = get_evaluation_run(database_url, KNOWLEDGE_BASE_ID, evaluation_run_id)
    assert detail is not None
    assert str(detail["evaluation_run_id"]) == evaluation_run_id
    assert get_evaluation_run(database_url, "kb_other", evaluation_run_id) is None


@requires_database
def test_every_declared_stage_is_writable_and_unknown_stages_are_rejected() -> None:
    """七个阶段逐格对应前端流水线；写不进去的阶段等于页面上一格永远点不亮。

    未知阶段用 ValueError 当场拒绝，而不是静默写一个前端认不出来的值。
    """

    database_url = _database_url()
    index_version_id = _prepared_candidate(database_url)
    created = create_evaluation_run(database_url, index_version_id, "corpus_v2", "admin")
    evaluation_run_id = str(created["evaluation_run_id"])
    operation_id = str(created["operation_id"])
    assert claim_evaluation_run(database_url, "worker-a") is not None

    observed: list[tuple[str, float]] = []
    for stage in EVALUATION_STAGES:
        update_evaluation_stage(database_url, evaluation_run_id, stage)
        operation = _operation_row(database_url, operation_id)
        observed.append((str(operation["current_stage"]), float(operation["progress_percent"])))

    assert [item[0] for item in observed] == list(EVALUATION_STAGES)
    percents = [item[1] for item in observed]
    assert percents == sorted(percents)
    assert len(set(percents)) == len(percents)
    assert percents[-1] == 100.0

    with pytest.raises(ValueError):
        update_evaluation_stage(database_url, evaluation_run_id, "almost_done")


# ---------------------------------------------------------------------------
# B 组：Worker 编排
# ---------------------------------------------------------------------------


def test_evaluation_dataset_allowlist_matches_the_dataset_files() -> None:
    """白名单里的 dataset_id/version 是从数据集文件抄下来的冻结副本，两边必须一致。

    入队时不读文件是为了让排队不依赖文件系统；代价是文件改了白名单不会自己发现，
    那份「运行记录说测的是 2.0.0，实际跑的是 2.1.0」的报告没有任何地方会报错。
    这条测试就是那个报错的地方。
    """

    assert EVALUATION_DATASETS
    for slug, dataset in EVALUATION_DATASETS.items():
        assert dataset.slug == slug
        path = DATASETS_PATH / dataset.filename
        assert path.is_file(), f"数据集文件不存在：{path}"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["dataset_id"] == dataset.dataset_id
        assert payload["version"] == dataset.dataset_version


def test_normalized_dsn_ignores_password_and_query_parameters() -> None:
    """密码与连接参数不改变「指向哪个库」，隔离判定不能被它们骗过去。"""

    assert normalized_dsn("postgresql://rag@postgres:5432/rag_enterprise") == (
        "postgres",
        "5432",
        "rag_enterprise",
    )
    assert normalized_dsn(
        "postgresql://rag:secret@postgres:5432/rag_enterprise?sslmode=disable"
    ) == ("postgres", "5432", "rag_enterprise")


def test_normalized_dsn_falls_back_to_the_default_port() -> None:
    """省略端口的连接串和显式写 5432 的指的是同一个库。"""

    assert normalized_dsn("postgresql://rag@postgres/rag_enterprise") == (
        "postgres",
        "5432",
        "rag_enterprise",
    )


def test_evaluation_database_must_be_configured() -> None:
    """没配评测库就明确 503，而不是退回去用业务库。"""

    for value in (None, "   "):
        settings = _worker_settings(
            database_url=BUSINESS_DATABASE_URL, evaluation_database_url=value
        )
        with pytest.raises(AppError) as excinfo:
            require_isolated_evaluation_database(settings)
        assert excinfo.value.code == "EVALUATION_DATABASE_NOT_CONFIGURED"
        assert excinfo.value.status_code == 503


def test_evaluation_database_must_not_be_the_business_database() -> None:
    """评测会在库里建临时语料再删掉，指到业务库就是一次带删除的写入。

    两个连接串字面量完全不同（密码、端口、参数都不一样），指的却是同一个库——
    只比字符串的话这条守卫会被静默绕过。
    """

    settings = _worker_settings(
        database_url="postgresql://rag@postgres:5432/rag_enterprise",
        evaluation_database_url="postgresql://rag:secret@postgres/rag_enterprise?sslmode=disable",
    )

    with pytest.raises(AppError) as excinfo:
        require_isolated_evaluation_database(settings)

    assert excinfo.value.code == "EVALUATION_DATABASE_NOT_ISOLATED"
    assert excinfo.value.status_code == 500


def test_an_isolated_evaluation_database_is_accepted() -> None:
    settings = _worker_settings(
        database_url=BUSINESS_DATABASE_URL, evaluation_database_url=EVALUATION_DATABASE_URL
    )

    assert require_isolated_evaluation_database(settings) == EVALUATION_DATABASE_URL


@requires_database
def test_run_evaluation_once_uses_the_frozen_candidate_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """评测跑的是候选版本冻结下来的切片配置和隔离评测库，不是 Worker 进程的当前配置。

    Worker 用自己的 chunk_size 跑出来的报告，指纹一定对不上候选版本；用户看到的却是
    「评测通过了但验证不过」，真正的原因在页面上完全看不出来。
    """

    business_url = _database_url()
    _reset(business_url)
    document_version_id = _add_document(business_url, "first")
    # 320/40 必须与 Settings 默认的 700/100 不同：相等的话，「用了冻结配置」与
    # 「用了进程默认配置」会得出同一个结果，这条断言就什么也证明不了。
    index_version_id = _candidate(
        business_url, "first", document_version_id, chunk_size=320, chunk_overlap=40
    )
    settings = _worker_settings(
        database_url=business_url,
        evaluation_database_url=EVALUATION_DATABASE_URL,
        reranker_model=RERANKER_MODEL,
    )
    assert (settings.chunk_size, settings.chunk_overlap) != (320, 40)

    created = create_evaluation_run(business_url, index_version_id, "corpus_v2", "admin")
    operation_id = str(created["operation_id"])
    run = claim_evaluation_run(business_url, "worker-a")
    assert run is not None

    report = _passing_report(_fingerprint_of(business_url, index_version_id))
    captured: dict[str, Any] = {}

    def fake_run_corpus_baseline(
        dataset: Any,
        contents: Any,
        commit: str,
        chunk_size: int,
        chunk_overlap: int,
        baseline: Any = None,
        database_url: str | None = None,
        embedder: Any = None,
        reranker: Any = None,
        *,
        retrieval_mode: str = "vector",
        official: bool = False,
        on_stage: Any = None,
        **_rest: Any,
    ) -> Any:
        captured["dataset_id"] = dataset.dataset_id
        captured["commit"] = commit
        captured["chunk_size"] = chunk_size
        captured["chunk_overlap"] = chunk_overlap
        captured["database_url"] = database_url
        captured["baseline"] = baseline
        captured["official"] = official
        captured["retrieval_mode"] = retrieval_mode
        assert on_stage is not None
        on_stage("build_corpus")
        # 在替身内部读一次：on_stage 必须当场把阶段写进 operations，
        # 而不是等 run_evaluation_once 返回之后再补上——长任务的进度只有当场写才有用。
        captured["stage_during_run"] = _operation_row(business_url, operation_id)["current_stage"]
        return report

    _patch_evaluation_database(monkeypatch)
    monkeypatch.setattr(
        "backend.app.index_evaluation_worker.run_corpus_baseline", fake_run_corpus_baseline
    )

    result = run_evaluation_once(settings, run, _FakeEmbedder(), _FakeReranker())

    assert result is report
    assert (captured["chunk_size"], captured["chunk_overlap"]) == (320, 40)
    assert captured["database_url"] == EVALUATION_DATABASE_URL
    assert captured["official"] is True
    assert captured["dataset_id"] == "rag-enterprise-corpus"
    assert captured["commit"] == str(run["commit_sha"])
    # 没有 active 版本的正式报告，基线为空。
    assert captured["baseline"] is None
    assert captured["stage_during_run"] == "build_corpus"
    # 收口前最后一步阶段由 run_evaluation_once 自己推进。
    assert _operation_row(business_url, operation_id)["current_stage"] == "persist_report"


@requires_database
def test_run_evaluation_once_rejects_a_drifted_embedding_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行时向量模型与候选版本不一致时提前失败，不跑完再产出一份对不上的报告。"""

    business_url = _database_url()
    _reset(business_url)
    document_version_id = _add_document(business_url, "first")
    index_version_id = _candidate(business_url, "first", document_version_id)
    settings = _worker_settings(
        database_url=business_url,
        evaluation_database_url=EVALUATION_DATABASE_URL,
        reranker_model=RERANKER_MODEL,
    )
    create_evaluation_run(business_url, index_version_id, "corpus_v2", "admin")
    run = claim_evaluation_run(business_url, "worker-a")
    assert run is not None
    _patch_evaluation_database(monkeypatch)
    monkeypatch.setattr(
        "backend.app.index_evaluation_worker.run_corpus_baseline", _forbidden_run_corpus_baseline
    )

    with pytest.raises(AppError) as excinfo:
        run_evaluation_once(settings, run, _DriftedEmbedder(), _FakeReranker())

    assert excinfo.value.code == "EVALUATION_EMBEDDING_MISMATCH"
    assert excinfo.value.status_code == 500
    assert excinfo.value.details == {
        "expected": "test/embedding",
        "actual": "test/other-embedding",
    }


@requires_database
def test_run_evaluation_once_rejects_a_drifted_component_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """组件清单进指纹计算，精排模型换代后跑出来的报告一定对不上候选版本。"""

    business_url = _database_url()
    _reset(business_url)
    document_version_id = _add_document(business_url, "first")
    index_version_id = _candidate(business_url, "first", document_version_id)
    settings = _worker_settings(
        database_url=business_url,
        evaluation_database_url=EVALUATION_DATABASE_URL,
        reranker_model="test/reranker-v2",
    )
    create_evaluation_run(business_url, index_version_id, "corpus_v2", "admin")
    run = claim_evaluation_run(business_url, "worker-a")
    assert run is not None
    _patch_evaluation_database(monkeypatch)
    monkeypatch.setattr(
        "backend.app.index_evaluation_worker.run_corpus_baseline", _forbidden_run_corpus_baseline
    )

    with pytest.raises(AppError) as excinfo:
        run_evaluation_once(settings, run, _FakeEmbedder(), _FakeReranker())

    assert excinfo.value.code == "EVALUATION_COMPONENTS_MISMATCH"
    assert excinfo.value.status_code == 500
    assert excinfo.value.details == {"fields": ["reranker_model"]}


@requires_database
def test_run_evaluation_once_rejects_a_report_with_a_foreign_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最后一道防线：报告必须能证明自己测的就是这个候选版本。

    前面的逐项校验漏掉任何一项都会在这里露出来，而不是等三层验证时以
    「验证未通过」的形式甩给用户。
    """

    business_url = _database_url()
    _reset(business_url)
    document_version_id = _add_document(business_url, "first")
    index_version_id = _candidate(business_url, "first", document_version_id)
    settings = _worker_settings(
        database_url=business_url,
        evaluation_database_url=EVALUATION_DATABASE_URL,
        reranker_model=RERANKER_MODEL,
    )
    create_evaluation_run(business_url, index_version_id, "corpus_v2", "admin")
    run = claim_evaluation_run(business_url, "worker-a")
    assert run is not None
    foreign = _passing_report("b" * 64)
    _patch_evaluation_database(monkeypatch)
    monkeypatch.setattr(
        "backend.app.index_evaluation_worker.run_corpus_baseline",
        lambda *_args, **_kwargs: foreign,
    )

    with pytest.raises(AppError) as excinfo:
        run_evaluation_once(settings, run, _FakeEmbedder(), _FakeReranker())

    assert excinfo.value.code == "EVALUATION_FINGERPRINT_MISMATCH"
    assert excinfo.value.status_code == 500
    assert excinfo.value.details["actual"] == "b" * 12
