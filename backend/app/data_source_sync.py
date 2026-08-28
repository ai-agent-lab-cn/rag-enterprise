"""数据源增量同步：差异计算、删除熔断与同步编排。

增量是这一层的能力，不是连接器的能力。连接器只回答「现在有什么」，本模块把那份清单与
``data_source_objects`` 里记录的上次状态比对，算出新增、更新、删除三类差异——这套逻辑
对所有连接器一致，不管它背后是本地目录还是对象存储。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings
from .connectors import Connector, LocalDirectoryConnector, S3Connector, SourceObject
from .errors import AppError
from .observability import structured_log

# 已实现同步的数据源类型。web 与 connector 自 0001 起就是预留值，不对应任何实现。
SYNCABLE_SOURCE_TYPES = frozenset({"local_directory", "object_storage"})


@dataclass(frozen=True)
class SyncDiff:
    """一次同步要处理的三类差异。

    ``deleted`` 只放对象键：它们在远端已经消失，除了键之外没有别的信息可用。
    """

    added: list[SourceObject] = field(default_factory=list)
    updated: list[SourceObject] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def has_changes(self) -> bool:
        return bool(self.added or self.updated or self.deleted)


def compute_diff(remote: list[SourceObject], known: dict[str, str]) -> SyncDiff:
    """比对远端清单与本地已知状态。

    判定只看 ``version``——它的契约是「内容变了才变」。不看 ``modified_at``：同内容
    重新落盘会刷新时间戳，那会触发无谓的重新解析与重新 embedding。

    ``deleted`` 排序输出：它会进熔断的错误信息，顺序稳定才能复现与比对。
    """

    added: list[SourceObject] = []
    updated: list[SourceObject] = []
    seen: set[str] = set()
    for item in remote:
        seen.add(item.key)
        previous = known.get(item.key)
        if previous is None:
            added.append(item)
        elif previous != item.version:
            updated.append(item)
    deleted = sorted(key for key in known if key not in seen)
    return SyncDiff(added=added, updated=updated, deleted=deleted)


def check_delete_circuit_breaker(
    diff: SyncDiff, known_total: int, threshold_percent: int, minimum_deletes: int = 3
) -> None:
    """删除量既超比例又超绝对下限时中止，不执行任何写入。

    连新增也不执行：触发熔断的典型原因是「看到的清单不可信」——根目录被误改、挂载点
    掉了、导出任务没跑成功——此时算出的新增同样不可信。

    V5-5 的索引版本回滚救不了这种情况：那是索引层的回滚，文档记录本身的删除不在它的
    范围内。所以这道闸必须在写入之前。

    **为什么要绝对下限**：纯比例阈值在小知识库上过于敏感——3 份文档删 1 份就是 33%，
    10 份删 4 份就是 40%，而这些都是正常的日常操作。一个部门二十来份手册的知识库在
    企业里很常见。加了下限之后，日常的少量删除不再被拦，而配置错误导致的批量删除
    在小知识库上同样会被抓住（20 份全部消失时删除数 20 远超下限）。

    首次同步（``known_total`` 为 0）没有可删的东西，不做判定。
    """

    if known_total <= 0 or not diff.deleted:
        return
    if len(diff.deleted) <= minimum_deletes:
        return
    ratio = len(diff.deleted) * 100 / known_total
    if ratio > threshold_percent:
        listed = "、".join(diff.deleted[:10])
        overflow = f" 等 {len(diff.deleted)} 项" if len(diff.deleted) > 10 else ""
        raise AppError(
            "SYNC_DELETE_CIRCUIT_BREAKER",
            f"待删除 {len(diff.deleted)}/{known_total} 个对象（{ratio:.0f}%），"
            f"超过阈值 {threshold_percent}%，已中止同步：{listed}{overflow}",
            409,
        )


def _set_retrieval_status(
    database_url: str,
    knowledge_base_id: str,
    document_ids: list[str],
    status: str,
) -> int:
    """把一批文档的检索状态写进文档与分块 metadata。

    分块侧必须覆盖 ``active`` / ``previous`` / ``building`` 三种索引版本——只刷 active
    的话，切回 previous 之后被软删除的文档会重新可检索。这条规则与 V5-5 的 ACL 写扩散
    同源（见 postgres_repositories.py 里 update_acl 与 assign 两处）。
    """

    if not document_ids:
        return 0
    patch = {"retrieval_status": status}
    with psycopg.connect(database_url) as connection, connection.transaction():
        updated = connection.execute(
            """UPDATE documents SET metadata = metadata || %s, updated_at = now()
               WHERE knowledge_base_id = %s AND document_id = ANY(%s)""",
            (Jsonb(patch), knowledge_base_id, document_ids),
        ).rowcount
        connection.execute(
            """UPDATE chunks c SET metadata = c.metadata || %s
               FROM documents d, index_versions iv
               WHERE d.knowledge_base_id = %s
                 AND d.document_id = ANY(%s)
                 AND c.knowledge_base_id = d.knowledge_base_id
                 AND c.document_version_id = d.current_version_id
                 AND iv.index_version_id = c.index_version_id
                 AND iv.status IN ('active', 'previous', 'building')""",
            (Jsonb(patch), knowledge_base_id, document_ids),
        )
    return int(updated)


def mark_documents_deleted(
    database_url: str, knowledge_base_id: str, document_ids: list[str]
) -> int:
    """软删除：对象在数据源里消失后，让它的分块不再进检索。

    文档记录、版本记录与向量全部保留，物理删除仍只能由人显式执行。这样漏过熔断的
    单个误删可以一键恢复。
    """

    return _set_retrieval_status(database_url, knowledge_base_id, document_ids, "deleted")


def mark_documents_searchable(
    database_url: str, knowledge_base_id: str, document_ids: list[str]
) -> int:
    """对象重新出现且内容未变时恢复可检索，不重新解析索引。"""

    return _set_retrieval_status(database_url, knowledge_base_id, document_ids, "searchable")


def _read_credentials(configuration: dict[str, Any]) -> tuple[str, str]:
    """从环境变量读取对象存储的访问密钥。

    凭据绝不进数据库：写进 configuration 会让数据库备份、审计 payload 和只读数据源
    接口同时变成密钥泄露面。缺失时明确失败而不回退匿名访问——回退会让一个配置错误
    表现成「桶是空的」，而空清单会被差异计算判成全部删除。
    """

    name = str(configuration.get("credential_env") or "").strip()
    if not name:
        raise AppError(
            "SOURCE_CONFIGURATION_INVALID", "对象存储数据源必须配置 credential_env。", 400
        )
    access_key = os.getenv(f"{name}_ACCESS_KEY")
    secret_key = os.getenv(f"{name}_SECRET_KEY")
    if not access_key or not secret_key:
        raise AppError(
            "SOURCE_CREDENTIALS_MISSING",
            f"缺少环境变量 {name}_ACCESS_KEY 或 {name}_SECRET_KEY。",
            409,
        )
    return access_key, secret_key


def build_connector(
    configuration: dict[str, Any], source_type: str, max_bytes: int | None = None
) -> Connector:
    """按数据源类型构造连接器。

    只认已实现的类型。``web`` / ``connector`` 两个 source_type 自 0001 起就是预留值，
    不对应任何实现——把它们当已实现会让同步静默什么都不做。

    ``max_bytes`` 传给连接器让它在列举阶段跳过超限对象。同步走 index_document、
    绕过了 API 上传的 validate_upload，不自己设限的话一个大文件就能打死 Worker。
    """

    if source_type == "local_directory":
        root = configuration.get("root")
        if not root:
            raise AppError(
                "SOURCE_CONFIGURATION_INVALID", "本地目录数据源必须配置 root。", 400
            )
        suffixes = tuple(configuration.get("include_suffixes") or (".md", ".txt", ".pdf"))
        return LocalDirectoryConnector(Path(str(root)), suffixes, max_bytes=max_bytes)

    if source_type == "object_storage":
        endpoint = str(configuration.get("endpoint") or "").strip()
        bucket = str(configuration.get("bucket") or "").strip()
        if not endpoint or not bucket:
            raise AppError(
                "SOURCE_CONFIGURATION_INVALID", "对象存储数据源必须配置 endpoint 与 bucket。", 400
            )
        access_key, secret_key = _read_credentials(configuration)
        return S3Connector(
            endpoint,
            bucket,
            str(configuration.get("prefix") or ""),
            access_key,
            secret_key,
            region=configuration.get("region") or None,
            secure=bool(configuration.get("secure", True)),
            max_bytes=max_bytes,
        )

    raise AppError(
        "SOURCE_TYPE_NOT_SUPPORTED", f"数据源类型 {source_type} 尚未实现同步。", 409
    )


def enqueue_sync(
    database_url: str, data_source_id: str, max_attempts: int = 3
) -> dict[str, object]:
    """入队一次同步。

    同一数据源同时只允许一个活动同步任务，由 ``index_jobs_one_active_sync_idx``
    保证——两个同步并发跑会重复入队索引任务，并互相覆盖 data_source_objects。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        source = connection.execute(
            """SELECT data_source_id, knowledge_base_id, source_type, enabled
               FROM data_sources WHERE data_source_id = %s""",
            (data_source_id,),
        ).fetchone()
        if source is None:
            raise AppError("DATA_SOURCE_NOT_FOUND", "未找到该数据源。", 404)
        if not bool(source["enabled"]):
            raise AppError("DATA_SOURCE_DISABLED", "数据源已停用，不能启动同步。", 409)
        if str(source["source_type"]) not in SYNCABLE_SOURCE_TYPES:
            raise AppError(
                "SOURCE_TYPE_NOT_SUPPORTED",
                f"数据源类型 {source['source_type']} 尚未实现同步。",
                409,
            )
        index_job_id = f"job_{uuid4().hex[:20]}"
        sync_run_id = f"run_{uuid4().hex[:20]}"
        try:
            with connection.transaction():
                connection.execute(
                    """INSERT INTO sync_runs
                       (sync_run_id, data_source_id, knowledge_base_id, status, stage, cursor)
                       VALUES (%s, %s, %s, 'queued', 'discover',
                         (SELECT next_cursor FROM sync_runs
                          WHERE data_source_id = %s AND status IN ('succeeded', 'partial_failed')
                          ORDER BY created_at DESC LIMIT 1))""",
                    (sync_run_id, data_source_id, source["knowledge_base_id"], data_source_id),
                )
                connection.execute(
                    """INSERT INTO index_jobs
                       (index_job_id, knowledge_base_id, data_source_id, idempotency_key,
                        status, max_attempts, job_type, sync_run_id)
                       VALUES (%s, %s, %s, %s, 'queued', %s, 'sync', %s)""",
                    (
                        index_job_id,
                        source["knowledge_base_id"],
                        data_source_id,
                        f"sync:{data_source_id}:{uuid4().hex[:12]}",
                        max_attempts,
                        sync_run_id,
                    ),
                )
        except psycopg.errors.UniqueViolation:
            raise AppError(
                "SYNC_ALREADY_RUNNING", "该数据源已有同步任务在进行中。", 409
            ) from None
    return {
        "index_job_id": index_job_id,
        "sync_run_id": sync_run_id,
        "data_source_id": data_source_id,
    }


