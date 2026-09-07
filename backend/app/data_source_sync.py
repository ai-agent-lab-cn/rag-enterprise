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
from typing import Any, Protocol
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .chunking import chunking_version
from .config import Settings
from .connectors import (
    Connector,
    LocalDirectoryConnector,
    ReadOnlyDatabaseConnector,
    S3Connector,
    SourceObject,
    WebConnector,
)
from .errors import AppError
from .observability import structured_log
from .pipeline_governance import (
    _sync_resource_params,
    aggregate_sync_run,
    create_operation,
    fail_sync_operation,
    upsert_sync_resource,
    upsert_sync_resources,
)
from .postgres_repositories import PostgresDataSourceRepository
from .schemas import DocumentInfo

# 已实现同步的数据源类型。web 与 connector 自 0001 起就是预留值，不对应任何实现。
SYNCABLE_SOURCE_TYPES = frozenset({"local_directory", "object_storage", "web", "connector"})


class DocumentIndexer(Protocol):
    """同步框架需要的索引能力，仅此一项。

    同步只负责「发现差异并把变化对象交给索引链路」，不参与索引版本的生命周期。
    把这一项声明成协议、由调用方注入，而不是在这里构造 ``PostgresAsyncRAGService``，
    是为了让依赖方向单向：同步层声明它需要什么，不认识谁提供。此前这里反向导入
    实现类，与 ``postgres_documents`` 形成循环依赖，两边都只能靠函数体内延迟导入绕开。
    """

    def index_document(
        self,
        filename: str,
        content: bytes,
        knowledge_base_id: str = ...,
        metadata: dict[str, object] | None = ...,
        data_source_id: str | None = ...,
        relative_path: str | None = ...,
        sync_run_id: str | None = ...,
    ) -> DocumentInfo: ...


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
                 AND iv.status IN ('active', 'previous', 'building', 'validating', 'ready')""",
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


def _apply_governance_metadata(
    database_url: str, knowledge_base_id: str, document_id: str, metadata: dict[str, object]
) -> None:
    """正文未变时仍扩散 Metadata/ACL，避免幂等短路留下旧权限。"""
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE documents SET metadata=metadata || %s, updated_at=now()
               WHERE knowledge_base_id=%s AND document_id=%s""",
            (Jsonb(metadata), knowledge_base_id, document_id),
        )
        connection.execute(
            """UPDATE chunks c SET metadata=c.metadata || %s
               FROM documents d JOIN index_versions iv ON iv.knowledge_base_id=d.knowledge_base_id
               WHERE d.knowledge_base_id=%s AND d.document_id=%s
                 AND c.knowledge_base_id=d.knowledge_base_id
                 AND c.document_version_id=d.current_version_id
                 AND c.index_version_id=iv.index_version_id
                 AND iv.status IN ('active','previous','building','validating','ready')""",
            (Jsonb(metadata), knowledge_base_id, document_id),
        )


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

    if source_type == "web":
        urls = [str(value) for value in configuration.get("urls") or []]
        return WebConnector(
            urls, max_bytes=max_bytes,
            sitemap_url=str(configuration.get("sitemap_url") or "") or None,
            max_objects=int(configuration.get("max_objects") or 1000),
        )

    if source_type == "connector" and configuration.get("connector_type") == "database_readonly":
        return ReadOnlyDatabaseConnector(
            str(configuration.get("database_url_env") or ""),
            str(configuration.get("view") or ""),
            str(configuration.get("id_column") or "id"),
            str(configuration.get("content_column") or "content"),
            str(configuration.get("updated_column") or "") or None,
            {str(key): str(value) for key, value in dict(configuration.get("metadata_mapping") or {}).items()},
            {str(key): str(value) for key, value in dict(configuration.get("acl_mapping") or {}).items()},
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
            """SELECT data_source_id, knowledge_base_id, source_type, sync_enabled
               FROM data_sources WHERE data_source_id = %s""",
            (data_source_id,),
        ).fetchone()
        if source is None:
            raise AppError("DATA_SOURCE_NOT_FOUND", "未找到该数据源。", 404)
        if not bool(source["sync_enabled"]):
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
                operation_id = create_operation(
                    connection,
                    operation_type="sync_run",
                    knowledge_base_id=str(source["knowledge_base_id"]),
                    data_source_id=data_source_id,
                    idempotency_key=f"sync:{data_source_id}:{sync_run_id}",
                )
                connection.execute(
                    """INSERT INTO sync_runs
                       (sync_run_id, data_source_id, knowledge_base_id, status, stage, cursor,
                        input_cursor, operation_id)
                       VALUES (%s, %s, %s, 'queued', 'discover',
                         (SELECT next_cursor FROM sync_runs
                          WHERE data_source_id = %s AND status IN ('succeeded', 'partial_failed')
                          ORDER BY created_at DESC LIMIT 1),
                         (SELECT next_cursor FROM sync_runs
                          WHERE data_source_id = %s AND status IN ('succeeded', 'partial_failed')
                          ORDER BY created_at DESC LIMIT 1), %s)""",
                    (sync_run_id, data_source_id, source["knowledge_base_id"], data_source_id,
                     data_source_id, operation_id),
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
                connection.execute(
                    """UPDATE data_sources SET last_sync_status='queued',
                              sync_failure_reason=NULL, updated_at=now()
                       WHERE data_source_id=%s""",
                    (data_source_id,),
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
        "failure_reason", "input_cursor", "discovered_cursor", "committed_cursor",
        "total_count", "completed_count", "processing_count", "dead_letter_count",
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


def _ensure_sync_active(database_url: str, sync_run_id: str) -> None:
    """协作式取消检查点，同时为任务续租。

    两件事合在一处不是图省事：它们问的是同一个问题——「我还该继续吗」。答案为是时就该
    让别人知道我还活着。

    **续租是必须的。** ``recover_stale_jobs`` 会把 ``status='running'`` 且
    ``locked_at`` 超过 ``index_job_stale_seconds``（默认 900 秒）的任务原地改回 'queued'，
    而一次同步的耗时没有上界（``list_objects`` 要读完每个文件算哈希，逐个对象还要
    fetch 加索引）。超时后任务被另一个 worker 领走，两份 run_sync 并发跑在**同一个
    sync_run_id** 上，互相覆盖 data_source_objects，先跑完的那份还会提前释放同步锁。

    两个唯一索引都拦不住这种并发：``index_jobs_one_active_sync_idx`` 与
    ``sync_runs_one_active_source_idx`` 防的是「两条不同的记录」，而这里自始至终是同一行。
    """

    if not sync_run_id:
        return
    with psycopg.connect(database_url) as connection, connection.transaction():
        row = connection.execute(
            "SELECT status FROM sync_runs WHERE sync_run_id=%s", (sync_run_id,)
        ).fetchone()
        if row is None or str(row[0]) in {"aborted", "failed"}:
            raise AppError("SYNC_CANCELLED", "同步任务已取消。", 409)
        connection.execute(
            """UPDATE index_jobs SET locked_at = now(), updated_at = now()
               WHERE sync_run_id = %s AND job_type = 'sync' AND status = 'running'""",
            (sync_run_id,),
        )


@dataclass(frozen=True)
class SyncContext:
    """一次同步的不变输入。

    ``connector`` 必须原样传递，不能在各阶段各自重建：Web 与 ReadOnlyDatabase 两种连接器
    是有状态的——``fetch`` / ``metadata`` / ``skipped`` 全部依赖 ``list_objects`` 时建立的
    内存缓存，重建一个新实例会拿到空缓存。
    """

    settings: Settings
    database_url: str
    data_source_id: str
    sync_run_id: str
    knowledge_base_id: str
    configuration: dict[str, Any]
    source_type: str
    connector: Connector
    indexer: DocumentIndexer


@dataclass(frozen=True)
class Discovery:
    """一次列举的结果，以及比对所需的两份本地快照。

    ``known`` 与 ``indexed`` 是两个不同的集合，都必须保留：前者是全部对象记录，用作熔断
    分母与删除集；后者只含「当前版本已 ready」的对象，用于区分「内容变了」和「上次没索引成」。
    合并成一次查询会让 retry 这一类整个失效。
    """

    remote: list[SourceObject]
    known: dict[str, dict[str, Any]]
    indexed: dict[str, dict[str, Any]]
    skipped: list[tuple[str, int]]
    cursor: str


@dataclass(frozen=True)
class SyncPlan:
    """差异计算的产物：每个远端对象与每条本地记录的去向都在这里有归属。"""

    added: list[SourceObject]
    updated: list[SourceObject]
    retry: list[SourceObject]
    deleted: list[str]
    unchanged: list[str]

    @property
    def changed(self) -> list[SourceObject]:
        return self.added + self.updated


def _load_context(
    settings: Settings, job: dict[str, Any], indexer: DocumentIndexer
) -> SyncContext:
    """读数据源配置并构造连接器。"""

    database_url = str(settings.database_url)
    data_source_id = str(job["data_source_id"])
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        source = connection.execute(
            """SELECT knowledge_base_id, configuration, source_type FROM data_sources
               WHERE data_source_id = %s""",
            (data_source_id,),
        ).fetchone()
    if source is None:
        raise AppError("DATA_SOURCE_NOT_FOUND", "未找到该数据源。", 404)
    configuration = dict(source["configuration"] or {})
    return SyncContext(
        settings=settings,
        database_url=database_url,
        data_source_id=data_source_id,
        sync_run_id=str(job.get("sync_run_id") or ""),
        knowledge_base_id=str(source["knowledge_base_id"]),
        configuration=configuration,
        source_type=str(source["source_type"]),
        connector=build_connector(
            configuration,
            str(source["source_type"]),
            max_bytes=settings.max_upload_mb * 1024 * 1024,
        ),
        indexer=indexer,
    )


def _discover(context: SyncContext) -> Discovery:
    """列举远端并取本地快照。

    ``list_objects`` 是整个流程唯一不可预估代价的外部调用（本地目录要读完每个文件算哈希，
    S3 一次 API 调用就够），因此任何事务都不能跨过它——这条边界不是风格选择。
    """

    indexed = _known_objects(context.database_url, context.data_source_id, only_indexed=True)
    known = _known_objects(context.database_url, context.data_source_id)
    remote = list(context.connector.list_objects())
    _ensure_sync_active(context.database_url, context.sync_run_id)
    cursor = sha256(
        "\n".join(
            f"{item.key}:{item.version}"
            for item in sorted(remote, key=lambda value: value.key)
        ).encode()
    ).hexdigest()
    return Discovery(
        remote=remote,
        known=known,
        indexed=indexed,
        # 跳过的对象在远端仍然存在，只是这次拉不动（超限、非文本）。必须在算差异之前拿到
        # 它们：list_objects 不会 yield 这些键，若不排除，「本地有、清单里没有」会把它们
        # 算成删除，于是一次调低 max_upload_mb 就能让线上文档静默退出检索。
        skipped=list(getattr(context.connector, "skipped", [])),
        cursor=cursor,
    )


def _plan(context: SyncContext, discovery: Discovery) -> SyncPlan:
    """比对差异并过熔断闸门。纯计算，不写库。"""

    known, indexed = discovery.known, discovery.indexed
    remote_keys = {item.key for item in discovery.remote}
    skipped_keys = {key for key, _ in discovery.skipped}

    # 分四类而不是三类。多出来的 retry 是「有记录但当前版本没到 ready」——解析或嵌入
    # 失败过的对象。它们不能当新增处理：index_document 查到相同 content_sha256 的既有
    # 版本会幂等短路，不会重新入队，于是文档永远停在 failed 而同步毫无反应。
    added = [item for item in discovery.remote if item.key not in known]
    retry = [item for item in discovery.remote if item.key in known and item.key not in indexed]
    updated = [
        item
        for item in discovery.remote
        if item.key in indexed and str(indexed[item.key]["version"]) != item.version
    ]
    deleted = sorted(
        key for key in known if key not in remote_keys and key not in skipped_keys
    )
    claimed = {item.key for item in added} | {item.key for item in updated} | {
        item.key for item in retry
    }
    plan = SyncPlan(
        added=added,
        updated=updated,
        retry=retry,
        deleted=deleted,
        unchanged=sorted(remote_keys - claimed),
    )
    # 闸门在任何写入之前：熔断触发说明「看到的清单不可信」，此时新增同样不可信。
    check_delete_circuit_breaker(
        SyncDiff(added=added, updated=updated, deleted=deleted),
        len(known),
        context.settings.sync_delete_threshold_percent,
        context.settings.sync_delete_minimum,
    )
    return plan


def _commit_plan(context: SyncContext, discovery: Discovery, plan: SyncPlan) -> None:
    """把整份差异清单一次事务写完，并置初始进度。

    逐条 upsert 的话每个对象一次 TCP 握手加一次事务提交，而「无变化」占绝大多数——
    千份文档的数据源光记录无变化就要开千余个连接。这个开销不会报错，只会让同步越来越慢。
    """

    upsert_sync_resources(
        context.database_url,
        [
            _sync_resource_params(context.sync_run_id, key, operation, status=status, stage=stage)
            for keys, operation, status, stage in (
                ([item.key for item in plan.added], "add", "discovered", "discover"),
                ([item.key for item in plan.updated], "update", "discovered", "discover"),
                ([item.key for item in plan.retry], "retry", "discovered", "discover"),
                (plan.deleted, "delete", "discovered", "discover"),
                (plan.unchanged, "unchanged", "unchanged", "complete"),
            )
            for key in keys
        ],
    )
    with psycopg.connect(context.database_url) as connection:
        connection.execute(
            """UPDATE operations SET status='running', current_stage='fetch',
                      total_count=%s, completed_count=%s, progress_percent=10, updated_at=now()
               WHERE operation_id=(SELECT operation_id FROM sync_runs WHERE sync_run_id=%s)""",
            (len(discovery.remote) + len(plan.deleted), len(plan.unchanged), context.sync_run_id),
        )


def _fetch_with_retry(context: SyncContext, key: str) -> bytes:
    """拉取单个对象，失败按配置次数重试。"""

    for attempt in range(1, context.settings.index_job_max_attempts + 1):
        try:
            return context.connector.fetch(key)
        except Exception:
            with psycopg.connect(context.database_url) as connection:
                connection.execute(
                    """UPDATE sync_resource_runs SET attempt_count=%s,
                              current_stage='retry_wait', updated_at=now()
                       WHERE sync_run_id=%s AND external_resource_id=%s""",
                    (attempt, context.sync_run_id, key),
                )
            if attempt >= context.settings.index_job_max_attempts:
                raise
    raise AppError("SOURCE_FETCH_FAILED", f"拉取 {key} 失败。", 502)


def _build_metadata(context: SyncContext, item: SourceObject) -> dict[str, Any]:
    """组装该对象的治理元数据。"""

    metadata = dict(context.configuration.get("metadata_defaults") or {})
    metadata.update({
        "source_system": context.source_type,
        "external_resource_id": item.key,
        "retrieval_status": "searchable",
    })
    metadata.update(context.connector.metadata(item.key))
    # 来源追踪：出事时这三项是全部线索——远端改了文件而同步没更新时，要判断是连接器
    # 没发现变化还是索引没跑成，得先知道当时拿到的 version。
    metadata.setdefault("source_uri", item.key)
    metadata["source_etag"] = item.version
    if item.modified_at is not None:
        # metadata 会被 Jsonb 序列化，datetime 进不去；ISO 字符串写回 timestamptz 列时
        # 由 Postgres 自行解析。
        metadata["source_modified_at"] = item.modified_at.isoformat()
    category_id = context.configuration.get("default_category_id")
    if category_id:
        with psycopg.connect(context.database_url) as connection:
            category = connection.execute(
                """SELECT name FROM document_categories
                   WHERE knowledge_base_id=%s AND category_id=%s AND active""",
                (context.knowledge_base_id, category_id),
            ).fetchone()
        if category:
            metadata.update({
                "category_id": str(category_id),
                "category": str(category[0]),
                "classification_status": "manual",
            })
    return metadata


def _process_object(context: SyncContext, item: SourceObject, operation: str) -> bool:
    """处理一个变化对象：拉取 → 索引 → 记账。返回是否成功。

    单个对象失败只让它自己进 dead_letter——「单文档解析失败不该让整次同步失败」这条
    设计承诺的落点就在这里的 except。
    """

    try:
        upsert_sync_resource(
            context.database_url, context.sync_run_id, item.key, operation,
            status="fetching", stage="fetch",
        )
        content = _fetch_with_retry(context, item.key)
        upsert_sync_resource(
            context.database_url, context.sync_run_id, item.key, operation,
            status="normalizing", stage="normalize",
        )
        metadata = _build_metadata(context, item)
        document = context.indexer.index_document(
            Path(item.key).name, content, context.knowledge_base_id, metadata=metadata,
            data_source_id=context.data_source_id, relative_path=item.key,
            sync_run_id=context.sync_run_id,
        )
        _apply_governance_metadata(
            context.database_url, context.knowledge_base_id, document.document_id, metadata
        )
        _record_object(context.database_url, context.data_source_id, item, document.document_id)
        # 按内容哈希精确定位 index_document 实际操作的那个版本。
        # 不能取「最新版本」：内容回退（A→B→A）时 index_document 复活的是承载 A 的旧版本，
        # 而最新版本是 B。取错的话资源行会带上一个早已完成的版本 id，
        # update_sync_resource_for_job 按 document_version_id 匹配不上它，
        # 那一行永远停在 building，批次算不出完成，最终把整个数据源锁死。
        # document_versions 上有 UNIQUE (knowledge_base_id, document_id, content_sha256)，
        # 因此这个查询恒定返回一行。
        with psycopg.connect(context.database_url) as connection:
            version = connection.execute(
                """SELECT document_version_id FROM document_versions
                   WHERE knowledge_base_id=%s AND document_id=%s AND content_sha256=%s""",
                (context.knowledge_base_id, document.document_id, sha256(content).hexdigest()),
            ).fetchone()
        if version and version[0]:
            immediate = document.status == "ready"
            upsert_sync_resource(
                context.database_url, context.sync_run_id, item.key, operation,
                status="succeeded" if immediate else "building",
                stage="complete" if immediate else "build",
                document_id=document.document_id,
                document_version_id=str(version[0]),
            )
        return True
    except Exception as error:
        upsert_sync_resource(
            context.database_url, context.sync_run_id, item.key, operation,
            status="dead_letter", stage="fetch_or_normalize",
            error_code=(error.code if isinstance(error, AppError) else "SYNC_RESOURCE_FAILED"),
            error_message=str(error)[:1000],
        )
        return False


def _process_changed(context: SyncContext, plan: SyncPlan) -> int:
    """逐个处理新增与更新。每个对象内部有外部 IO，因此无法收进一个事务。"""

    added_keys = {item.key for item in plan.added}
    changed = plan.changed
    processed = 0
    for index, item in enumerate(changed, start=1):
        _ensure_sync_active(context.database_url, context.sync_run_id)
        if _process_object(context, item, "add" if item.key in added_keys else "update"):
            processed += 1
        with psycopg.connect(context.database_url) as connection:
            connection.execute(
                """UPDATE operations SET current_stage='fetch', progress_percent=%s,
                          completed_count=completed_count+1, updated_at=now()
                   WHERE operation_id=(SELECT operation_id FROM sync_runs WHERE sync_run_id=%s)
                     AND status='running'""",
                (10 + round(25 * index / max(len(changed), 1), 2), context.sync_run_id),
            )
    return processed


def _process_retries(context: SyncContext, discovery: Discovery, plan: SyncPlan) -> int:
    """重跑上次没索引成的对象。"""

    processed = 0
    for item in plan.retry:
        _ensure_sync_active(context.database_url, context.sync_run_id)
        document_id = str(discovery.known[item.key]["document_id"])
        retried_version = _retry_object(
            context.settings, context.knowledge_base_id, document_id, context.sync_run_id
        )
        if retried_version:
            upsert_sync_resource(
                context.database_url, context.sync_run_id, item.key, "retry",
                status="building", stage="build",
                document_id=document_id, document_version_id=retried_version,
            )
            processed += 1
        else:
            # 没有可重试的版本是一个确定的结局，必须落终态。留在 'discovered' 的话这一行
            # 既非终态、又没有 document_version_id 可供后续任务匹配，aggregate 的完成判定
            # 永远为假，sync_runs 卡在 indexing，而 sync_runs_one_active_source_idx 会把它
            # 升级成整个数据源永久不可同步。
            upsert_sync_resource(
                context.database_url, context.sync_run_id, item.key, "retry",
                status="failed", stage="retry_unavailable", document_id=document_id,
                error_code="SYNC_RETRY_NO_VERSION",
                error_message="该资料没有可重试的版本记录。",
            )
    return processed


def _apply_deletions(context: SyncContext, discovery: Discovery, plan: SyncPlan) -> None:
    """软删除远端已消失的对象，并留下墓碑。"""

    document_ids = [
        str(discovery.known[key]["document_id"])
        for key in plan.deleted
        if discovery.known[key].get("document_id")
    ]
    mark_documents_deleted(context.database_url, context.knowledge_base_id, document_ids)
    _forget_objects(
        context.database_url, context.data_source_id, plan.deleted, context.sync_run_id
    )
    upsert_sync_resources(
        context.database_url,
        [
            _sync_resource_params(
                context.sync_run_id, key, "delete", status="deleted", stage="complete",
                document_id=(
                    str(discovery.known[key]["document_id"])
                    if discovery.known[key].get("document_id") else None
                ),
            )
            for key in plan.deleted
        ],
    )


def _reconcile_present(context: SyncContext) -> None:
    """把当前仍在数据源里的对象恢复为可检索，并撤掉它们的墓碑。

    恢复覆盖三种情况：本次新索引的、内容未变的、以及曾被软删后重新出现的——最后那种走的是
    「新增」路径，而 index_document 对相同内容哈希会幂等短路、不碰 metadata。

    放在 ``_apply_deletions`` 之后是为了让 ``present`` 成为一份真正的「剩下什么」快照：
    此时被软删的对象已从 data_source_objects 移走，不会进入恢复集。**实测反转顺序不会
    改变最终状态**（反转时它们先被标 searchable，随后又被删除阶段标回 deleted；墓碑那侧
    要撤的记录尚未写入，是空操作），所以这不是一条承重顺序——但依赖「后一次写覆盖前一次」
    比依赖一份干净的快照脆弱，故保持现序。
    """

    present = _known_objects(context.database_url, context.data_source_id)
    if present:
        # 墓碑表达的是「远端已经没有它了」，对象回来之后这句话不再成立。
        with psycopg.connect(context.database_url) as connection, connection.transaction():
            connection.execute(
                """DELETE FROM data_source_tombstones
                   WHERE data_source_id = %s AND object_key = ANY(%s)""",
                (context.data_source_id, list(present.keys())),
            )
    mark_documents_searchable(
        context.database_url,
        context.knowledge_base_id,
        [str(item["document_id"]) for item in present.values() if item.get("document_id")],
    )


def _refresh_governance_defaults(
    context: SyncContext, discovery: Discovery, plan: SyncPlan
) -> None:
    """把数据源级的治理配置扩散到内容未变的对象。

    `metadata_defaults` 与 `default_category_id` 的组装只发生在 added/updated 循环体里，
    而内容未变的对象根本不进那个循环。`_apply_governance_metadata` 的 docstring 承诺
    「正文未变时仍扩散 Metadata/ACL」，但它的调用点在 index_document 之后——前提是该对象的
    version 变了；对 local_directory 而言 version 就是内容哈希，正文不变则 version 不变，
    那条兜底永远不会被触发。结果是管理员改了默认分类或默认元数据，已同步的资料一份都不更新。

    只扩散**共享**的那部分：配置变更影响的正是它，而 source_etag、external_resource_id
    这些逐对象字段只在对象自身变化时才需要更新，那条路径已经覆盖了。因此这里是一次
    批量 UPDATE，不是每个对象重算一遍元数据。
    """

    patch: dict[str, Any] = dict(context.configuration.get("metadata_defaults") or {})
    category_id = context.configuration.get("default_category_id")
    if category_id:
        with psycopg.connect(context.database_url) as connection:
            category = connection.execute(
                """SELECT name FROM document_categories
                   WHERE knowledge_base_id=%s AND category_id=%s AND active""",
                (context.knowledge_base_id, category_id),
            ).fetchone()
        if category:
            patch.update({
                "category_id": str(category_id),
                "category": str(category[0]),
                "classification_status": "manual",
            })
    document_ids = [
        str(discovery.known[key]["document_id"])
        for key in plan.unchanged
        if key in discovery.known and discovery.known[key].get("document_id")
    ]
    if not patch or not document_ids:
        return
    with psycopg.connect(context.database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE documents SET metadata = metadata || %s, updated_at = now()
               WHERE knowledge_base_id = %s AND document_id = ANY(%s)""",
            (Jsonb(patch), context.knowledge_base_id, document_ids),
        )
        connection.execute(
            """UPDATE chunks c SET metadata = c.metadata || %s
               FROM documents d JOIN index_versions iv
                 ON iv.knowledge_base_id = d.knowledge_base_id
               WHERE d.knowledge_base_id = %s AND d.document_id = ANY(%s)
                 AND c.knowledge_base_id = d.knowledge_base_id
                 AND c.document_version_id = d.current_version_id
                 AND c.index_version_id = iv.index_version_id
                 AND iv.status IN ('active','previous','building','validating','ready')""",
            (Jsonb(patch), context.knowledge_base_id, document_ids),
        )


def _record_skipped(context: SyncContext, discovery: Discovery) -> None:
    """记录被连接器跳过的对象。

    跳过对同步结果是「成功」，对提问的人是「这份资料不在库里」。逐条记录而非拼成一行，
    是因为 structured_log 会丢弃列表值并截断长字符串。
    """

    upsert_sync_resources(
        context.database_url,
        [
            _sync_resource_params(
                context.sync_run_id, key, "skip", status="skipped", stage="size_limit",
                error_code="SOURCE_OBJECT_TOO_LARGE",
                error_message=f"对象 {size} bytes，超过上传限制",
            )
            for key, size in discovery.skipped
        ],
    )
    for key, size in discovery.skipped:
        structured_log(
            "data_source.object_skipped",
            data_source_id=context.data_source_id,
            object_key=key,
            size_bytes=size,
            max_bytes=context.settings.max_upload_mb * 1024 * 1024,
        )


def _finalize(context: SyncContext, discovery: Discovery, plan: SyncPlan) -> None:
    """落计数与游标，然后按单资源真实状态收口。

    ``_update_sync_run`` 必须先于 ``aggregate_sync_run``：committed_cursor 是 aggregate
    从 discovered_cursor 抄过去的，顺序反了会把当时还是 NULL 的值抄成已提交游标。
    """

    if not context.sync_run_id:
        return
    _update_sync_run(
        context.database_url,
        context.sync_run_id,
        "indexing",
        "build",
        added_count=len(plan.added),
        updated_count=len(plan.updated),
        deleted_count=len(plan.deleted),
        skipped_count=len(discovery.skipped),
        retry_count=len(plan.retry),
        discovered_cursor=discovery.cursor,
    )
    aggregate_sync_run(context.database_url, context.sync_run_id)


def _record_sync_failure(
    database_url: str, data_source_id: str, sync_run_id: str,
    *, status: str, error_code: str, message: str,
) -> None:
    """把失败收口到三张表。

    三者的状态域不同，必须分别写：``data_sources`` 与 ``sync_runs`` 用同一套 status，
    而 ``operations`` 此前根本没人收口——失败的任务在前端列表里永远排队、永远不报错。
    """

    _set_sync_status(database_url, data_source_id, status, f"{error_code}: {message}")
    if not sync_run_id:
        return
    _update_sync_run(
        database_url, sync_run_id, status, "failed",
        error_code=error_code, failure_reason=message,
    )
    fail_sync_operation(
        database_url, sync_run_id, status=status, error_code=error_code, error_message=message,
    )


def run_sync(
    settings: Settings, job: dict[str, Any], indexer: DocumentIndexer
) -> dict[str, object]:
    """执行一次同步：列举、比对、熔断、索引变化对象、软删消失对象。

    只负责「发现差异并入队」，实际索引由各自独立的 index 任务完成——这样单个文档的
    解析失败不会让整次同步失败。

    ``indexer`` 由调用方注入（见 ``DocumentIndexer``）。

    **两处先后是承重的**，改动前先看对应函数的 docstring：列举与熔断必须在任何写入之前
    （熔断的前提是「看到的清单不可信」，此时新增同样不可信）；``_finalize`` 里写
    discovered_cursor 必须先于 ``aggregate_sync_run``——committed_cursor 是后者从前者抄的。
    """

    database_url = str(settings.database_url)
    data_source_id = str(job["data_source_id"])
    sync_run_id = str(job.get("sync_run_id") or "")
    _ensure_sync_active(database_url, sync_run_id)
    _set_sync_status(database_url, data_source_id, "running")
    if sync_run_id:
        _update_sync_run(database_url, sync_run_id, "discovering", "discover")
    try:
        context = _load_context(settings, job, indexer)
        discovery = _discover(context)
        if sync_run_id:
            _update_sync_run(database_url, sync_run_id, "syncing", "diff")
        plan = _plan(context, discovery)

        _commit_plan(context, discovery, plan)
        processed = _process_changed(context, plan)
        processed += _process_retries(context, discovery, plan)

        _ensure_sync_active(database_url, sync_run_id)
        _apply_deletions(context, discovery, plan)
        _reconcile_present(context)
        _refresh_governance_defaults(context, discovery, plan)
        _set_sync_status(database_url, data_source_id, "running")
        _record_skipped(context, discovery)
        _finalize(context, discovery, plan)
        return {
            "data_source_id": data_source_id,
            "added": len(plan.added),
            "updated": len(plan.updated),
            "deleted": len(plan.deleted),
            "retried": len(plan.retry),
            "processed": processed,
            "skipped": [key for key, _ in discovery.skipped],
        }
    except AppError as error:
        _record_sync_failure(
            database_url, data_source_id, sync_run_id,
            status=(
                "aborted"
                if error.code in {"SYNC_DELETE_CIRCUIT_BREAKER", "SYNC_CANCELLED"}
                else "failed"
            ),
            error_code=error.code, message=error.message,
        )
        raise
    except Exception as error:
        # SDK、网络或解析层的非业务异常也必须落稳定状态，避免任务永远显示「同步中」。
        _record_sync_failure(
            database_url, data_source_id, sync_run_id, status="failed",
            error_code="SYNC_INTERNAL_ERROR",
            message=str(error)[:1000] or type(error).__name__,
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


def _forget_objects(
    database_url: str,
    data_source_id: str,
    object_keys: list[str],
    sync_run_id: str = "",
) -> None:
    """对象记录随软删除移入墓碑。

    记录必须从 ``data_source_objects`` 移走：留着会让下次同步把它算成"已知但远端没有"，
    反复触发软删除，还会永久污染熔断的比例分母。

    但移走不等于抹掉。删除前先写一条墓碑，否则「这份资料何时、被哪次同步删的」在任何
    一张表里都查不到，历史文档快照里的成员也无从解释为什么不在当前清单里。
    墓碑不进熔断分母——分母只数 data_source_objects，因此这里的行为与此前一致。
    """

    if not object_keys:
        return
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO data_source_tombstones
               (data_source_id, object_key, version, document_id, sync_run_id)
               SELECT data_source_id, object_key, version, document_id, %s
               FROM data_source_objects
               WHERE data_source_id = %s AND object_key = ANY(%s)
               ON CONFLICT (data_source_id, object_key) DO UPDATE
                 SET version = EXCLUDED.version,
                     document_id = EXCLUDED.document_id,
                     sync_run_id = EXCLUDED.sync_run_id,
                     deleted_at = now()""",
            (sync_run_id or None, data_source_id, object_keys),
        )
        connection.execute(
            "DELETE FROM data_source_objects WHERE data_source_id = %s AND object_key = ANY(%s)",
            (data_source_id, object_keys),
        )


def _retry_object(
    settings: Settings, knowledge_base_id: str, document_id: str, sync_run_id: str
) -> str | None:
    """重新处理一个索引失败的文档。

    走 ``reprocess_version`` 而不是 ``index_document``：后者对相同 content_sha256 的既有
    版本会幂等短路，不会重新入队，失败的文档因此永远无法通过同步恢复。
    """

    database_url = str(settings.database_url)
    with psycopg.connect(database_url) as connection:
        # 不能用 documents.current_version_id：索引失败时指针根本没移动过，它是 NULL。
        # 要重试的正是那个「没能变成 current」的版本——因此排除已经成功过的 ready 与
        # 被取代的 superseded，只留 pending / indexing / failed。
        #
        # 不能简单取版本号最大的那一版：内容回退（A→B→A）会复活承载 A 的旧版本 v1，
        # 而版本号最大的是承载 B 的 v2。取错的话重试会把 B 重新推成 current，
        # 而远端明明是 A——检索侧从此返回一份数据源里已经不存在的内容。
        row = connection.execute(
            """SELECT document_version_id FROM document_versions
               WHERE knowledge_base_id = %s AND document_id = %s
                 AND status NOT IN ('ready', 'superseded')
               ORDER BY version_number DESC LIMIT 1""",
            (knowledge_base_id, document_id),
        ).fetchone()
    if row is None or not row[0]:
        # 真的没有可重试的版本。返回 None，由调用方把资源行收进终态——
        # 留在非终态会让整个批次永远算不出完成。
        return None
    repository = PostgresDataSourceRepository(database_url)
    try:
        repository.reprocess_version(
            knowledge_base_id,
            str(row[0]),
            chunking_version(settings.chunk_size, settings.chunk_overlap),
            settings.index_job_max_attempts,
            sync_run_id=sync_run_id,
        )
    except psycopg.errors.UniqueViolation:
        # 该版本已有活动任务在跑，本次不必重复入队。
        pass
    # 无论本次是否真的入队，都返回版本 id：那个版本确实在重试中（要么刚入队，要么已有
    # 任务在跑）。此前这两条路径返回 None，调用方于是不写资源行，那一行永远停在
    # 'discovered'——非终态且 document_version_id 为 NULL，update_sync_resource_for_job
    # 再也匹配不上它，批次永远算不出完成，最终把整个数据源锁死。
    return str(row[0])


def retry_sync_resource(
    settings: Settings, data_source_id: str, sync_run_id: str, sync_resource_run_id: str
) -> bool:
    """只重试一个失败资源，并复用原批次保留审计关系。"""
    database_url = str(settings.database_url)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        resource = connection.execute(
            """SELECT r.document_id, r.document_version_id, r.status, s.knowledge_base_id,
                      s.operation_id
               FROM sync_resource_runs r JOIN sync_runs s USING (sync_run_id)
               WHERE r.sync_resource_run_id=%s AND r.sync_run_id=%s
                 AND s.data_source_id=%s""",
            (sync_resource_run_id, sync_run_id, data_source_id),
        ).fetchone()
    if resource is None:
        return False
    if str(resource["status"]) not in {"failed", "dead_letter"} or not resource["document_version_id"]:
        raise AppError("SYNC_RESOURCE_NOT_RETRYABLE", "该资源当前不可重试。", 409)
    job_id = PostgresDataSourceRepository(database_url).reprocess_version(
        str(resource["knowledge_base_id"]), str(resource["document_version_id"]),
        chunking_version(settings.chunk_size, settings.chunk_overlap),
        settings.index_job_max_attempts, sync_run_id=sync_run_id,
    )
    if not job_id:
        return False
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE sync_resource_runs SET status='building', current_stage='retry_wait',
                      error_code=NULL, error_message=NULL, finished_at=NULL, updated_at=now()
               WHERE sync_resource_run_id=%s""",
            (sync_resource_run_id,),
        )
        connection.execute(
            """UPDATE sync_runs SET status='indexing', stage='retry', finished_at=NULL,
                      failure_reason=NULL, updated_at=now() WHERE sync_run_id=%s""",
            (sync_run_id,),
        )
        connection.execute(
            """UPDATE operations SET status='running', current_stage='retry', finished_at=NULL,
                      error_code=NULL, error_message=NULL, updated_at=now() WHERE operation_id=%s""",
            (resource["operation_id"],),
        )
    aggregate_sync_run(database_url, sync_run_id)
    return True
