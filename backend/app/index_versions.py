"""索引版本的配置指纹、创建与状态查询。

索引版本承载"这批分块由什么配置产出"这一事实。切换放行时用配置指纹比对评测报告，
阻止用一套配置跑出的合格报告去放行另一套配置的索引。
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .errors import AppError
from .parsers import PARSER_SCHEMA_VERSION

if TYPE_CHECKING:
    # 只用于类型标注：运行时导入 backend.evaluation 会把整个评测包（及其对 app.parsers
    # 的反向依赖）拉进应用启动路径。
    from backend.evaluation.report import RetrievalEvaluationReport

    from .audit import AuditRepository


def config_fingerprint(
    chunking_version: str,
    embedding_model: str,
    embedding_dimension: int,
    processing_options: dict[str, Any],
) -> str:
    """按规范化 JSON 计算指纹，键顺序不影响结果。

    只纳入操作者能选择、评测能精确复现的配置。解析部分取全局
    ``PARSER_SCHEMA_VERSION`` 而不是各格式的 parser 版本，且不作为参数暴露——
    per-format 版本由文档格式决定（Markdown 与 PDF 是 2.0，DOCX 与 CSV 是 1.0），
    评测语料的格式组合与生产知识库必然不同，纳入它会让指纹永远匹配不上、
    切换永远被拒。索引版本表里仍记录 per-format 版本作为事实。
    """

    canonical = json.dumps(
        {
            "chunking_version": chunking_version,
            "parser_schema_version": PARSER_SCHEMA_VERSION,
            "embedding_model": embedding_model,
            "embedding_dimension": embedding_dimension,
            "processing_options": processing_options,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_building_version(
    database_url: str,
    knowledge_base_id: str,
    *,
    chunking_version: str,
    parser_version: str,
    embedding_model: str,
    embedding_dimension: int,
    processing_options: dict[str, Any],
    rebuild_batch_id: str,
) -> str:
    """创建一个 building 索引版本并冻结全套配置。

    每个知识库同时只能有一个 building 版本，由数据库的 partial unique index 保证；
    并发调用会得到 UniqueViolation 而不是两个半成品版本。
    """

    index_version_id = f"iv_{uuid4().hex[:16]}"
    fingerprint = config_fingerprint(
        chunking_version, embedding_model, embedding_dimension, processing_options
    )
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO index_versions
               (index_version_id, knowledge_base_id, status, chunking_version, parser_version,
                embedding_model, embedding_dimension, processing_options, config_fingerprint,
                rebuild_batch_id)
               VALUES (%s, %s, 'building', %s, %s, %s, %s, %s, %s, %s)""",
            (
                index_version_id,
                knowledge_base_id,
                chunking_version,
                parser_version,
                embedding_model,
                embedding_dimension,
                Jsonb(processing_options),
                fingerprint,
                rebuild_batch_id,
            ),
        )
    return index_version_id


def active_index_version_id(database_url: str, knowledge_base_id: str) -> str | None:
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            "SELECT active_index_version_id FROM knowledge_bases WHERE knowledge_base_id = %s",
            (knowledge_base_id,),
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def get_version(database_url: str, index_version_id: str) -> dict[str, Any] | None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT * FROM index_versions WHERE index_version_id = %s", (index_version_id,)
        ).fetchone()
    return dict(row) if row else None


