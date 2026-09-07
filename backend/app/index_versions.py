"""索引版本的配置指纹、创建与状态查询。

索引版本承载"这批分块由什么配置产出"这一事实。切换放行时用配置指纹比对评测报告，
阻止用一套配置跑出的合格报告去放行另一套配置的索引。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .chunking import chunking_version as derive_chunking_version
from .document_snapshots import create_snapshot, current_document_set
from .errors import AppError
from .parsers import PARSER_SCHEMA_VERSION

if TYPE_CHECKING:
    # 只用于类型标注：运行时导入 backend.evaluation 会把整个评测包（及其对 app.parsers
    # 的反向依赖）拉进应用启动路径。
    from .audit import AuditRepository


CREATION_REASONS = frozenset(
    {
        "initial_build",
        "config_changed",
        "document_snapshot_changed",
        "component_upgraded",
        "consistency_repair",
        "manual_rebuild",
    }
)
FORCED_CREATION_REASONS = frozenset({"consistency_repair", "manual_rebuild"})

# 这些值描述当前仓库真实的索引组件形态，不是可选产品配置。实现或 schema 改变时必须
# 递增对应值，使新版本的 release_fingerprint 跟着变化。
COMPONENT_SCHEMA_VERSIONS = {
    "vector_index_schema_version": "pgvector-hnsw-v1",
    "keyword_index_schema_version": "bm25-cache-v1",
    "metadata_schema_version": "chunk-metadata-v1",
    "acl_schema_version": "acl-json-v1",
    "citation_schema_version": "citation-location-v1",
}


@dataclass(frozen=True)
class Actor:
    """执行某次生命周期动作的人。

    `None` 表示系统自动触发（worker 收口构建、首次索引引导），与「不知道是谁」不同——
    前者是事实，后者是缺陷。此前 audit 里 actor_id 恒为硬编码的 None，两者无从区分。
    """

    actor_id: str | None
    actor_role: str | None


def config_fingerprint(
    chunking_version: str,
    embedding_model: str,
    embedding_dimension: int,
    processing_options: dict[str, Any],
    components: dict[str, str] | None = None,
) -> str:
    """按规范化 JSON 计算指纹，键顺序不影响结果。

    只纳入操作者能选择、评测能精确复现的配置。解析部分取全局
    ``PARSER_SCHEMA_VERSION`` 而不是各格式的 parser 版本，且不作为参数暴露——
    per-format 版本由文档格式决定（Markdown 与 PDF 是 2.0，DOCX 与 CSV 是 1.0），
    评测语料的格式组合与生产知识库必然不同，纳入它会让指纹永远匹配不上、
    切换永远被拒。索引版本表里仍记录 per-format 版本作为事实。
    """

    payload: dict[str, Any] = {
            "chunking_version": chunking_version,
            "parser_schema_version": PARSER_SCHEMA_VERSION,
            "embedding_model": embedding_model,
            "embedding_dimension": embedding_dimension,
            "processing_options": processing_options,
    }
    # 新治理版本把 Vector / Keyword / Metadata / ACL / Citation / Reranker 作为一套
    # 不可拆分的发布配置；旧调用不传 manifest，指纹算法保持兼容。
    if components is not None:
        payload["components"] = components
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def component_manifest(*, reranker_model: str) -> dict[str, str]:
    """返回参与版本发布的一组真实组件版本。"""

    return {
        **COMPONENT_SCHEMA_VERSIONS,
        "parser_schema_version": PARSER_SCHEMA_VERSION,
        "reranker_model": reranker_model,
    }


def release_fingerprint(
    *, config_fingerprint_value: str, document_set_fingerprint: str,
    components: dict[str, str],
) -> str:
    """把配置、文档集合和组件 manifest 合成不可变发布指纹。"""

    canonical = json.dumps(
        {
            "config_fingerprint": config_fingerprint_value,
            "document_set_fingerprint": document_set_fingerprint,
            "components": components,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _effective_definition(
    connection: psycopg.Connection[Any], *, chunk_size: int, chunk_overlap: int,
    reranker_model: str,
) -> dict[str, Any]:
    registered = connection.execute(
        "SELECT embedding_model, embedding_dimension FROM index_settings WHERE singleton"
    ).fetchone()
    target_chunking = derive_chunking_version(chunk_size, chunk_overlap)
    options = {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap}
    components = component_manifest(reranker_model=reranker_model)
    fingerprint = None
    if registered:
        fingerprint = config_fingerprint(
            target_chunking,
            str(registered["embedding_model"]),
            int(registered["embedding_dimension"]),
            options,
            components,
        )
    return {
        "chunking": {
            "version": target_chunking,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        },
        "parser": {"schema_version": PARSER_SCHEMA_VERSION},
        "embedding": {
            "model": str(registered["embedding_model"]) if registered else None,
            "dimension": int(registered["embedding_dimension"]) if registered else None,
        },
        "components": components,
        "processing_options": options,
        "config_fingerprint": fingerprint,
        "capabilities": [
            {"field": "chunk_size", "editable": True, "value": chunk_size, "source": "application_settings", "reason": None},
            {"field": "chunk_overlap", "editable": True, "value": chunk_overlap, "source": "application_settings", "reason": None},
            {
                "field": "parser",
                "editable": False,
                "value": PARSER_SCHEMA_VERSION,
                "source": "parser_registry",
                "reason": "由当前解析器注册表决定。",
            },
            {
                "field": "embedding",
                "editable": False,
                "value": str(registered["embedding_model"]) if registered else None,
                "source": "index_settings",
                "reason": "当前数据库使用全局向量维度。",
            },
            *[
                {
                    "field": key,
                    "editable": False,
                    "value": value,
                    "source": "runtime_component_manifest",
                    "reason": "由当前索引实现版本决定。",
                }
                for key, value in components.items()
                if key not in {"parser_schema_version", "reranker_model"}
            ],
        ],
    }


def _document_diff(
    connection: psycopg.Connection[Any], *, active_snapshot_id: str | None,
    current_members: dict[str, str],
) -> dict[str, int]:
    if not active_snapshot_id:
        return {
            "added": len(current_members),
            "removed": 0,
            "updated": 0,
            "unchanged": 0,
        }
    rows = connection.execute(
        """SELECT document_id, document_version_id FROM document_snapshot_members
           WHERE document_snapshot_id=%s AND inclusion_status='included'""",
        (active_snapshot_id,),
    ).fetchall()
    active_members = {
        str(row["document_id"]): str(row["document_version_id"]) for row in rows
    }
    current_ids, active_ids = set(current_members), set(active_members)
    updated = sum(
        current_members[document_id] != active_members[document_id]
        for document_id in current_ids & active_ids
    )
    return {
        "added": len(current_ids - active_ids),
        "removed": len(active_ids - current_ids),
        "updated": updated,
        "unchanged": len(active_ids & current_ids) - updated,
    }


def get_index_version_creation_context(
    database_url: str,
    knowledge_base_id: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    reranker_model: str,
    max_concurrent_builds: int = 2,
    max_documents: int = 10000,
) -> dict[str, Any]:
    """汇总创建入口所需事实；不创建 Version 或 Snapshot。"""

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        knowledge_base = connection.execute(
            "SELECT 1 FROM knowledge_bases WHERE knowledge_base_id=%s",
            (knowledge_base_id,),
        ).fetchone()
        if knowledge_base is None:
            raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404)
        definition = _effective_definition(
            connection,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            reranker_model=reranker_model,
        )
        document_set = current_document_set(connection, knowledge_base_id)
        document_version_ids = [
            str(item["document_version_id"]) for item in document_set["included"]
        ]
        active_document_jobs = 0
        if document_version_ids:
            active_document_jobs = int(
                connection.execute(
                    """SELECT count(*) AS total FROM index_jobs
                       WHERE document_version_id=ANY(%s)
                         AND status IN ('queued','running')""",
                    (document_version_ids,),
                ).fetchone()["total"]
            )
        active = connection.execute(
            """SELECT iv.*, ds.snapshot_fingerprint AS document_set_fingerprint,
                      ds.included_count, ds.excluded_count
               FROM index_versions iv
               LEFT JOIN document_snapshots ds
                 ON ds.document_snapshot_id=iv.document_snapshot_id
               WHERE iv.knowledge_base_id=%s AND iv.status='active'""",
            (knowledge_base_id,),
        ).fetchone()
        candidate = connection.execute(
            """SELECT index_version_id, status FROM index_versions
               WHERE knowledge_base_id=%s AND status IN ('building','validating','ready')
               ORDER BY created_at DESC LIMIT 1""",
            (knowledge_base_id,),
        ).fetchone()
        active_builds = int(
            connection.execute(
                "SELECT count(*) AS total FROM index_versions WHERE status='building'"
            ).fetchone()["total"]
        )
        latest_snapshot = connection.execute(
            """SELECT document_snapshot_id, snapshot_fingerprint, included_count,
                      excluded_count, snapshot_completeness, reason, created_at
               FROM document_snapshots WHERE knowledge_base_id=%s
               ORDER BY created_at DESC LIMIT 1""",
            (knowledge_base_id,),
        ).fetchone()
        current_members = {
            str(item["document_id"]): str(item["document_version_id"])
            for item in document_set["included"]
        }
        document_diff = _document_diff(
            connection,
            active_snapshot_id=(str(active["document_snapshot_id"]) if active and active["document_snapshot_id"] else None),
            current_members=current_members,
        )

    config_changed = bool(
        active
        and definition["config_fingerprint"]
        and str(active["config_fingerprint"]) != definition["config_fingerprint"]
    )
    document_changed = bool(
        active is not None
        and (
            not active["document_set_fingerprint"]
            or str(active["document_set_fingerprint"]) != document_set["fingerprint"]
        )
    )
    if active is None:
        scenario = "initial_build"
    elif config_changed or document_changed:
        scenario = "candidate"
    else:
        scenario = "no_change"
    blocked_reasons: list[str] = []
    if not document_set["included"]:
        blocked_reasons.append("知识库暂无可构建资料。")
    if definition["config_fingerprint"] is None:
        blocked_reasons.append("索引尚未登记向量模型。")
    if candidate:
        blocked_reasons.append(
            f"已有候选版本 {candidate['index_version_id']} 处于 {candidate['status']}。"
        )
    if active_builds >= max_concurrent_builds and not (
        candidate and str(candidate["status"]) == "building"
    ):
        blocked_reasons.append(
            f"当前已有 {active_builds} 个全量索引构建，达到并发上限 {max_concurrent_builds}。"
        )
    if len(document_set["included"]) > max_documents:
        blocked_reasons.append(
            f"本次包含 {len(document_set['included'])} 份资料，超过单次上限 {max_documents}。"
        )
    if active_document_jobs:
        blocked_reasons.append(
            f"当前文档集合中有 {active_document_jobs} 个索引或处理任务尚未结束。"
        )
    if scenario == "no_change":
        blocked_reasons.append("配置与文档集合均未变化。")
    return {
        "scenario": scenario,
        "definition": definition,
        "active_version": dict(active) if active else None,
        "candidate_version": dict(candidate) if candidate else None,
        "latest_document_snapshot": dict(latest_snapshot) if latest_snapshot else None,
        "document_scope": {
            "included": len(document_set["included"]),
            "excluded": len(document_set["excluded"]),
            "source_bytes": sum(int(item["source_file_bytes"]) for item in document_set["included"]),
            "parse_failed": sum(
                1 for item in document_set["excluded_details"]
                if item["reason"] == "parse_failed"
            ),
            "missing_current_revision": sum(
                1 for item in document_set["excluded_details"]
                if item["reason"] == "missing_current_revision"
            ),
        },
        "document_exclusions": document_set["excluded_details"],
        "build_capacity": {
            "active_builds": active_builds,
            "max_concurrent_builds": max_concurrent_builds,
            "remaining_build_slots": max(0, max_concurrent_builds - active_builds),
            "max_documents": max_documents,
        },
        "document_diff": document_diff,
        "document_set_fingerprint": document_set["fingerprint"],
        "config_changed": config_changed,
        "document_changed": document_changed,
        "creation_allowed": not blocked_reasons,
        "blocked_reasons": blocked_reasons,
    }


def preview_index_version_candidate(
    database_url: str,
    knowledge_base_id: str,
    *,
    reason: str,
    chunk_size: int,
    chunk_overlap: int,
    force: bool,
    force_reason: str | None,
    reranker_model: str,
    max_concurrent_builds: int = 2,
    max_documents: int = 10000,
) -> dict[str, Any]:
    """返回候选版本预览；所有许可结论均由后端产生。"""

    if reason not in CREATION_REASONS:
        raise AppError("INDEX_CREATION_REASON_INVALID", "索引版本创建原因无效。", 400)
    if chunk_overlap >= chunk_size:
        raise AppError("CHUNKING_POLICY_INVALID", "切片重叠必须小于切片大小。", 400)
    context = get_index_version_creation_context(
        database_url,
        knowledge_base_id,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        reranker_model=reranker_model,
        max_concurrent_builds=max_concurrent_builds,
        max_documents=max_documents,
    )
    blocked = [
        item for item in context["blocked_reasons"]
        if item != "配置与文档集合均未变化。"
    ]
    if context["scenario"] == "initial_build" and reason != "initial_build":
        blocked.append("首个版本必须使用“创建首个索引版本”场景。")
    if context["scenario"] != "initial_build" and reason == "initial_build":
        blocked.append("知识库已经存在生效版本。")
    changed = context["config_changed"] or context["document_changed"]
    if not changed and context["scenario"] != "initial_build" and not force:
        blocked.append("配置与文档集合均未变化；如需修复性重建，请填写原因。")
    if reason in FORCED_CREATION_REASONS and not force:
        blocked.append("修复性或主动重建必须显式确认强制创建。")
    if force and not (force_reason or "").strip():
        blocked.append("强制重建必须填写原因。")
    definition = context["definition"]
    config_value = str(definition["config_fingerprint"] or "")
    document_value = str(context["document_set_fingerprint"])
    release_value = (
        release_fingerprint(
            config_fingerprint_value=config_value,
            document_set_fingerprint=document_value,
            components=definition["components"],
        )
        if config_value
        else None
    )
    config_diff: list[dict[str, Any]] = []
    active = context["active_version"]
    if active:
        for field, candidate_value in (
            ("chunking_version", definition["chunking"]["version"]),
            ("embedding_model", definition["embedding"]["model"]),
            ("embedding_dimension", definition["embedding"]["dimension"]),
            ("processing_options", definition["processing_options"]),
        ):
            if active.get(field) != candidate_value:
                config_diff.append(
                    {"field": field, "active": active.get(field), "candidate": candidate_value}
                )
        active_components = dict(active.get("component_manifest") or {})
        if str(active.get("config_completeness")) == "complete":
            for field, candidate_value in definition["components"].items():
                if active_components.get(field) != candidate_value:
                    config_diff.append(
                        {
                            "field": field,
                            "active": active_components.get(field),
                            "candidate": candidate_value,
                        }
                    )
        else:
            config_diff.append(
                {
                    "field": "component_manifest",
                    "active": "unknown",
                    "candidate": definition["components"],
                }
            )
    return {
        **context,
        "reason": reason,
        "force": force,
        "force_reason": (force_reason or "").strip() or None,
        "config_fingerprint": config_value or None,
        "release_fingerprint": release_value,
        "config_snapshot": {
            "chunking": definition["chunking"],
            "parser": definition["parser"],
            "embedding": definition["embedding"],
            "processing_options": definition["processing_options"],
            "components": definition["components"],
        },
        "component_manifest": definition["components"],
        "config_diff": config_diff,
        "estimated_documents": context["document_scope"]["included"],
        "estimated_chunks": max(
            context["document_scope"]["included"],
            (context["document_scope"]["source_bytes"] + chunk_size - 1) // chunk_size,
        ),
        "estimated_embedding_units": context["document_scope"]["source_bytes"],
        "creation_allowed": not blocked,
        "blocked_reasons": blocked,
    }


def record_lifecycle_event(
    connection: psycopg.Connection[Any],
    *,
    knowledge_base_id: str,
    index_version_id: str,
    event_type: str,
    from_status: str | None = None,
    to_status: str | None = None,
    actor: Actor | None = None,
    reason: str | None = None,
    validation_report_id: str | None = None,
) -> None:
    """追加一条生命周期事件。

    在调用方的事务里执行：事件与它描述的那次状态转换必须同生共死，否则会留下
    「记了却没发生」或「发生了却没记」的事实。

    append-only：没有 update 路径。回滚不是撤销一条记录，是再追加一条方向相反的事件。
    """

    connection.execute(
        """INSERT INTO index_lifecycle_events
           (event_id, knowledge_base_id, index_version_id, event_type,
            from_status, to_status, actor_id, actor_role, reason, validation_report_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            f"ile_{uuid4().hex[:20]}",
            knowledge_base_id,
            index_version_id,
            event_type,
            from_status,
            to_status,
            actor.actor_id if actor else None,
            actor.actor_role if actor else None,
            reason,
            validation_report_id,
        ),
    )


