"""V5 操作、同步资源与进度聚合。

批次状态只由持久化的单资源状态计算，禁止由调用链是否返回来猜测完成。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row, tuple_row

from .index_versions import finalize_building_version


TERMINAL_RESOURCE_STATUSES = frozenset(
    {"succeeded", "unchanged", "skipped", "deleted", "failed", "dead_letter", "cancelled"}
)


def create_operation(
    connection: psycopg.Connection[Any], *, operation_type: str, knowledge_base_id: str,
    idempotency_key: str, data_source_id: str | None = None,
    document_id: str | None = None, document_version_id: str | None = None,
    progress_mode: str = "resources",
) -> str:
    operation_id = f"op_{uuid4().hex[:20]}"
    connection.execute(
        """INSERT INTO operations
           (operation_id, operation_type, knowledge_base_id, data_source_id, document_id,
            document_version_id, idempotency_key, progress_mode)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (operation_id, operation_type, knowledge_base_id, data_source_id, document_id,
         document_version_id, idempotency_key, progress_mode),
    )
    return operation_id


_UPSERT_SYNC_RESOURCE_SQL = """INSERT INTO sync_resource_runs
               (sync_resource_run_id, sync_run_id, external_resource_id, operation, status,
                current_stage, document_id, document_version_id, error_code, error_message,
                started_at, finished_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       CASE WHEN %s <> 'discovered' THEN now() END,
                       CASE WHEN %s = ANY(%s) THEN now() END)
               ON CONFLICT (sync_run_id, external_resource_id) DO UPDATE SET
                 operation=EXCLUDED.operation, status=EXCLUDED.status,
                 current_stage=EXCLUDED.current_stage,
                 document_id=COALESCE(EXCLUDED.document_id, sync_resource_runs.document_id),
                 document_version_id=COALESCE(EXCLUDED.document_version_id,
                                              sync_resource_runs.document_version_id),
                 error_code=EXCLUDED.error_code, error_message=EXCLUDED.error_message,
                 started_at=COALESCE(sync_resource_runs.started_at, EXCLUDED.started_at),
                 finished_at=EXCLUDED.finished_at, updated_at=now()"""


def _sync_resource_params(
    sync_run_id: str, external_resource_id: str, operation: str, *, status: str, stage: str,
    document_id: str | None = None, document_version_id: str | None = None,
    error_code: str | None = None, error_message: str | None = None,
) -> tuple[Any, ...]:
    return (
        f"srr_{uuid4().hex[:20]}", sync_run_id, external_resource_id, operation, status,
        stage, document_id, document_version_id, error_code, error_message, status, status,
        list(TERMINAL_RESOURCE_STATUSES),
    )


def upsert_sync_resources(
    database_url: str, rows: list[tuple[Any, ...]]
) -> None:
    """一次事务写入多条单资源状态。

    差异计算阶段要为**每个**远端对象记一行，其中绝大多数是「无变化」。逐条调用
    ``upsert_sync_resource`` 意味着每个对象一次 TCP 握手加一次事务提交——一个千份文档、
    零变化的数据源要开一千个连接，而这个开销不会报错，只是同步越来越慢。

    用 ``_sync_resource_params`` 构造每行参数，SQL 与单条版本完全共用，避免两份写法漂移。
    """

    if not rows:
        return
    with psycopg.connect(database_url) as connection, connection.transaction():
        with connection.cursor() as cursor:
            cursor.executemany(_UPSERT_SYNC_RESOURCE_SQL, rows)


