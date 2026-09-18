"""索引构建的输入文档快照。

一次索引构建要回答两个独立的问题：**用什么配置建**（index_versions 的配置快照）和
**对哪些文档建**（本模块）。此前只有前者被冻结，后者是每次现查——于是同一个索引版本
在不同时刻重跑，覆盖完整性的分母会变，构建结果不可复现。

本模块只提供创建与读取，**没有任何修改路径**：快照的价值全部来自它不会变。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .errors import AppError


def snapshot_fingerprint(members: list[tuple[str, str]]) -> str:
    """按成员集合计算指纹，与插入顺序无关。

    只纳入 included 成员：excluded 记录的是「看到了但没纳入」，它们不参与构建，
    也就不该影响「这次构建的输入是不是同一批」这个判断。
    """

    canonical = "\n".join(
        f"{document_id}:{document_version_id}"
        for document_id, document_version_id in sorted(members)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# 源文件已经不在磁盘上。数据库里的 document_version 仍是 ready，Worker 走到读文件那
# 一步才会炸，而失败原因此前被记成 PARSER_FAILED——「解析器坏了」和「文件没了」是两
# 件事：前者该修代码，后者该重新上传或删掉这条记录。
SOURCE_FILE_MISSING = "SOURCE_FILE_MISSING"
SOURCE_FILE_MISSING_MESSAGE = "源文件已丢失，无法构建索引。"


def validate_snapshot_sources(
    upload_root: Path, members: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """检查文档集合成员的源文件是否还在磁盘上，返回缺失清单。

    这一步放在创建版本之前，是因为源文件缺失在 Worker 里表现为整批构建部分失败：
    Version、Build、Operation 和 Jobs 都已经建好，用户看到的是一个卡在 build_failed
    的版本，而真正该做的动作（重新上传或删掉失效资料）在页面上完全看不出来。

    路径逃逸防御照抄删除路径（postgres_documents 里删除文档时的写法）：先 resolve()
    再确认仍在 upload_root 内。source_path 是库里的相对路径，正常情况下不会越界，
    但它不该是「因为正常情况下不会」才安全。

    返回项只含 document_id 与 filename：宿主绝对路径不进接口，也不进 Operation 文案。
    """

    root = upload_root.resolve()
    missing: list[dict[str, str]] = []
    for member in members:
        relative = str(member.get("source_path") or "")
        document = {
            "document_id": str(member.get("document_id") or ""),
            "filename": str(member.get("filename") or ""),
        }
        if not relative:
            missing.append(document)
            continue
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            missing.append(document)
    return missing


def require_present_sources(upload_root: Path, members: list[dict[str, Any]]) -> None:
    """缺失即以稳定错误码拒绝，不创建任何版本、构建、任务或 Operation。"""

    missing = validate_snapshot_sources(upload_root, members)
    if missing:
        raise AppError(
            SOURCE_FILE_MISSING,
            SOURCE_FILE_MISSING_MESSAGE,
            409,
            {"documents": missing},
        )


def current_document_set(
    connection: psycopg.Connection[Any], knowledge_base_id: str
) -> dict[str, Any]:
    """返回当前可构建文档集合及其稳定指纹，不产生任何持久化副作用。

    Creation Context、Preview 和最终 Create 共用这一个读取边界。三处若各写一份 SQL，
    纳入规则早晚会分叉，页面确认的范围就可能不是最终冻结的范围。
    """

    with connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            """SELECT d.document_id, d.filename, d.current_version_id, v.content_sha256,
                      v.source_file_bytes, v.source_path, latest.status AS latest_status,
                      latest.parse_status AS latest_parse_status,
                      latest.parse_failure_code
               FROM documents d
               LEFT JOIN document_versions v
                 ON v.document_version_id=d.current_version_id
               LEFT JOIN LATERAL (
                   SELECT status, parse_status, parse_failure_code
                   FROM document_versions candidate
                   WHERE candidate.knowledge_base_id=d.knowledge_base_id
                     AND candidate.document_id=d.document_id
                   ORDER BY candidate.version_number DESC LIMIT 1
               ) latest ON true
               WHERE d.knowledge_base_id=%s
               ORDER BY d.document_id""",
            (knowledge_base_id,),
        ).fetchall()
    included = [
        {
            "document_id": str(row["document_id"]),
            "filename": str(row["filename"]),
            "document_version_id": str(row["current_version_id"]),
            "content_sha256": str(row["content_sha256"] or ""),
            "source_file_bytes": int(row["source_file_bytes"] or 0),
            # 源文件相对路径，只用于构建前的存在性预检；它不进快照成员表，也不出接口。
            "source_path": str(row["source_path"] or ""),
        }
        for row in rows
        if row["current_version_id"]
    ]
    excluded_rows = [row for row in rows if not row["current_version_id"]]
    excluded = [str(row["document_id"]) for row in excluded_rows]
    excluded_details = [
        {
            "document_id": str(row["document_id"]),
            "filename": str(row["filename"]),
            "reason": (
                "parse_failed" if str(row["latest_parse_status"] or "") == "failed"
                else "missing_current_revision"
            ),
            "latest_status": str(row["latest_status"] or "missing"),
            "parse_failure_code": (
                str(row["parse_failure_code"]) if row["parse_failure_code"] else None
            ),
        }
        for row in excluded_rows
    ]
    return {
        "included": included,
        "excluded": excluded,
        "excluded_details": excluded_details,
        "fingerprint": snapshot_fingerprint(
            [(item["document_id"], item["document_version_id"]) for item in included]
        ),
    }


def create_snapshot(
    connection: psycopg.Connection[Any],
    *,
    knowledge_base_id: str,
    reason: str,
) -> str:
    """冻结该知识库当前可参与构建的文档集合。

    在调用方已有的事务里执行：清单查询与成员写入必须原子，否则「冻结」只是换了个
    地方现查。included 的判定与检索侧一致——只有 ``current_version_id`` 指向的版本
    参与构建，历史版本不进检索，重切它们没有收益。

    没有当前版本的文档记为 excluded 而不是丢弃：它们是「看到了但这次建不了」，
    留痕之后完整性校验才能解释分母，运维也能查到某份资料为什么没进这次索引。
    """

    # 显式给游标指定 row_factory，不继承调用方连接的设置：本函数在别人的事务里执行，
    # 那个连接是不是 dict_row 由调用方决定。依赖它等于让本函数的正确性取决于调用现场，
    # 而这类不一致在本仓已经出过事（见 CLAUDE.md 第四条的 fetchone()[0]）。
    document_set = current_document_set(connection, knowledge_base_id)
    included = document_set["included"]
    excluded = document_set["excluded_details"]

    document_snapshot_id = f"ds_{uuid4().hex[:20]}"
    connection.execute(
        """INSERT INTO document_snapshots
           (document_snapshot_id, knowledge_base_id, snapshot_fingerprint,
            snapshot_completeness, included_count, excluded_count, reason)
           VALUES (%s, %s, %s, 'complete', %s, %s, %s)""",
        (
            document_snapshot_id,
            knowledge_base_id,
            snapshot_fingerprint([
                (item["document_id"], item["document_version_id"])
                for item in included
            ]),
            len(included),
            len(excluded),
            reason,
        ),
    )
    for item in included:
        connection.execute(
            """INSERT INTO document_snapshot_members
               (document_snapshot_id, document_id, document_version_id, inclusion_status,
                content_sha256, filename)
               VALUES (%s, %s, %s, 'included', %s, %s)""",
            (
                document_snapshot_id,
                item["document_id"],
                item["document_version_id"],
                item["content_sha256"],
                item["filename"],
            ),
        )
    # 排除项也逐条留名。只记一个 excluded_count 的话，「这份资料为什么没进这次索引」
    # 在任何地方都查不到——而完整性门禁要求快照里每个成员都有明确处理结果，
    # 一个数字给不出这个结果。V36 放宽了 document_version_id 的 NOT NULL 正是为此。
    for item in excluded:
        connection.execute(
            """INSERT INTO document_snapshot_members
               (document_snapshot_id, document_id, document_version_id, inclusion_status,
                content_sha256, filename)
               VALUES (%s, %s, NULL, 'excluded', '', %s)""",
            (document_snapshot_id, item["document_id"], item["filename"]),
        )
    return document_snapshot_id


def get_snapshot(database_url: str, document_snapshot_id: str) -> dict[str, Any] | None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT * FROM document_snapshots WHERE document_snapshot_id = %s",
            (document_snapshot_id,),
        ).fetchone()
    return dict(row) if row else None


def list_members(
    database_url: str, document_snapshot_id: str, *, included_only: bool = True
) -> list[dict[str, Any]]:
    clause = " AND inclusion_status = 'included'" if included_only else ""
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            f"""SELECT document_id, document_version_id, inclusion_status,
                       content_sha256, filename
                FROM document_snapshot_members
                WHERE document_snapshot_id = %s{clause}
                ORDER BY document_id""",
            (document_snapshot_id,),
        ).fetchall()
    return [dict(row) for row in rows]