def _known_objects(
    database_url: str, data_source_id: str, *, only_indexed: bool = False
) -> dict[str, dict[str, Any]]:
    """读取该数据源已记录的对象状态。

    ``only_indexed`` 用于差异计算：只把「当前版本已 ready」的对象算作已同步。
    对象记录是在 ``index_document`` 返回后就写入的，而那时索引只是入队——若后续解析或
    嵌入失败，记录里已有 version，下次同步就会把它当成「无变化」永久跳过，而文档在列表里
    一直显示 failed，重跑同步没有任何效果。把未 ready 的对象排除出「已知」之后，它们会被
    重新算作变化并重试。
    """

    filter_sql = ""
    if only_indexed:
        filter_sql = """
              AND o.document_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM documents d
                  JOIN document_versions v ON v.document_version_id = d.current_version_id
                  WHERE d.document_id = o.document_id
                    AND d.data_source_id = o.data_source_id
                    AND v.status = 'ready')"""
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            f"""SELECT o.object_key, o.version, o.document_id
               FROM data_source_objects o
               WHERE o.data_source_id = %s{filter_sql}""",
            (data_source_id,),
        ).fetchall()
    return {str(row["object_key"]): dict(row) for row in rows}


def _set_sync_status(
    database_url: str, data_source_id: str, status: str, reason: str | None = None
) -> None:
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE data_sources
               SET last_sync_status = %s, sync_failure_reason = %s,
                   last_sync_at = CASE WHEN %s = 'succeeded' THEN now() ELSE last_sync_at END,
                   updated_at = now()
               WHERE data_source_id = %s""",
            (status, reason, status, data_source_id),
        )


def _update_sync_run(
    database_url: str,
    sync_run_id: str,
    status: str,
    stage: str,
    **values: object,
) -> None:
    allowed = {
        "added_count", "updated_count", "deleted_count", "skipped_count",
        "failed_count", "retry_count", "cursor", "next_cursor", "error_code",
        "failure_reason",
    }
    assignments = ["status = %s", "stage = %s", "updated_at = now()"]
    parameters: list[object] = [status, stage]
    if status not in {"queued"}:
        assignments.append("started_at = COALESCE(started_at, now())")
    if status in {"succeeded", "partial_failed", "aborted", "failed"}:
        assignments.append("finished_at = now()")
    for key, value in values.items():
        if key not in allowed:
            raise ValueError(f"unsupported sync run field: {key}")
        assignments.append(f"{key} = %s")
        parameters.append(value)
    parameters.append(sync_run_id)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            f"UPDATE sync_runs SET {', '.join(assignments)} WHERE sync_run_id = %s",
            parameters,
        )


def run_sync(settings: Settings, embedder: Any, job: dict[str, Any]) -> dict[str, object]:
    """执行一次同步：列举、比对、熔断、索引变化对象、软删消失对象。

    只负责「发现差异并入队」，实际索引由各自独立的 index 任务完成——这样单个文档的
    解析失败不会让整次同步失败。
    """

    database_url = str(settings.database_url)
    data_source_id = str(job["data_source_id"])
    sync_run_id = str(job.get("sync_run_id") or "")
    _set_sync_status(database_url, data_source_id, "running")
    if sync_run_id:
        _update_sync_run(database_url, sync_run_id, "discovering", "discover")
    try:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            source = connection.execute(
                """SELECT knowledge_base_id, configuration, source_type FROM data_sources
                   WHERE data_source_id = %s""",
                (data_source_id,),
            ).fetchone()
        if source is None:
            raise AppError("DATA_SOURCE_NOT_FOUND", "未找到该数据源。", 404)
        knowledge_base_id = str(source["knowledge_base_id"])
        configuration = dict(source["configuration"] or {})
        connector = build_connector(
            configuration,
            str(source["source_type"]),
            max_bytes=settings.max_upload_mb * 1024 * 1024,
        )

        # 只承认已 ready 的对象为"已同步"。
        indexed = _known_objects(database_url, data_source_id, only_indexed=True)
        # 熔断分母用全部记录：未 ready 的对象在远端依然存在，不该被算进"待删除"。
        known = _known_objects(database_url, data_source_id)
        remote = list(connector.list_objects())
        next_cursor = sha256(
            "\n".join(
                f"{item.key}:{item.version}"
                for item in sorted(remote, key=lambda value: value.key)
            ).encode()
        ).hexdigest()
        if sync_run_id:
            _update_sync_run(database_url, sync_run_id, "syncing", "diff")
        remote_keys = {item.key for item in remote}

        # 分四类而不是三类。多出来的 retry 是"有记录但当前版本没到 ready"——解析或嵌入
        # 失败过的对象。它们不能当新增处理：index_document 查到相同 content_sha256 的
        # 既有版本会幂等短路，不会重新入队，于是文档永远停在 failed 而同步毫无反应。
        added = [item for item in remote if item.key not in known]
        retry = [item for item in remote if item.key in known and item.key not in indexed]
        updated = [
            item
            for item in remote
            if item.key in indexed and str(indexed[item.key]["version"]) != item.version
        ]
        diff = SyncDiff(
            added=added,
            updated=updated,
            deleted=sorted(key for key in known if key not in remote_keys),
        )
        # 闸门在任何写入之前：熔断触发说明"看到的清单不可信"，此时新增同样不可信。
        check_delete_circuit_breaker(
            diff,
            len(known),
            settings.sync_delete_threshold_percent,
            settings.sync_delete_minimum,
        )

        from .postgres_documents import PostgresAsyncRAGService

        service = PostgresAsyncRAGService(settings, embedder, None, None)
        processed = 0
        for item in diff.added + diff.updated:
            metadata = dict(configuration.get("metadata_defaults") or {})
            metadata.update(
                {
                    "source_system": str(source["source_type"]),
                    "external_resource_id": item.key,
                    "retrieval_status": "searchable",
                }
            )
            category_id = configuration.get("default_category_id")
            if category_id:
                with psycopg.connect(database_url) as category_connection:
                    category = category_connection.execute(
                        """SELECT name FROM document_categories
                           WHERE knowledge_base_id=%s AND category_id=%s AND active""",
                        (knowledge_base_id, category_id),
                    ).fetchone()
                if category:
                    metadata.update(
                        {
                            "category_id": str(category_id),
                            "category": str(category[0]),
                            "classification_status": "manual",
                        }
                    )
            document = service.index_document(
                Path(item.key).name,
                connector.fetch(item.key),
                knowledge_base_id,
                metadata=metadata,
                data_source_id=data_source_id,
                relative_path=item.key,
            )
            _record_object(database_url, data_source_id, item, document.document_id)
            processed += 1
        for item in retry:
            if _retry_object(settings, knowledge_base_id, str(known[item.key]["document_id"])):
                processed += 1

        deleted_document_ids = [
            str(known[key]["document_id"])
            for key in diff.deleted
            if known[key].get("document_id")
        ]
        mark_documents_deleted(database_url, knowledge_base_id, deleted_document_ids)
        _forget_objects(database_url, data_source_id, diff.deleted)

        # 把当前远端仍存在的对象一律标为可检索。这同时覆盖三种情况：本次新索引的、
        # 内容未变的、以及曾被软删后重新出现的——最后那种走的是"新增"路径，而
        # index_document 对相同内容哈希会幂等短路、不碰 metadata，所以必须在这里恢复。
        present = _known_objects(database_url, data_source_id)
        present_ids = [
            str(item["document_id"]) for item in present.values() if item.get("document_id")
        ]
        mark_documents_searchable(database_url, knowledge_base_id, present_ids)

        _set_sync_status(database_url, data_source_id, "succeeded")
        # 跳过对同步结果是「成功」，对提问的人是「这份资料不在库里」。两者之间只有日志：
        # 超限对象不入队、不软删、不进对象记录，任何一张表里都查不到它存在过。
        # 逐条记录而非拼成一行，是因为 structured_log 会丢弃列表值并截断长字符串。
        skipped = list(getattr(connector, "skipped", []))
        for key, size in skipped:
            structured_log(
                "data_source.object_skipped",
                data_source_id=data_source_id,
                object_key=key,
                size_bytes=size,
                max_bytes=settings.max_upload_mb * 1024 * 1024,
            )
        if sync_run_id:
            _update_sync_run(
                database_url,
                sync_run_id,
                "succeeded",
                "complete",
                added_count=len(diff.added),
                updated_count=len(diff.updated),
                deleted_count=len(diff.deleted),
                skipped_count=len(skipped),
                retry_count=len(retry),
                next_cursor=next_cursor,
            )
        return {
            "data_source_id": data_source_id,
            "added": len(diff.added),
            "updated": len(diff.updated),
            "deleted": len(diff.deleted),
            "retried": len(retry),
            "processed": processed,
            "skipped": [key for key, _ in skipped],
        }
    except AppError as error:
        status = "aborted" if error.code == "SYNC_DELETE_CIRCUIT_BREAKER" else "failed"
        _set_sync_status(database_url, data_source_id, status, f"{error.code}: {error.message}")
        if sync_run_id:
            _update_sync_run(
                database_url,
                sync_run_id,
                status,
                "failed",
                error_code=error.code,
                failure_reason=error.message,
            )
        raise
    except Exception as error:
        # SDK、网络或解析层的非业务异常也必须落稳定状态，避免任务永远显示“同步中”。
        message = str(error)[:1000] or type(error).__name__
        _set_sync_status(database_url, data_source_id, "failed", f"SYNC_INTERNAL_ERROR: {message}")
        if sync_run_id:
            _update_sync_run(
                database_url,
                sync_run_id,
                "failed",
                "failed",
                error_code="SYNC_INTERNAL_ERROR",
                failure_reason=message,
            )
        raise


def _record_object(
    database_url: str, data_source_id: str, item: SourceObject, document_id: str
) -> None:
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO data_source_objects
               (data_source_id, object_key, version, document_id, synced_at)
               VALUES (%s, %s, %s, %s, now())
               ON CONFLICT (data_source_id, object_key)
               DO UPDATE SET version = EXCLUDED.version,
                             document_id = EXCLUDED.document_id,
                             synced_at = EXCLUDED.synced_at""",
            (data_source_id, item.key, item.version, document_id),
        )