def list_lifecycle_events(
    database_url: str,
    index_version_id: str,
    knowledge_base_id: str | None = None,
) -> list[dict[str, Any]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        if knowledge_base_id is None:
            rows = connection.execute(
                """SELECT * FROM index_lifecycle_events
                   WHERE index_version_id = %s ORDER BY created_at DESC, event_id DESC""",
                (index_version_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT * FROM index_lifecycle_events
                   WHERE index_version_id = %s AND knowledge_base_id = %s
                   ORDER BY created_at DESC, event_id DESC""",
                (index_version_id, knowledge_base_id),
            ).fetchall()
    return [dict(row) for row in rows]


def active_config_drift(
    database_url: str, knowledge_base_id: str, *, chunk_size: int, chunk_overlap: int,
    reranker_model: str | None = None,
) -> dict[str, Any] | None:
    """当前生效的索引是不是用现在的配置建的。

    ``config_fingerprint`` 此前只用于两件事：创建版本时算一次，验证时比对「评测报告评的
    是不是这个版本的配置」。**没有任何代码拿 active 版本的指纹与当前配置比对**——于是改完
    ``chunk_size`` 之后，线上索引仍是旧配置建的，可以无限期这样跑下去：不报错、不提示、
    页面上看不出来。这与 CLAUDE.md 第五条记的那几次腐烂是同一个形状：没有检测就会静默漂移。

    只报告，不自动重建。全量重解析加重嵌入比备份贵得多，而这个项目已经两次拒绝隐式的
    昂贵动作（``validate_kubernetes.py`` 的「禁止隐式定时备份」硬检查，以及 V5-5 拒绝
    索引版本自动过期清理）。什么时候重建由操作者决定。

    返回 None 表示没有漂移或无从判断（没有 active 版本、没有登记过嵌入模型）；
    否则返回逐项差异，让操作者知道该不该重建，而不只是「有问题」。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        active = connection.execute(
            """SELECT iv.chunking_version, iv.embedding_model, iv.embedding_dimension,
                      iv.processing_options, iv.config_fingerprint,
                      iv.component_manifest, iv.config_completeness
               FROM knowledge_bases kb
               JOIN index_versions iv ON iv.index_version_id = kb.active_index_version_id
               WHERE kb.knowledge_base_id = %s""",
            (knowledge_base_id,),
        ).fetchone()
        if active is None:
            return None
        registered = connection.execute(
            "SELECT embedding_model, embedding_dimension FROM index_settings WHERE singleton"
        ).fetchone()
    if registered is None:
        return None

    current_chunking = derive_chunking_version(chunk_size, chunk_overlap)
    current_options = {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap}
    components = (
        component_manifest(reranker_model=reranker_model)
        if reranker_model and str(active["config_completeness"]) == "complete"
        else None
    )
    current = config_fingerprint(
        current_chunking,
        str(registered["embedding_model"]),
        int(registered["embedding_dimension"]),
        current_options,
        components,
    )
    if current == str(active["config_fingerprint"]):
        return None

    changes = [
        {"field": field, "active": built_with, "current": now}
        for field, built_with, now in (
            ("chunking_version", str(active["chunking_version"]), current_chunking),
            ("embedding_model", str(active["embedding_model"]), str(registered["embedding_model"])),
            (
                "embedding_dimension",
                int(active["embedding_dimension"]),
                int(registered["embedding_dimension"]),
            ),
            ("processing_options", dict(active["processing_options"] or {}), current_options),
        )
        if built_with != now
    ]
    if components is not None:
        active_components = dict(active["component_manifest"] or {})
        changes.extend(
            {
                "field": key,
                "active": active_components.get(key),
                "current": value,
            }
            for key, value in components.items()
            if active_components.get(key) != value
        )
    return {
        "active_fingerprint": str(active["config_fingerprint"]),
        "current_fingerprint": current,
        # 指纹不同但逐项都相同，说明差异来自 config_fingerprint 里不作为参数暴露的
        # PARSER_SCHEMA_VERSION——解析器升级同样意味着索引是旧的。
        "changes": changes or [{"field": "parser_schema_version", "active": "旧", "current": "已升级"}],
    }


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
    creation_reason: str = "legacy",
    force_reason: str | None = None,
    requested_by: str | None = None,
    creation_idempotency_key: str | None = None,
    config_snapshot: dict[str, Any] | None = None,
    components: dict[str, str] | None = None,
) -> tuple[str, str]:
    """创建一个 building 索引版本，同时冻结配置与输入文档集合，返回两者的 id。

    索引版本 = 配置快照 + 文档快照。两者必须在同一个事务里冻结：先建版本再建快照的话，
    两步之间新增的文档会落进快照却不属于这次构建；反过来也一样。

    每个知识库同时只能有一个 building 版本，由数据库的 partial unique index 保证；
    并发调用会得到 UniqueViolation 而不是两个半成品版本——此时快照随事务一并回滚，
    不会留下没有版本引用的孤儿快照。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        return create_building_version_in_transaction(
            connection,
            knowledge_base_id,
            chunking_version=chunking_version,
            parser_version=parser_version,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
            processing_options=processing_options,
            rebuild_batch_id=rebuild_batch_id,
            creation_reason=creation_reason,
            force_reason=force_reason,
            requested_by=requested_by,
            creation_idempotency_key=creation_idempotency_key,
            config_snapshot=config_snapshot,
            components=components,
        )


def create_building_version_in_transaction(
    connection: psycopg.Connection[Any],
    knowledge_base_id: str,
    *,
    chunking_version: str,
    parser_version: str,
    embedding_model: str,
    embedding_dimension: int,
    processing_options: dict[str, Any],
    rebuild_batch_id: str,
    creation_reason: str,
    force_reason: str | None,
    requested_by: str | None,
    creation_idempotency_key: str | None,
    config_snapshot: dict[str, Any] | None,
    components: dict[str, str] | None,
) -> tuple[str, str]:
    """在调用方事务内原子创建 Document Snapshot 与 building Version。"""

    if creation_reason not in CREATION_REASONS and creation_reason != "legacy":
        raise AppError("INDEX_CREATION_REASON_INVALID", "索引版本创建原因无效。", 400)
    if creation_reason in FORCED_CREATION_REASONS and not (force_reason or "").strip():
        raise AppError("INDEX_FORCE_REASON_REQUIRED", "修复性或主动重建必须填写原因。", 400)
    if connection.execute(
        "SELECT 1 FROM knowledge_bases WHERE knowledge_base_id=%s FOR UPDATE",
        (knowledge_base_id,),
    ).fetchone() is None:
        raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404)
    if creation_idempotency_key:
        existing = connection.execute(
            """SELECT index_version_id, document_snapshot_id FROM index_versions
               WHERE knowledge_base_id=%s AND creation_idempotency_key=%s""",
            (knowledge_base_id, creation_idempotency_key),
        ).fetchone()
        if existing:
            return str(existing["index_version_id"]), str(existing["document_snapshot_id"])

    index_version_id = f"iv_{uuid4().hex[:16]}"
    fingerprint = config_fingerprint(
        chunking_version, embedding_model, embedding_dimension, processing_options, components
    )
    document_snapshot_id = create_snapshot(
        connection,
        knowledge_base_id=knowledge_base_id,
        reason=f"rebuild:{rebuild_batch_id}",
    )
    snapshot = connection.execute(
        "SELECT snapshot_fingerprint FROM document_snapshots WHERE document_snapshot_id=%s",
        (document_snapshot_id,),
    ).fetchone()
    manifest = components or {}
    release = (
        release_fingerprint(
            config_fingerprint_value=fingerprint,
            document_set_fingerprint=str(snapshot["snapshot_fingerprint"]),
            components=manifest,
        )
        if manifest
        else None
    )
    version_no = int(
        connection.execute(
            """SELECT COALESCE(max(version_no),0)+1 AS value FROM index_versions
               WHERE knowledge_base_id=%s""",
            (knowledge_base_id,),
        ).fetchone()["value"]
    )
    snapshot_payload = config_snapshot or {
        "chunking": {"version": chunking_version, **processing_options},
        "parser": {"version": parser_version},
        "embedding": {"model": embedding_model, "dimension": embedding_dimension},
        "processing_options": processing_options,
        "components": manifest,
    }
    connection.execute(
        """INSERT INTO index_versions
           (index_version_id, knowledge_base_id, status, chunking_version, parser_version,
            embedding_model, embedding_dimension, processing_options, config_fingerprint,
            rebuild_batch_id, document_snapshot_id, version_no, creation_reason, force_reason,
            requested_by, creation_idempotency_key, config_snapshot, component_manifest,
            release_fingerprint, config_completeness)
           VALUES (%s, %s, 'building', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   %s, %s, %s, %s, %s, %s)""",
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
            document_snapshot_id,
            version_no,
            creation_reason,
            (force_reason or "").strip() or None,
            requested_by,
            creation_idempotency_key,
            Jsonb(snapshot_payload),
            Jsonb(manifest),
            release,
            "complete" if manifest else "unknown",
        ),
    )
    record_lifecycle_event(
        connection,
        knowledge_base_id=knowledge_base_id,
        index_version_id=index_version_id,
        event_type="created",
        to_status="building",
        actor=Actor(requested_by, "admin") if requested_by else None,
        reason=f"{creation_reason} · 重建批次 {rebuild_batch_id}",
    )
    return index_version_id, document_snapshot_id


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
                      config_fingerprint, evaluation_report_id, validation_report_id,
                      document_snapshot_id, rebuild_batch_id, version_no, creation_reason,
                      force_reason, requested_by, config_snapshot, component_manifest,
                      release_fingerprint, config_completeness,
                      legacy_migrated,
                      created_at, activated_at, retired_at, cleaned_at
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
    reranker_model: str | None = None,
) -> str:
    """返回 active 索引版本；知识库首次索引时创建并直接激活第一个版本。

    **这条路径有意绕过发布门禁，但不绕过审计。** 首版没有前序基线可比，也没有已经在
    服务的索引可以回退，要求它先跑三层门禁等于让用户上传第一份文档后无法检索，直到有人
    手工跑一次评测——那是产品行为倒退。

    代价是它必须留痕：引导创建的版本会同时写一份 ``report_source='bootstrap'`` 的验证
    报告，三层结果一律 ``unknown``。首版确实没经过门禁，标 pass 就是伪造；但报告本身
    必须存在，否则回滚到首版之后就再也切不回来（``switch_to_version`` 要求持有 pass 报告）。
    """

    existing = active_index_version_id(database_url, knowledge_base_id)
    if existing:
        return existing
    index_version_id = f"iv_{uuid4().hex[:16]}"
    components = component_manifest(reranker_model=reranker_model) if reranker_model else None
    fingerprint = config_fingerprint(
        chunking_version, embedding_model, embedding_dimension, processing_options, components
    )
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        knowledge_base = connection.execute(
            """SELECT active_index_version_id FROM knowledge_bases
               WHERE knowledge_base_id=%s FOR UPDATE""",
            (knowledge_base_id,),
        ).fetchone()
        if knowledge_base is None:
            raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404)
        if knowledge_base["active_index_version_id"]:
            return str(knowledge_base["active_index_version_id"])
        version_no = int(
            connection.execute(
                """SELECT COALESCE(max(version_no),0)+1 AS value FROM index_versions
                   WHERE knowledge_base_id=%s""",
                (knowledge_base_id,),
            ).fetchone()["value"]
        )
        config_snapshot = {
            "chunking": {"version": chunking_version, **processing_options},
            "parser": {"version": parser_version},
            "embedding": {"model": embedding_model, "dimension": embedding_dimension},
            "processing_options": processing_options,
            "components": components or {},
        }
        connection.execute(
            """INSERT INTO index_versions
               (index_version_id, knowledge_base_id, status, chunking_version, parser_version,
                embedding_model, embedding_dimension, processing_options, config_fingerprint,
                evaluation_report_id, activated_at, version_no, creation_reason,
                config_snapshot, component_manifest, config_completeness)
               VALUES (%s, %s, 'active', %s, %s, %s, %s, %s, %s, 'initial-index', now(),
                       %s, 'initial_build', %s, %s, %s)""",
            (
                index_version_id,
                knowledge_base_id,
                chunking_version,
                parser_version,
                embedding_model,
                embedding_dimension,
                Jsonb(processing_options),
                fingerprint,
                version_no,
                Jsonb(config_snapshot),
                Jsonb(components or {}),
                "complete" if components else "unknown",
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
        # 只有真正由本次调用创建出来的版本才补报告：并发下另一个 worker 可能先完成引导，
        # 那时 row 指向它创建的版本，报告由它自己写，这里不重复写。
        if row and str(row["active_index_version_id"]) == index_version_id:
            _record_bootstrap_validation(connection, index_version_id)
            record_lifecycle_event(
                connection,
                knowledge_base_id=knowledge_base_id,
                index_version_id=index_version_id,
                event_type="created",
                to_status="active",
                reason="首次资料索引引导创建；未经过正式三层门禁",
            )
    # 并发下另一个 worker 可能先完成引导，此时沿用它创建的版本。
    return (
        str(row["active_index_version_id"])
        if row and row["active_index_version_id"] else index_version_id
    )


def _record_bootstrap_validation(
    connection: psycopg.Connection[Any], index_version_id: str
) -> None:
    """为引导创建的首版留一份 bootstrap 来源的验证报告。

    三层结果是 unknown 而不是 pass：首版没有前序基线、没有输入快照，三层门禁一项也没跑过。
    报告存在是为了让回滚回到首版之后还能再切回来，不是为了宣称它通过了门禁。
    """

    note = "首次索引引导创建，没有前序基线，未经过三层门禁。"
    layer = {"status": "unknown", "checks": [], "note": note}
    connection.execute(
        """INSERT INTO validation_reports
           (validation_report_id, index_version_id, status, policy_version,
            integrity_result, technical_result, retrieval_result, summary,
            report_source, started_at, finished_at)
           VALUES (%s, %s, 'pass', 'bootstrap', %s::jsonb, %s::jsonb, %s::jsonb, %s,
                   'bootstrap', now(), now())
           ON CONFLICT DO NOTHING""",
        (
            (report_id := f"vr_{uuid4().hex[:20]}"),
            index_version_id,
            Jsonb(layer),
            Jsonb(layer),
            Jsonb(layer),
            note,
        ),
    )
    connection.execute(
        """UPDATE index_versions SET validation_report_id = %s
           WHERE index_version_id = %s AND validation_report_id IS NULL""",
        (report_id, index_version_id),
    )


def finalize_building_version(database_url: str, index_version_id: str) -> str:
    """按覆盖完整性把 building 版本推进到 validating 或 build_failed，返回新状态。

    **构建完整只到 validating。** ready 在生产链里只有一个入口——三层门禁通过，
    见 ``index_validation.validate_index_version``。

    分母是"该知识库中 current_version_id 非空的文档数"：尚未成功索引的 pending / failed
    文档本就没有可用分块，把它们计入会让新版本永远无法放行。
    该批次还有 queued / running 任务时不动状态，返回 building；已离开 building 的版本
    只回报当前状态，重复调用因此安全。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            version = connection.execute(
                """SELECT knowledge_base_id, status, rebuild_batch_id, document_snapshot_id
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
            # 分母取文档快照，而不是再查一次 documents。两者的差别正是快照存在的理由：
            # 即时查询会把「建版本之后新增的文档」算进分母，于是一次本已覆盖完整的构建
            # 会因为期间上传了新资料而判为不完整；反过来，期间被删掉的文档会让分母变小，
            # 让一次漏建的构建看起来是完整的。快照锁定的是「这次要建的就是这批」。
            if version["document_snapshot_id"]:
                expected = connection.execute(
                    """SELECT count(*) AS total FROM document_snapshot_members
                       WHERE document_snapshot_id = %s AND inclusion_status = 'included'""",
                    (version["document_snapshot_id"],),
                ).fetchone()
                # 覆盖数同样只认快照成员：期间新上传的文档即使被别的任务建进了这个版本，
                # 也不属于本次输入，不能拿来充抵覆盖率。
                covered = connection.execute(
                    """SELECT count(DISTINCT c.document_version_id) AS total
                       FROM chunks c
                       JOIN document_snapshot_members m
                         ON m.document_version_id = c.document_version_id
                        AND m.document_snapshot_id = %s
                        AND m.inclusion_status = 'included'
                       WHERE c.index_version_id = %s""",
                    (version["document_snapshot_id"], index_version_id),
                ).fetchone()
            else:
                # 快照机制之前创建的版本没有输入快照，只能沿用即时查询。
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
            # 构建完整只让版本进入 validating，**不是 ready**。ready 在生产链里只有一个
            # 入口：三层门禁通过。此前这里直接置 ready，于是「构建完整但从未验证」与
            # 「已验证并上线过」两种版本同形，而 switch_to_version 只看 status='ready'。
            status = "validating" if complete else "build_failed"
            connection.execute(
                "UPDATE index_versions SET status = %s WHERE index_version_id = %s",
                (status, index_version_id),
            )
            # 构建收口由 worker 触发，没有按按钮的人——actor 留空正是「系统自动」的意思。
            record_lifecycle_event(
                connection, knowledge_base_id=str(version["knowledge_base_id"]),
                index_version_id=index_version_id,
                event_type="build_succeeded" if complete else "build_failed",
                from_status="building", to_status=status,
                reason=(
                    None if complete
                    else f"覆盖 {int(covered['total'])}/{int(expected['total'])} 份，"
                         f"失败任务 {int(failed['total'])} 个"
                ),
            )
    if status == "validating":
        # 部分向量索引在构建完成时就建，不等验证：技术门禁要读实际维度，而检索质量
        # 门禁要跑真实查询，两者都需要这个索引已经可用。
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
    audit: AuditRepository | None = None,
    actor: Actor | None = None,
) -> dict[str, object]:
    """把 ready 版本切为 active，原 active 降为 previous，原 previous 转 retired。

    **本函数不再执行质量判定，只核验它已经发生过。** 三层门禁移到了
    ``index_validation.validate_index_version``：判定结果落成不可变的 validation_report，
    版本据此进入 ready。此前判定在这里现场做、依据是调用方传进来的内存对象——库里查不到
    任何一次验证发生过，换个调用方就能拿另一份报告放行。

    质量门仍是**相对比较**：只要求三项指标不相对基线回退，不要求达到冻结的绝对阈值。
    两者回答的是不同问题——绝对阈值（Recall@5 0.70 等）回答"这套系统能否上线"，
    而切换要回答的是"这次换配置是变好还是变坏"。把绝对阈值用作切换门槛会让功能锁死：
    `corpus_v2` 在当前实现下召回阶段 0.6862 未达 0.70，永远产不出 ``passed`` 的报告，
    于是一次索引切换都做不成。该规则现在写在 ``check_retrieval_quality`` 里。

    三条 UPDATE 的顺序不能调整，两条实测理由：one_previous_idx 是非延迟的 partial unique
    index，previous 与 active 并存时先降级原 active 会立刻报
    "duplicate key value violates unique constraint index_versions_one_previous_idx"；
    即便躲开它，退役语句按 ``status = 'previous'`` 匹配，跑在降级之后会把刚降级的版本一并
    退役，回滚路径静默消失。因此先退役原 previous，再降级原 active，最后提升目标。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        scope = connection.execute(
            "SELECT knowledge_base_id FROM index_versions WHERE index_version_id=%s",
            (index_version_id,),
        ).fetchone()
        if scope is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
        # 所有会移动 active / previous 的动作先锁知识库，再锁 Version。候选创建同样先锁
        # knowledge_bases 行，因此并发 Activate、Rollback 与 Create 会按知识库串行执行。
        connection.execute(
            "SELECT active_index_version_id FROM knowledge_bases WHERE knowledge_base_id=%s FOR UPDATE",
            (scope["knowledge_base_id"],),
        ).fetchone()
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
        # 门禁查的是持久化的验证报告，不是调用方递进来的对象。此前这里现场比对
        # 调用方传入的 RetrievalEvaluationReport——报告不落库，换个调用方就能绕过。
        # 现在 ready 本身已经是「三层门禁通过」的证据，这里再核一次报告确实存在且为
        # pass，是为了挡住直接改库把状态置成 ready 的情况。
        validation = connection.execute(
            """SELECT status, index_version_id FROM validation_reports
               WHERE validation_report_id = %s""",
            (target["validation_report_id"],),
        ).fetchone() if target["validation_report_id"] else None
        if validation is None:
            raise AppError(
                "VALIDATION_NOT_PASSED",
                "该索引版本没有验证报告，不能激活。",
                409,
            )
        if str(validation["status"]) != "pass":
            raise AppError(
                "VALIDATION_NOT_PASSED",
                f"验证报告状态为 {validation['status']}，不能激活。",
                409,
            )
        if str(validation["index_version_id"]) != index_version_id:
            raise AppError(
                "VALIDATION_NOT_PASSED",
                "验证报告属于另一个索引版本，不能用它放行本版本。",
                409,
            )
        knowledge_base_id = str(target["knowledge_base_id"])
        retiring = connection.execute(
            """UPDATE index_versions SET status = 'retired', retired_at = now()
               WHERE knowledge_base_id = %s AND status = 'previous'
               RETURNING index_version_id""",
            (knowledge_base_id,),
        ).fetchone()
        if retiring:
            record_lifecycle_event(
                connection, knowledge_base_id=knowledge_base_id,
                index_version_id=str(retiring["index_version_id"]),
                event_type="retired", from_status="previous", to_status="retired",
                actor=actor, reason=f"因激活 {index_version_id} 而退役",
            )
        demoted = connection.execute(
            """UPDATE index_versions SET status = 'previous'
               WHERE knowledge_base_id = %s AND status = 'active'
               RETURNING index_version_id""",
            (knowledge_base_id,),
        ).fetchone()
        connection.execute(
            """UPDATE index_versions
               SET status = 'active', activated_at = now(),
                   evaluation_report_id = COALESCE(
                       (SELECT evaluation_set_version FROM validation_reports
                        WHERE validation_report_id = %s),
                       evaluation_report_id)
               WHERE index_version_id = %s""",
            (target["validation_report_id"], index_version_id),
        )
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id = %s WHERE knowledge_base_id = %s",
            (index_version_id, knowledge_base_id),
        )
        record_lifecycle_event(
            connection, knowledge_base_id=knowledge_base_id,
            index_version_id=index_version_id, event_type="activated",
            from_status="ready", to_status="active", actor=actor,
            validation_report_id=target["validation_report_id"],
        )
        if demoted:
            record_lifecycle_event(
                connection, knowledge_base_id=knowledge_base_id,
                index_version_id=str(demoted["index_version_id"]),
                event_type="deactivated", from_status="active", to_status="previous",
                actor=actor, reason=f"被 {index_version_id} 取代",
            )
    if audit is not None:
        audit.record(
            "index_version.activate",
            actor_id=actor.actor_id if actor else None,
            actor_role=(actor.actor_role if actor else None) or "operator",
            resource_type="index_version",
            resource_id=index_version_id,
            result="success",
        )
    return {
        "knowledge_base_id": knowledge_base_id,
        "active": index_version_id,
        "previous": str(demoted["index_version_id"]) if demoted else "",
        "validation_report_id": str(target["validation_report_id"]),
    }


def rollback_to_previous(
    database_url: str,
    knowledge_base_id: str,
    audit: AuditRepository | None = None,
    actor: Actor | None = None,
    *,
    confirm_content_lag: bool = False,
) -> dict[str, str]:
    """把 previous 切回 active，原 active 退回 ready。

    原 active 退回 ready 而不是 previous——同一知识库只允许一个 previous，且 ready 在
    新状态机里明确表示「已通过三层门禁、可以激活」，正好描述一个刚被换下来的版本。
    它的 validation_report_id 仍然有效，因此想再切回去是一次普通激活，不必重跑评测。

    回滚不要求新报告：目标版本此前已被质量门放行过，其 ``evaluation_report_id`` 仍然有效，
    因此提升它不会违反 index_versions_active_requires_report。原 active 退回 ready 而不是
    previous——同一知识库只允许一个 previous，且它同样是放行过的版本，ready 语义正确。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        if connection.execute(
            "SELECT active_index_version_id FROM knowledge_bases WHERE knowledge_base_id=%s FOR UPDATE",
            (knowledge_base_id,),
        ).fetchone() is None:
            raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404)
        target = connection.execute(
            """SELECT index_version_id, document_snapshot_id FROM index_versions
               WHERE knowledge_base_id = %s AND status = 'previous' FOR UPDATE""",
            (knowledge_base_id,),
        ).fetchone()
        if target is None:
            raise AppError("INDEX_NO_PREVIOUS_VERSION", "没有可回滚的上一索引版本。", 409)
        restored = str(target["index_version_id"])
        # 分块被清理过的版本不能回滚过去——那会把知识库切成空索引，而且切换本身
        # 不会报错，只有用户提问检索不到时才会发现。cleaned 状态就是为了能在这里拦住。
        remaining = connection.execute(
            "SELECT count(*) AS total FROM chunks WHERE index_version_id = %s",
            (restored,),
        ).fetchone()
        if int(remaining["total"]) == 0:
            raise AppError(
                "INDEX_VERSION_ALREADY_CLEANED",
                "上一索引版本的分块已被清理，回滚过去会得到空索引。",
                409,
            )
        current_set = current_document_set(connection, knowledge_base_id)
        snapshot = connection.execute(
            """SELECT snapshot_fingerprint FROM document_snapshots
               WHERE document_snapshot_id=%s""",
            (target["document_snapshot_id"],),
        ).fetchone() if target["document_snapshot_id"] else None
        target_fingerprint = str(snapshot["snapshot_fingerprint"]) if snapshot else None
        content_lag = target_fingerprint != str(current_set["fingerprint"])
        if content_lag and not confirm_content_lag:
            raise AppError(
                "INDEX_ROLLBACK_CONTENT_LAG_CONFIRMATION_REQUIRED",
                "上一索引版本的文档时间点与当前资料集合不同；请先查看差异并显式确认。",
                409,
            )
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
        record_lifecycle_event(
            connection, knowledge_base_id=knowledge_base_id, index_version_id=restored,
            event_type="rolled_back", from_status="previous", to_status="active",
            actor=actor,
            reason=(
                "回滚到上一生效版本；已确认内容时间点差异"
                if content_lag else "回滚到上一生效版本"
            ),
        )
        if demoted:
            record_lifecycle_event(
                connection, knowledge_base_id=knowledge_base_id,
                index_version_id=str(demoted["index_version_id"]),
                event_type="deactivated", from_status="active", to_status="ready",
                actor=actor, reason=f"因回滚到 {restored} 而退下",
            )
    if audit is not None:
        audit.record(
            "index_version.rollback",
            actor_id=actor.actor_id if actor else None,
            actor_role=(actor.actor_role if actor else None) or "operator",
            resource_type="index_version",
            resource_id=restored,
            result="success",
        )
    return {
        "knowledge_base_id": knowledge_base_id,
        "active": restored,
        "demoted": str(demoted["index_version_id"]) if demoted else "",
    }


def retire_version(
    database_url: str,
    index_version_id: str,
    actor: Actor | None = None,
    *,
    knowledge_base_id: str | None = None,
) -> dict[str, str]:
    """显式放弃 ``previous`` 回滚点，但保留物理索引等待后续 Cleanup。

    Retire 与 Cleanup 分开：前者是可回滚性的业务决策，后者才是物理删除。只有
    ``previous`` 可以显式退役，避免把 active、已验证待发布或仍在构建的版本误作废。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        version = connection.execute(
            """SELECT knowledge_base_id, status FROM index_versions
               WHERE index_version_id=%s
                 AND (%s IS NULL OR knowledge_base_id=%s)
               FOR UPDATE""",
            (index_version_id, knowledge_base_id, knowledge_base_id),
        ).fetchone()
        if version is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
        if str(version["status"]) != "previous":
            raise AppError(
                "INDEX_VERSION_NOT_RETIRABLE",
                f"索引版本状态为 {version['status']}，只有 previous 可以退役。",
                409,
            )
        connection.execute(
            """UPDATE index_versions SET status='retired', retired_at=now()
               WHERE index_version_id=%s""",
            (index_version_id,),
        )
        record_lifecycle_event(
            connection,
            knowledge_base_id=str(version["knowledge_base_id"]),
            index_version_id=index_version_id,
            event_type="retired",
            from_status="previous",
            to_status="retired",
            actor=actor,
            reason="操作者明确放弃该回滚点",
        )
    return {
        "knowledge_base_id": str(version["knowledge_base_id"]),
        "index_version_id": index_version_id,
        "status": "retired",
    }


def compare_index_version(
    database_url: str, knowledge_base_id: str, index_version_id: str
) -> dict[str, Any]:
    """比较目标版本与当前 active 的冻结配置、文档快照和实际物理范围。"""

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        target = connection.execute(
            """SELECT iv.*, ds.snapshot_fingerprint AS document_set_fingerprint,
                      ds.included_count, ds.excluded_count, ds.created_at AS snapshot_created_at
               FROM index_versions iv
               LEFT JOIN document_snapshots ds
                 ON ds.document_snapshot_id=iv.document_snapshot_id
               WHERE iv.knowledge_base_id=%s AND iv.index_version_id=%s""",
            (knowledge_base_id, index_version_id),
        ).fetchone()
        if target is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该知识库的索引版本。", 404)
        baseline = connection.execute(
            """SELECT iv.*, ds.snapshot_fingerprint AS document_set_fingerprint,
                      ds.included_count, ds.excluded_count, ds.created_at AS snapshot_created_at
               FROM index_versions iv
               LEFT JOIN document_snapshots ds
                 ON ds.document_snapshot_id=iv.document_snapshot_id
               WHERE iv.knowledge_base_id=%s
                 AND iv.index_version_id<>%s
                 AND iv.status=CASE WHEN %s='active' THEN 'previous' ELSE 'active' END
               LIMIT 1""",
            (knowledge_base_id, index_version_id, target["status"]),
        ).fetchone()

        def _members(snapshot_id: str | None) -> dict[str, str]:
            if not snapshot_id:
                return {}
            rows = connection.execute(
                """SELECT m.document_id, m.content_sha256 AS content_hash
                   FROM document_snapshot_members m
                   WHERE m.document_snapshot_id=%s AND m.inclusion_status='included'""",
                (snapshot_id,),
            ).fetchall()
            return {str(row["document_id"]): str(row["content_hash"]) for row in rows}

        target_members = _members(
            str(target["document_snapshot_id"]) if target["document_snapshot_id"] else None
        )
        baseline_members = _members(
            str(baseline["document_snapshot_id"])
            if baseline and baseline["document_snapshot_id"]
            else None
        )
        actual_rows = connection.execute(
            """SELECT count(*) AS chunks, count(DISTINCT document_version_id) AS documents
               FROM chunks WHERE index_version_id=%s""",
            (index_version_id,),
        ).fetchone()
        retrievable_rows = connection.execute(
            """SELECT count(*) AS chunks, count(DISTINCT c.document_version_id) AS documents
               FROM chunks c
               JOIN documents d
                 ON d.knowledge_base_id=c.knowledge_base_id
                AND d.current_version_id=c.document_version_id
               WHERE c.index_version_id=%s""",
            (index_version_id,),
        ).fetchone()
        current_set = current_document_set(connection, knowledge_base_id)
        current_members = {
            str(item["document_id"]): str(item["document_version_id"])
            for item in current_set["included"]
        }
        target_snapshot_rows = connection.execute(
            """SELECT document_id, document_version_id FROM document_snapshot_members
               WHERE document_snapshot_id=%s AND inclusion_status='included'""",
            (target["document_snapshot_id"],),
        ).fetchall() if target["document_snapshot_id"] else []
        target_snapshot_members = {
            str(row["document_id"]): str(row["document_version_id"])
            for row in target_snapshot_rows
        }
        report_ids = [
            value for value in (
                target["validation_report_id"],
                baseline["validation_report_id"] if baseline else None,
            ) if value
        ]
        report_rows = connection.execute(
            """SELECT validation_report_id, status, policy_version, evaluation_set_version,
                      integrity_result, technical_result, retrieval_result, summary, created_at
               FROM validation_reports WHERE validation_report_id=ANY(%s)""",
            (report_ids,),
        ).fetchall() if report_ids else []
        reports = {str(row["validation_report_id"]): dict(row) for row in report_rows}

    config_fields = (
        "chunking_version",
        "parser_version",
        "embedding_model",
        "embedding_dimension",
        "processing_options",
        "component_manifest",
    )
    config_diff = [
        {
            "field": field,
            "baseline": baseline[field] if baseline else None,
            "target": target[field],
        }
        for field in config_fields
        if baseline is None or baseline[field] != target[field]
    ]
    target_ids, baseline_ids = set(target_members), set(baseline_members)
    updated = sum(
        1
        for document_id in target_ids & baseline_ids
        if target_members[document_id] != baseline_members[document_id]
    )
    current_ids, target_snapshot_ids = set(current_members), set(target_snapshot_members)
    current_updated = sum(
        current_members[document_id] != target_snapshot_members[document_id]
        for document_id in current_ids & target_snapshot_ids
    )
    current_content_diff = {
        "added": len(current_ids - target_snapshot_ids),
        "removed": len(target_snapshot_ids - current_ids),
        "updated": current_updated,
        "unchanged": len(current_ids & target_snapshot_ids) - current_updated,
    }
    has_content_lag = (
        not target["document_snapshot_id"]
        or str(target["document_set_fingerprint"] or "") != str(current_set["fingerprint"])
    )
    return {
        "knowledge_base_id": knowledge_base_id,
        "target_version": dict(target),
        "baseline_version": dict(baseline) if baseline else None,
        "config_diff": config_diff,
        "document_diff": {
            "added": len(target_ids - baseline_ids),
            "removed": len(baseline_ids - target_ids),
            "updated": updated,
            "unchanged": len(target_ids & baseline_ids) - updated,
        },
        "actual_scope": {
            "documents": int(actual_rows["documents"]),
            "chunks": int(actual_rows["chunks"]),
        },
        "current_content": {
            "document_set_fingerprint": str(current_set["fingerprint"]),
            "diff": current_content_diff,
            "retrievable_documents": int(retrievable_rows["documents"]),
            "retrievable_chunks": int(retrievable_rows["chunks"]),
            "requires_confirmation": has_content_lag,
        },
        "validation_comparison": {
            "target": reports.get(str(target["validation_report_id"])),
            "baseline": (
                reports.get(str(baseline["validation_report_id"]))
                if baseline and baseline["validation_report_id"] else None
            ),
        },
        "content_snapshot_note": (
            (
                "目标版本与当前资料集合存在时间点差异："
                f"新增 {current_content_diff['added']}、移除 {current_content_diff['removed']}、"
                f"更新 {current_content_diff['updated']}。回滚不会自动回放这些内容，必须显式确认。"
            )
            if has_content_lag
            else "目标版本的冻结文档集合与当前资料集合一致。"
        ),
    }


def cleanup_version(
    database_url: str, index_version_id: str, actor: Actor | None = None
) -> int:
    """删除 retired / 失败版本的分块与其部分索引，把版本推进到 cleaned。

    **清理之后必须改状态。** 此前这里删完分块就返回，版本仍停在 ``retired``——于是
    「已退役但分块还在、可以回滚回去」与「分块已删光、回滚过去就是空索引」这两种
    截然不同的状态，在库里、页面上和回滚校验里长得完全一样。``cleaned`` 把它们分开，
    回滚因此可以拒绝那些已经没有数据的版本。

    版本记录本身保留，仍是可审计的事实。
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        version = connection.execute(
            "SELECT status FROM index_versions WHERE index_version_id = %s FOR UPDATE",
            (index_version_id,),
        ).fetchone()
        if version is None:
            raise AppError("INDEX_VERSION_NOT_FOUND", "未找到该索引版本。", 404)
        if str(version["status"]) == "cleaned":
            # 已清理过：重复调用不再删一遍，也不报错——清理是幂等的收尾动作。
            return 0
        if str(version["status"]) not in {"retired", "build_failed", "validation_failed"}:
            raise AppError(
                "INDEX_VERSION_IN_USE",
                f"索引版本状态为 {version['status']}，只有 retired、build_failed 或 "
                "validation_failed 可以清理。",
                409,
            )
        deleted = connection.execute(
            "DELETE FROM chunks WHERE index_version_id = %s", (index_version_id,)
        ).rowcount
        # DROP INDEX 与状态推进放在同一个事务：若物理清理失败，Version 仍保持原状态，
        # 不能出现 cleaned 但专属索引资源还在的半成功事实。
        connection.execute(
            sql.SQL("DROP INDEX IF EXISTS {name}").format(
                name=sql.Identifier(_partial_index_name(index_version_id))
            )
        )
        connection.execute(
            """UPDATE index_versions SET status = 'cleaned', cleaned_at = now()
               WHERE index_version_id = %s""",
            (index_version_id,),
        )
        scope = connection.execute(
            "SELECT knowledge_base_id FROM index_versions WHERE index_version_id = %s",
            (index_version_id,),
        ).fetchone()
        record_lifecycle_event(
            connection, knowledge_base_id=str(scope["knowledge_base_id"]),
            index_version_id=index_version_id, event_type="cleaned",
            from_status=str(version["status"]), to_status="cleaned", actor=actor,
            reason=f"删除 {deleted} 个分块",
        )
    return int(deleted)