def upsert_sync_resource(
    database_url: str, sync_run_id: str, external_resource_id: str, operation: str,
    *, status: str = "discovered", stage: str = "discover", document_id: str | None = None,
    document_version_id: str | None = None, error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    upsert_sync_resources(
        database_url,
        [
            _sync_resource_params(
                sync_run_id, external_resource_id, operation, status=status, stage=stage,
                document_id=document_id, document_version_id=document_version_id,
                error_code=error_code, error_message=error_message,
            )
        ],
    )


def update_sync_resource_for_job(
    database_url: str, sync_run_id: str, document_version_id: str,
    *, succeeded: bool, terminal: bool, failure_reason: str | None = None,
) -> None:
    status = "succeeded" if succeeded else ("dead_letter" if terminal else "building")
    stage = "complete" if succeeded else ("dead_letter" if terminal else "retry_wait")
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE sync_resource_runs SET status=%s, current_stage=%s,
                      attempt_count=attempt_count+CASE WHEN %s THEN 0 ELSE 1 END,
                      error_code=CASE WHEN %s THEN NULL ELSE 'INDEX_BUILD_FAILED' END,
                      error_message=%s,
                      finished_at=CASE WHEN %s OR %s THEN now() ELSE NULL END,
                      updated_at=now()
               WHERE sync_run_id=%s AND document_version_id=%s AND status <> 'cancelled'""",
            (status, stage, succeeded, succeeded, failure_reason, succeeded, terminal,
             sync_run_id, document_version_id),
        )
    aggregate_sync_run(database_url, sync_run_id)


def fail_sync_operation(
    database_url: str, sync_run_id: str, *, status: str, error_code: str, error_message: str
) -> None:
    """把同步失败收口到 operations 投影。

    失败路径此前只写 data_sources 与 sync_runs，operations 行原样留在 queued/running——
    前端任务列表因此会显示一个永远排队、永远不报错的任务，而库里 sync_runs 明明已经
    是 failed。这是 sync_runs 与 operations 两套状态机在失败路径上分叉的直接后果。

    aggregate_sync_run 只在成功路径上被调用，收不了这个口。
    """

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE operations SET status=%s, current_stage='failed',
                      error_code=%s, error_message=%s,
                      started_at=COALESCE(started_at, now()),
                      finished_at=now(), updated_at=now()
               WHERE operation_id=(SELECT operation_id FROM sync_runs WHERE sync_run_id=%s)""",
            (status, error_code, error_message[:1000], sync_run_id),
        )


# 一次同步聚合的结论要写进三张表，而三者的 status 取值域并不相同——交集只有
# aborted / failed / queued / succeeded 四个。直接把 sync_runs 的值塞给另外两张表会违反
# 它们的 CHECK，整个聚合事务回滚，同步任务卡在 queued 反复重试，而表面症状是
# 「该数据源已有同步任务在进行中」，离真正的原因隔着两层。这个坑本轮已经咬过四次。
#
#   sync_runs      indexing / succeeded / partial_failed
#   operations     running  / succeeded / partial_failed
#   data_sources   running  / succeeded / failed
#
# 映射写在这里而不是内联，是为了让它可被 test_sync_status_mapping_covers_every_domain
# 逐值校验：新增一个 sync_runs 状态而不在这里映射，那条测试会红。
SYNC_STATUS_TO_OPERATION = {"indexing": "running"}
SYNC_STATUS_TO_DATA_SOURCE = {"indexing": "running", "partial_failed": "failed"}


def map_sync_status(status: str, *, target: str) -> str:
    """把同步批次状态翻译成目标表允许的取值。"""

    table = {
        "operations": SYNC_STATUS_TO_OPERATION,
        "data_sources": SYNC_STATUS_TO_DATA_SOURCE,
    }[target]
    return table.get(status, status)