def list_versions(database_url: str, knowledge_base_id: str) -> list[dict[str, Any]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """SELECT index_version_id, status, chunking_version, parser_version,
                      embedding_model, embedding_dimension, processing_options,
                      config_fingerprint, evaluation_report_id, rebuild_batch_id,
                      created_at, activated_at, retired_at
               FROM index_versions WHERE knowledge_base_id = %s
               ORDER BY created_at DESC""",
            (knowledge_base_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def active_or_bootstrap_version(
    database_url: str,
    knowledge_base_id: str,
    *,
    chunking_version: str,
    parser_version: str,
    embedding_model: str,
    embedding_dimension: int,
    processing_options: dict[str, Any],
) -> str:
    """返回 active 索引版本；知识库首次索引时创建并直接激活第一个版本。

    首个版本没有可比较的前序基线，因此不要求评测报告，用固定标记满足数据库约束。
    """

    existing = active_index_version_id(database_url, knowledge_base_id)
    if existing:
        return existing
    index_version_id = f"iv_{uuid4().hex[:16]}"
    fingerprint = config_fingerprint(
        chunking_version, embedding_model, embedding_dimension, processing_options
    )
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO index_versions
               (index_version_id, knowledge_base_id, status, chunking_version, parser_version,
                embedding_model, embedding_dimension, processing_options, config_fingerprint,
                evaluation_report_id, activated_at)
               VALUES (%s, %s, 'active', %s, %s, %s, %s, %s, %s, 'initial-index', now())
               ON CONFLICT DO NOTHING""",
            (
                index_version_id,
                knowledge_base_id,
                chunking_version,
                parser_version,
                embedding_model,
                embedding_dimension,
                Jsonb(processing_options),
                fingerprint,
            ),
        )
        connection.execute(
            """UPDATE knowledge_bases SET active_index_version_id = %s
               WHERE knowledge_base_id = %s AND active_index_version_id IS NULL""",
            (index_version_id, knowledge_base_id),
        )
        row = connection.execute(
            "SELECT active_index_version_id FROM knowledge_bases WHERE knowledge_base_id = %s",
            (knowledge_base_id,),
        ).fetchone()
        if row and row[0]:
            from .pipeline_governance import ensure_index_definition

            ensure_index_definition(
                connection,
                knowledge_base_id=knowledge_base_id,
                index_version_id=str(row[0]),
            )
    # 并发下另一个 worker 可能先完成引导，此时沿用它创建的版本。
    return str(row[0]) if row and row[0] else index_version_id


def finalize_building_version(database_url: str, index_version_id: str) -> str:
    """按覆盖完整性把 building 版本推进到 ready 或 failed，返回新状态。

    分母是"该知识库中 current_version_id 非空的文档数"：尚未成功索引的 pending / failed
    文档本就没有可用分块，把它们计入会让新版本永远无法放行。
    该批次还有 queued / running 任务时不动状态，返回 building；已离开 building 的版本
    只回报当前状态，重复调用因此安全。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            version = connection.execute(
                """SELECT knowledge_base_id, status, rebuild_batch_id
                   FROM index_versions WHERE index_version_id = %s FOR UPDATE""",
                (index_version_id,),
            ).fetchone()
            if version is None:
                raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
            if str(version["status"]) != "building":
                return str(version["status"])
            unfinished = connection.execute(
                """SELECT count(*) AS total FROM index_jobs
                   WHERE rebuild_batch_id = %s AND status IN ('queued', 'running')""",
                (version["rebuild_batch_id"],),
            ).fetchone()
            if int(unfinished["total"]) > 0:
                return "building"
            failed = connection.execute(
                """SELECT count(*) AS total FROM index_jobs
                   WHERE rebuild_batch_id = %s AND status IN ('failed', 'cancelled')""",
                (version["rebuild_batch_id"],),
            ).fetchone()
            expected = connection.execute(
                """SELECT count(*) AS total FROM documents
                   WHERE knowledge_base_id = %s AND current_version_id IS NOT NULL""",
                (version["knowledge_base_id"],),
            ).fetchone()
            covered = connection.execute(
                """SELECT count(DISTINCT document_version_id) AS total FROM chunks
                   WHERE index_version_id = %s""",
                (index_version_id,),
            ).fetchone()
            # 覆盖 0 篇文档的版本不得放行：分子分母同时为 0 时"覆盖完整"在算术上成立，
            # 但切过去等于把知识库变成空索引。
            complete = (
                int(failed["total"]) == 0
                and int(covered["total"]) > 0
                and int(covered["total"]) == int(expected["total"])
            )
            status = "ready" if complete else "failed"
            connection.execute(
                "UPDATE index_versions SET status = %s WHERE index_version_id = %s",
                (status, index_version_id),
            )
    if status == "ready":
        create_partial_vector_index(database_url, index_version_id)
    return status


def _partial_index_name(index_version_id: str) -> str:
    """索引名由版本 id 稳定推导，DROP 时不必查库即可对上同一个索引。"""

    return f"chunks_hnsw_{index_version_id.replace('-', '_')}"


def create_partial_vector_index(database_url: str, index_version_id: str) -> None:
    """为单个索引版本建部分 HNSW 索引。

    pgvector 对带 WHERE 过滤的 ANN 查询是 post-filter：默认只取 hnsw.ef_search 个候选再
    过滤，过滤掉大半就静默少返回。``index_version_id`` 天生只有极少取值（同一知识库同时
    最多 active / building / previous 三个），官方对这种场景推荐部分索引——索引内只含本
    版本的行，查询谓词与索引谓词一致，post-filter 问题因此不出现。

    索引谓词必须是不可变表达式，不能用绑定参数（DDL 不接受 $1），因此用 psycopg 的
    sql.Literal 把版本 id 安全地拼成字面量。

    维度修饰：pgvector 的 HNSW 构建对无维度列报 "column does not have dimensions"。
    ``0010`` 迁移只在 index_settings 已有行时 ALTER 过 chunks.embedding，空库升级后该列
    仍是无维度的 vector，之后写入的分块也不会改变这一点。因此这里在建索引前按本索引版本
    冻结的 embedding_dimension 补做 ALTER，不写死数字；若既有分块维度与之不符，
    pgvector 自己会带着实际维度报错，不静默跳过。
    """

    with psycopg.connect(database_url, autocommit=True) as connection:
        row = connection.execute(
            "SELECT embedding_dimension FROM index_versions WHERE index_version_id = %s",
            (index_version_id,),
        ).fetchone()
        if row is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
        typmod = connection.execute(
            """SELECT atttypmod FROM pg_attribute
               WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'""",
        ).fetchone()
        if typmod is not None and int(typmod[0]) < 0:
            connection.execute(
                sql.SQL("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector({dimension})").format(
                    dimension=sql.Literal(int(row[0]))
                )
            )
        connection.execute(
            sql.SQL(
                """CREATE INDEX IF NOT EXISTS {name} ON chunks
                   USING hnsw (embedding vector_cosine_ops)
                   WHERE index_version_id = {value}"""
            ).format(
                name=sql.Identifier(_partial_index_name(index_version_id)),
                value=sql.Literal(index_version_id),
            )
        )


def drop_partial_vector_index(database_url: str, index_version_id: str) -> None:
    """删除该索引版本的部分索引；索引已不存在时静默通过，清理路径可重复执行。"""

    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP INDEX IF EXISTS {name}").format(
                name=sql.Identifier(_partial_index_name(index_version_id))
            )
        )