def _forget_objects(database_url: str, data_source_id: str, object_keys: list[str]) -> None:
    """对象记录随软删除一并移除。

    留着会让下次同步把它算成"已知但远端没有"，反复触发软删除；移除之后对象若重新出现，
    会被当作新增重新索引——这与"内容可能已变"的实际情况一致。
    """

    if not object_keys:
        return
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "DELETE FROM data_source_objects WHERE data_source_id = %s AND object_key = ANY(%s)",
            (data_source_id, object_keys),
        )


def _retry_object(settings: Settings, knowledge_base_id: str, document_id: str) -> bool:
    """重新处理一个索引失败的文档。

    走 ``reprocess_version`` 而不是 ``index_document``：后者对相同 content_sha256 的既有
    版本会幂等短路，不会重新入队，失败的文档因此永远无法通过同步恢复。
    """

    from .chunking import chunking_version
    from .postgres_repositories import PostgresDataSourceRepository

    database_url = str(settings.database_url)
    with psycopg.connect(database_url) as connection:
        # 不能用 documents.current_version_id：索引失败时指针根本没移动过，它是 NULL。
        # 要重试的正是那个最新的、没能变成 current 的版本。
        row = connection.execute(
            """SELECT document_version_id FROM document_versions
               WHERE knowledge_base_id = %s AND document_id = %s
               ORDER BY version_number DESC LIMIT 1""",
            (knowledge_base_id, document_id),
        ).fetchone()
    if row is None or not row[0]:
        return False
    repository = PostgresDataSourceRepository(database_url)
    try:
        return bool(
            repository.reprocess_version(
                knowledge_base_id,
                str(row[0]),
                chunking_version(settings.chunk_size, settings.chunk_overlap),
                settings.index_job_max_attempts,
            )
        )
    except psycopg.errors.UniqueViolation:
        # 该版本已有活动任务在跑，本次不必重复入队。
        return False