def aggregate_sync_run(database_url: str, sync_run_id: str) -> None:
    """按单资源真实状态收口同步批次，并在完全结束后提交 cursor。"""
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        run = connection.execute(
            "SELECT operation_id, data_source_id, discovered_cursor FROM sync_runs WHERE sync_run_id=%s FOR UPDATE",
            (sync_run_id,),
        ).fetchone()
        if run is None:
            return
        # 取消/熔断是人为确定的终态。迟到的 Worker 只能完成自身清理，不能把批次改回成功。
        terminal = connection.execute(
            "SELECT status FROM sync_runs WHERE sync_run_id=%s", (sync_run_id,)
        ).fetchone()
        if terminal and str(terminal["status"]) in {"aborted", "failed"}:
            return
        counts = connection.execute(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE status = ANY(%s)) AS completed,
                      count(*) FILTER (WHERE status NOT IN ('discovered','succeeded','unchanged','skipped','deleted','failed','dead_letter','cancelled')) AS processing,
                      count(*) FILTER (WHERE status IN ('failed','dead_letter')) AS failed,
                      count(*) FILTER (WHERE status='dead_letter') AS dead_letter
               FROM sync_resource_runs WHERE sync_run_id=%s""",
            (list(TERMINAL_RESOURCE_STATUSES), sync_run_id),
        ).fetchone()
        total, completed = int(counts["total"]), int(counts["completed"])
        processing, failed = int(counts["processing"]), int(counts["failed"])
        done = total == completed
        status = ("partial_failed" if failed else "succeeded") if done else "indexing"
        stage = "complete_with_failures" if done and failed else ("complete" if done else "build")
        connection.execute(
            """UPDATE sync_runs SET status=%s, stage=%s, total_count=%s,
                      completed_count=%s, processing_count=%s, failed_count=%s,
                      dead_letter_count=%s,
                      committed_cursor=CASE WHEN %s AND %s=0 THEN discovered_cursor ELSE committed_cursor END,
                      next_cursor=CASE WHEN %s AND %s=0 THEN discovered_cursor ELSE next_cursor END,
                      finished_at=CASE WHEN %s THEN now() ELSE NULL END, updated_at=now()
               WHERE sync_run_id=%s""",
            (status, stage, total, completed, processing, failed, int(counts["dead_letter"]),
             done, failed, done, failed, done, sync_run_id),
        )
        percent = 100 if done else (round(completed * 100 / total, 2) if total else None)
        operation_status = map_sync_status(status, target="operations")
        source_status = map_sync_status(status, target="data_sources")
        connection.execute(
            """UPDATE operations SET status=%s, current_stage=%s, total_count=%s,
                      completed_count=%s, processing_count=%s, failed_count=%s,
                      progress_percent=%s, started_at=COALESCE(started_at, now()),
                      finished_at=CASE WHEN %s THEN now() ELSE NULL END, updated_at=now()
               WHERE operation_id=%s""",
            (operation_status, stage, total, completed, processing, failed, percent, done,
             run["operation_id"]),
        )
        connection.execute(
            """UPDATE data_sources SET last_sync_status=%s,
                      sync_failure_reason=CASE WHEN %s THEN %s ELSE NULL END,
                      last_sync_at=CASE WHEN %s THEN now() ELSE last_sync_at END, updated_at=now()
               WHERE data_source_id=%s""",
            (source_status, bool(failed), f"{failed} 个资源处理失败", done, run["data_source_id"]),
        )


def list_sync_resources(
    database_url: str, sync_run_id: str, data_source_id: str | None = None
) -> list[dict[str, object]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """SELECT r.* FROM sync_resource_runs r JOIN sync_runs s USING (sync_run_id)
               WHERE r.sync_run_id=%s AND (%s IS NULL OR s.data_source_id=%s)
               ORDER BY r.created_at, r.external_resource_id""",
            (sync_run_id, data_source_id, data_source_id),
        ).fetchall()
    return [dict(row) for row in rows]


def list_operations(database_url: str, knowledge_base_id: str, limit: int = 50) -> list[dict[str, object]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """SELECT * FROM operations WHERE knowledge_base_id=%s
               ORDER BY created_at DESC LIMIT %s""",
            (knowledge_base_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def cancel_sync_run(database_url: str, data_source_id: str, sync_run_id: str) -> bool:
    """取消尚未完成的同步；已在供应商侧执行的读取不会回滚，落库任务停止激活。"""
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        run = connection.execute(
            """SELECT operation_id, status FROM sync_runs
               WHERE sync_run_id=%s AND data_source_id=%s FOR UPDATE""",
            (sync_run_id, data_source_id),
        ).fetchone()
        if run is None:
            return False
        if str(run["status"]) in {"succeeded", "partial_failed", "failed", "aborted"}:
            return True
        connection.execute(
            """UPDATE index_jobs SET status='cancelled', finished_at=now(), updated_at=now()
               WHERE sync_run_id=%s AND status IN ('queued','running')""",
            (sync_run_id,),
        )
        connection.execute(
            """UPDATE sync_resource_runs SET status='cancelled', current_stage='cancelled',
                      finished_at=now(), updated_at=now()
               WHERE sync_run_id=%s AND status NOT IN
                 ('succeeded','unchanged','skipped','deleted','failed','dead_letter','cancelled')""",
            (sync_run_id,),
        )
        connection.execute(
            """UPDATE sync_runs SET status='aborted', stage='cancelled', finished_at=now(),
                      failure_reason='管理员取消同步', updated_at=now() WHERE sync_run_id=%s""",
            (sync_run_id,),
        )
        connection.execute(
            """UPDATE operations SET status='cancelled', current_stage='cancelled',
                      finished_at=now(), updated_at=now() WHERE operation_id=%s""",
            (run["operation_id"],),
        )
        connection.execute(
            """UPDATE data_sources SET last_sync_status='aborted',
                      sync_failure_reason='管理员取消同步', updated_at=now()
               WHERE data_source_id=%s""",
            (data_source_id,),
        )
    return True


TERMINAL_BUILD_STATUSES = frozenset(
    {"succeeded", "partial_failed", "failed", "cancelled"}
)


def ensure_index_build(
    connection: psycopg.Connection[Any], *, knowledge_base_id: str,
    index_version_id: str, build_type: str = "full_rebuild",
) -> str:
    """返回该索引版本进行中的构建；没有就新开一次尝试。

    进行中的构建直接复用——重复调用是续跑，不是重试。只有当上一次已经进入终态
    （成功、失败或取消）时才递增 ``attempt_no`` 新建一次，失败现场因此完整保留下来，
    「查看构建日志 / 重试构建」这条恢复路径才有东西可看。

    ``operations`` 与 attempt 保持一一对应：用户看到的「任务」是这一次尝试，不是
    整个版本。因此 idempotency_key 必须带上 attempt_no，否则第二次尝试会撞上第一次
    留下的唯一键。
    """

    # 显式指定 row_factory，不假设调用方连接是什么：本函数同时被 dict_row 连接
    # （enqueue_rebuild）和普通连接调用，而 fetchone()[0] 在 dict_row 上是 KeyError。
    # 这正是 CLAUDE.md 第四条记的那个坑——它只在生产路径上炸，测试用普通连接照样绿。
    with connection.cursor(row_factory=tuple_row) as cursor:
        existing = cursor.execute(
            """SELECT index_build_id FROM index_builds
               WHERE index_version_id=%s AND status <> ALL(%s)
               ORDER BY attempt_no DESC LIMIT 1""",
            (index_version_id, list(TERMINAL_BUILD_STATUSES)),
        ).fetchone()
        if existing:
            return str(existing[0])
        previous = cursor.execute(
            "SELECT COALESCE(max(attempt_no), 0) FROM index_builds WHERE index_version_id=%s",
            (index_version_id,),
        ).fetchone()
    attempt_no = int(previous[0]) + 1
    operation_id = create_operation(
        connection, operation_type="index_build", knowledge_base_id=knowledge_base_id,
        idempotency_key=f"index-build:{index_version_id}:{attempt_no}",
        progress_mode="documents",
    )
    index_build_id = f"ib_{uuid4().hex[:20]}"
    connection.execute(
        """INSERT INTO index_builds
           (index_build_id, operation_id, index_version_id, build_type, attempt_no)
           VALUES (%s,%s,%s,%s,%s)""",
        (index_build_id, operation_id, index_version_id, build_type, attempt_no),
    )
    return index_build_id


def upsert_document_index_state(
    connection: psycopg.Connection[Any], *, index_build_id: str, index_version_id: str,
    document_id: str, document_version_id: str, status: str = "pending",
) -> None:
    """写入单文档索引状态。

    **三条 lane 目前恒等，这是如实反映，不是遗漏。** 索引写入是一个原子步骤：向量、
    关键词与元数据在同一事务里一起落库，没有任何时刻它们的状态会不同。分别建模要等
    build 阶段真正拆分之后——在那之前，让三列取不同值只会凭空造出一个不存在的中间态。

    页面也已经不再把它们画成三个独立阶段（此前三列并排显示为「向量索引 / 关键词索引 /
    元数据索引」，看起来像三段流程，实际永远同亮同灭）。
    """

    lane = "ready" if status == "ready" else ("failed" if status == "failed" else "pending")
    connection.execute(
        """INSERT INTO document_index_states
           (index_build_id, index_version_id, document_id, document_version_id,
            vector_status, keyword_status, metadata_status, overall_status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (index_build_id, document_id) DO NOTHING""",
        (index_build_id, index_version_id, document_id, document_version_id,
         lane, lane, lane, status),
    )


def update_index_build_for_job(
    database_url: str, rebuild_batch_id: str, document_version_id: str,
    *, succeeded: bool, terminal: bool, failure_reason: str | None = None,
) -> None:
    with psycopg.connect(database_url) as connection, connection.transaction():
        state = "ready" if succeeded else ("failed" if terminal else "building")
        lane = "ready" if succeeded else ("failed" if terminal else "building")
        connection.execute(
            """UPDATE document_index_states dis SET overall_status=%s,
                      vector_status=%s, keyword_status=%s, metadata_status=%s,
                      chunk_count=(SELECT count(*) FROM chunks c
                                   WHERE c.index_version_id=dis.index_version_id
                                     AND c.document_version_id=dis.document_version_id),
                      failure_stage=CASE WHEN %s THEN NULL ELSE 'build' END,
                      failure_code=CASE WHEN %s THEN NULL ELSE 'INDEX_BUILD_FAILED' END,
                      failure_reason=%s, updated_at=now()
               FROM index_builds ib JOIN index_versions iv USING (index_version_id)
               WHERE dis.index_build_id=ib.index_build_id
                 AND iv.rebuild_batch_id=%s AND dis.document_version_id=%s
                 -- 一个版本可以有多次构建尝试，只推进最新那次；否则旧 attempt 的
                 -- 历史记录会被这一次的结果改写，失败现场就没了。
                 AND ib.attempt_no=(SELECT max(attempt_no) FROM index_builds
                                    WHERE index_version_id=ib.index_version_id)""",
            (state, lane, lane, lane, succeeded, succeeded, failure_reason,
             rebuild_batch_id, document_version_id),
        )
    aggregate_index_build(database_url, rebuild_batch_id)


def aggregate_index_build(database_url: str, rebuild_batch_id: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        build = connection.execute(
            """SELECT ib.index_build_id, ib.operation_id, ib.status FROM index_builds ib
               JOIN index_versions iv USING (index_version_id)
               WHERE iv.rebuild_batch_id=%s
               ORDER BY ib.attempt_no DESC LIMIT 1 FOR UPDATE""",
            (rebuild_batch_id,),
        ).fetchone()
        if build is None:
            return
        # Cancel 是操作者确定的终态。迟到的 Worker 可以结束自身，但不能把已取消的
        # Build / Operation 再聚合成 succeeded。
        if str(build["status"]) == "cancelled":
            return
        counts = connection.execute(
            """SELECT count(*) total,
                      count(*) FILTER (WHERE overall_status='pending') queued,
                      count(*) FILTER (WHERE overall_status='building') processing,
                      count(*) FILTER (WHERE overall_status='ready') succeeded,
                      count(*) FILTER (WHERE overall_status='failed') failed
               FROM document_index_states WHERE index_build_id=%s""",
            (build["index_build_id"],),
        ).fetchone()
        total = int(counts["total"])
        finished = int(counts["succeeded"]) + int(counts["failed"])
        done = total == finished
        # Build 只描述索引产物是否构建完成。验证与激活属于 Index Version 的后续动作，
        # 不能让 Build 停在 ready 等它们结束，更不能把 Version=validating 解释成构建失败。
        status = ("partial_failed" if counts["failed"] else "succeeded") if done else "building"
        connection.execute(
            """UPDATE index_builds SET status=%s, total_documents=%s, queued_documents=%s,
                      processing_documents=%s, succeeded_documents=%s, failed_documents=%s,
                      started_at=COALESCE(started_at,now()),
                      finished_at=CASE WHEN %s THEN now() ELSE NULL END, updated_at=now()
               WHERE index_build_id=%s""",
            (status, total, counts["queued"], counts["processing"], counts["succeeded"],
             counts["failed"], done, build["index_build_id"]),
        )
        percent = 100 if done else (round(finished * 100 / total, 2) if total else None)
        operation_status = (
            "partial_failed" if done and counts["failed"] else ("succeeded" if done else "running")
        )
        connection.execute(
            """UPDATE operations SET status=%s, current_stage=%s, progress_percent=%s,
                      total_count=%s, completed_count=%s, processing_count=%s, failed_count=%s,
                      started_at=COALESCE(started_at,now()),
                      finished_at=CASE WHEN %s THEN now() ELSE NULL END, updated_at=now()
               WHERE operation_id=%s""",
            (operation_status, "complete" if done else "build", percent, total, finished,
             counts["processing"], counts["failed"], done, build["operation_id"]),
        )
    if done:
        # Build 与 Index Version 共用同一个终态判断，避免页面 100% 而版本仍停在 building。
        with psycopg.connect(database_url) as connection:
            version = connection.execute(
                "SELECT index_version_id FROM index_versions WHERE rebuild_batch_id=%s",
                (rebuild_batch_id,),
            ).fetchone()
        if version:
            version_status = finalize_building_version(database_url, str(version[0]))
            # 文档任务全成功但覆盖校验失败，属于 Build 的一致性失败；此时才把原本的
            # succeeded 修正为 failed。已有失败文档时 Build / Operation 已准确记录为
            # partial_failed，不能再抹掉“部分成功”的执行事实。其余后续 Version 状态
            # （validating / validation_failed / ready / active）也不得反写 Build。
            if version_status == "build_failed" and int(counts["failed"]) == 0:
                with psycopg.connect(database_url) as connection, connection.transaction():
                    connection.execute(
                        """UPDATE index_builds SET status='failed', updated_at=now()
                           WHERE index_build_id=%s""",
                        (build["index_build_id"],),
                    )
                    connection.execute(
                        """UPDATE operations SET status='failed', current_stage='build',
                                  error_code='INDEX_BUILD_INCOMPLETE',
                                  error_message='索引构建覆盖不完整。', updated_at=now()
                           WHERE operation_id=%s""",
                        (build["operation_id"],),
                    )


def update_index_stage(
    database_url: str, rebuild_batch_id: str, document_version_id: str, stage: str
) -> None:
    """按实际执行点更新 Vector/Keyword/Metadata 三路状态。"""
    allowed = {"parsing", "chunking", "vector", "keyword", "metadata", "validating"}
    if stage not in allowed:
        raise ValueError(f"unsupported index stage: {stage}")
    assignments = {
        "parsing": ("building", "pending", "pending", "building"),
        "chunking": ("building", "pending", "pending", "building"),
        "vector": ("building", "pending", "pending", "building"),
        "keyword": ("ready", "building", "pending", "building"),
        "metadata": ("ready", "ready", "building", "building"),
        "validating": ("ready", "ready", "ready", "validating"),
    }[stage]
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE document_index_states dis SET vector_status=%s, keyword_status=%s,
                      metadata_status=%s, overall_status=%s, updated_at=now()
               FROM index_builds ib JOIN index_versions iv USING (index_version_id)
               WHERE dis.index_build_id=ib.index_build_id AND iv.rebuild_batch_id=%s
                 AND dis.document_version_id=%s
                 AND ib.attempt_no=(SELECT max(attempt_no) FROM index_builds
                                    WHERE index_version_id=ib.index_version_id)""",
            (*assignments, rebuild_batch_id, document_version_id),
        )


def list_index_builds(database_url: str, knowledge_base_id: str) -> list[dict[str, object]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """SELECT ib.*, o.progress_percent, o.current_stage, iv.knowledge_base_id
               FROM index_builds ib JOIN operations o USING (operation_id)
               JOIN index_versions iv USING (index_version_id)
               WHERE iv.knowledge_base_id=%s ORDER BY ib.created_at DESC""",
            (knowledge_base_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_document_index_states(
    database_url: str, knowledge_base_id: str, index_build_id: str
) -> list[dict[str, object]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """SELECT dis.*, d.filename FROM document_index_states dis
               JOIN documents d ON d.document_id=dis.document_id
               JOIN index_versions iv ON iv.index_version_id=dis.index_version_id
               WHERE dis.index_build_id=%s AND iv.knowledge_base_id=%s
               ORDER BY d.filename""",
            (index_build_id, knowledge_base_id),
        ).fetchall()
    return [dict(row) for row in rows]