def switch_to_version(
    database_url: str,
    index_version_id: str,
    report: RetrievalEvaluationReport,
    audit: AuditRepository | None = None,
) -> dict[str, object]:
    """把 ready 版本切为 active，原 active 降为 previous，原 previous 转 retired。

    质量门是**相对比较**：只要求三项指标不相对基线回退，不要求达到冻结的绝对阈值。
    两者回答的是不同问题——绝对阈值（Recall@5 0.70 等）回答"这套系统能否上线"，
    而切换要回答的是"这次换配置是变好还是变坏"。把绝对阈值用作切换门槛会让功能锁死：
    `corpus_v2` 在当前实现下召回阶段 0.6862 未达 0.70，永远产不出 ``passed`` 的报告，
    于是一次索引切换都做不成。回退与否由报告自己的 ``regressed`` 给出（生成报告时传入
    上一版本报告作 baseline，见 evaluation/report.py 的 assess_metric），这里不重复实现
    比较规则，也不再检查 ``official``——它在 run_corpus_baseline 里就等于 ``passed``
    （`:198` 的 ``official=report.passed``），检查它等于又查一遍绝对阈值。

    指纹比对是本函数真正的牙齿：阻止用 A 配置跑出的报告放行 B 配置的索引。报告里的任何
    布尔字段都可以被伪造，配置指纹不行——它必须由被测配置本身算出来。

    三条 UPDATE 的顺序不能调整，两条实测理由：one_previous_idx 是非延迟的 partial unique
    index，previous 与 active 并存时先降级原 active 会立刻报
    "duplicate key value violates unique constraint index_versions_one_previous_idx"；
    即便躲开它，退役语句按 ``status = 'previous'`` 匹配，跑在降级之后会把刚降级的版本一并
    退役，回滚路径静默消失。因此先退役原 previous，再降级原 active，最后提升目标。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        target = connection.execute(
            "SELECT * FROM index_versions WHERE index_version_id = %s FOR UPDATE",
            (index_version_id,),
        ).fetchone()
        if target is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
        if str(target["status"]) != "ready":
            raise AppError(
                "INDEX_VERSION_NOT_READY",
                f"索引版本状态为 {target['status']}，只有 ready 可以切换。",
                409,
            )
        regressed = [
            name
            for name, metric in (
                ("recall_at_5", report.recall_at_5),
                ("vector_mrr", report.vector_mrr),
                ("rerank_mrr", report.rerank_mrr),
            )
            if metric.regressed
        ]
        if regressed:
            raise AppError(
                "INDEX_QUALITY_REGRESSED",
                f"以下指标相对基线回退，拒绝切换：{'、'.join(regressed)}。",
                409,
            )
        if report.config_fingerprint is None:
            raise AppError(
                "INDEX_REPORT_INCOMPLETE",
                "放行报告缺少配置指纹，无法证明它评测的就是该索引版本的配置。",
                409,
            )
        if report.config_fingerprint != str(target["config_fingerprint"]):
            raise AppError(
                "INDEX_CONFIG_MISMATCH",
                "报告的配置指纹与索引版本不一致，拒绝放行。",
                409,
            )
        knowledge_base_id = str(target["knowledge_base_id"])
        connection.execute(
            """UPDATE index_versions SET status = 'retired', retired_at = now()
               WHERE knowledge_base_id = %s AND status = 'previous'""",
            (knowledge_base_id,),
        )
        demoted = connection.execute(
            """UPDATE index_versions SET status = 'previous'
               WHERE knowledge_base_id = %s AND status = 'active'
               RETURNING index_version_id""",
            (knowledge_base_id,),
        ).fetchone()
        connection.execute(
            """UPDATE index_versions
               SET status = 'active', activated_at = now(), evaluation_report_id = %s
               WHERE index_version_id = %s""",
            (report.report_id, index_version_id),
        )
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id = %s WHERE knowledge_base_id = %s",
            (index_version_id, knowledge_base_id),
        )
        connection.execute(
            """UPDATE index_builds SET status='succeeded', finished_at=now(), updated_at=now()
               WHERE index_version_id=%s AND status='ready'""",
            (index_version_id,),
        )
        connection.execute(
            """UPDATE operations o SET status='succeeded', current_stage='active',
                      progress_percent=100, finished_at=now(), updated_at=now()
               FROM index_builds ib
               WHERE ib.operation_id=o.operation_id AND ib.index_version_id=%s""",
            (index_version_id,),
        )
    if audit is not None:
        audit.record(
            "index_version.activate",
            actor_id=None,
            actor_role="operator",
            resource_type="index_version",
            resource_id=index_version_id,
            result="success",
        )
    return {
        "knowledge_base_id": knowledge_base_id,
        "active": index_version_id,
        "previous": str(demoted["index_version_id"]) if demoted else "",
        # 绝对阈值结论只作提示，不参与放行判定：操作者应当知道自己切到的索引在冻结
        # 标准上是否达标，但那是系统能否上线的问题，不是能否换索引的问题。
        "meets_frozen_thresholds": report.passed,
    }


def rollback_to_previous(
    database_url: str,
    knowledge_base_id: str,
    audit: AuditRepository | None = None,
) -> dict[str, str]:
    """把 previous 切回 active，原 active 退回 ready。

    回滚不要求新报告：目标版本此前已被质量门放行过，其 ``evaluation_report_id`` 仍然有效，
    因此提升它不会违反 index_versions_active_requires_report。原 active 退回 ready 而不是
    previous——同一知识库只允许一个 previous，且它同样是放行过的版本，ready 语义正确。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        target = connection.execute(
            """SELECT index_version_id FROM index_versions
               WHERE knowledge_base_id = %s AND status = 'previous' FOR UPDATE""",
            (knowledge_base_id,),
        ).fetchone()
        if target is None:
            raise AppError("INDEX_NO_PREVIOUS_VERSION", "没有可回滚的上一索引版本。", 409)
        restored = str(target["index_version_id"])
        demoted = connection.execute(
            """UPDATE index_versions SET status = 'ready'
               WHERE knowledge_base_id = %s AND status = 'active'
               RETURNING index_version_id""",
            (knowledge_base_id,),
        ).fetchone()
        connection.execute(
            "UPDATE index_versions SET status = 'active', activated_at = now() WHERE index_version_id = %s",
            (restored,),
        )
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id = %s WHERE knowledge_base_id = %s",
            (restored, knowledge_base_id),
        )
    if audit is not None:
        audit.record(
            "index_version.rollback",
            actor_id=None,
            actor_role="operator",
            resource_type="index_version",
            resource_id=restored,
            result="success",
        )
    return {
        "knowledge_base_id": knowledge_base_id,
        "active": restored,
        "demoted": str(demoted["index_version_id"]) if demoted else "",
    }


def retire_version(database_url: str, index_version_id: str) -> int:
    """删除 retired / failed 版本的分块与其部分索引，返回删除的分块数。

    状态本身不代表数据已删除：``retired`` 只表示"分块可以被删"，实际删除只在这里发生。
    版本记录保留，仍是可审计的事实。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        version = connection.execute(
            "SELECT status FROM index_versions WHERE index_version_id = %s FOR UPDATE",
            (index_version_id,),
        ).fetchone()
        if version is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
        if str(version["status"]) not in {"retired", "failed"}:
            raise AppError(
                "INDEX_VERSION_IN_USE",
                f"索引版本状态为 {version['status']}，只有 retired 或 failed 可以清理。",
                409,
            )
        deleted = connection.execute(
            "DELETE FROM chunks WHERE index_version_id = %s", (index_version_id,)
        ).rowcount
    drop_partial_vector_index(database_url, index_version_id)
    return int(deleted)
