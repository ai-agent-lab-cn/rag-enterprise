import json
import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from .config import Settings
from .errors import AppError
from .evidence_gate import (
    EvidenceGateResult,
    UnsupportedRerankerScoreSemantics,
    evaluate_final_evidence,
    evaluate_preliminary_evidence,
)
from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID
from .lexical import LexicalIndexCache
from .models import AnswerGenerator, EmbeddingModel, Reranker
from .modular_rag import (
    DEFAULT_MODULE_REGISTRY,
    DEFAULT_PIPELINE_PROFILES,
    SOCIAL_DIRECT_REPLY,
    ExecutionTrace,
    ModuleRegistry,
    PipelineProfile,
    QueryIntentRouter,
    RAGPolicy,
    capability_manifest,
)
from .prompts import (
    GENERATION_FAILED_ANSWER,
    RETRIEVAL_ONLY_ANSWER,
    AnswerStatus,
    ParsedAnswer,
    build_prompt,
    parse_answer,
)
from .query_understanding import build_query_plan
from .ranking import fuse_query_candidates, rank_candidates, reciprocal_rank_fusion
from .retrieval_access import RetrievalAccessContext, can_retrieve_metadata
from .schemas import DocumentInfo, QueryMetadataFilter, QueryResponse, Source
from .store import RetrievedChunk
from .web_retrieval import SearXNGWebSearchProvider, WebSearchResult

QueryEventCallback = Callable[[str, dict[str, object]], None]

# 完整 RAG 编排：入库、召回、精排、Prompt、生成

# 这些 evidence.* 模块各自有专门的 trace（位置、状态与 metrics 都不同），不能再被
# 通用循环记第二条：重复轨迹会让技术抽屉出现两个结论不一致的同名模块。
_DEDICATED_EVIDENCE_MODULES = frozenset(
    {"evidence.gate", "evidence.fuse", "evidence.preliminary_gate", "evidence.final_gate"}
)

# Web 未执行时的稳定原因码，与 retrieval.web_policy 的 decision 一一对应。
_WEB_SKIP_REASON_CODES = {
    "disabled": "web_search_disabled",
    "provider_unavailable": "web_provider_not_configured",
    "scope_limited": "knowledge_base_scope_locked",
    "no_result": "web_no_search_result",
    "failed": "web_retrieval_failed",
}


def count_uncategorized(candidates: list[RetrievedChunk]) -> int:
    """召回结果里没有分类的条数。

    它进 query_metadata 供人解释「为什么引用了一份没有分类的资料」：无分类资料本就
    应该出现在不带分类过滤的检索里，但看到它的人需要知道这是设计如此，而不是过滤失效。
    """

    return sum(1 for item in candidates if item.metadata.get("category_id") is None)


def _filter_candidates(
    candidates: list[RetrievedChunk],
    filters: QueryMetadataFilter | None,
    access: RetrievalAccessContext | None = None,
) -> list[RetrievedChunk]:
    """所有召回通路共用同一判定，过滤发生在融合和 Rerank 之前。"""

    def matches(candidate: RetrievedChunk) -> bool:
        metadata = candidate.metadata
        if not can_retrieve_metadata(metadata, access):
            return False
        if filters is None:
            return True
        if filters.category_ids and metadata.get("category_id") not in filters.category_ids:
            return False
        if filters.categories and metadata.get("category") not in filters.categories:
            return False
        raw_tags = metadata.get("tags") or []
        if isinstance(raw_tags, str):
            try:
                raw_tags = json.loads(raw_tags)
            except json.JSONDecodeError:
                raw_tags = [raw_tags]
        candidate_tags = set(raw_tags)
        if filters.tags and not candidate_tags.intersection(filters.tags):
            return False
        if filters.source_types and metadata.get("source_type") not in filters.source_types:
            return False
        created_at = metadata.get("created_at")
        if (filters.created_from or filters.created_to) and not created_at:
            return False
        if created_at:
            created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            if filters.created_from and created < filters.created_from:
                return False
            if filters.created_to and created > filters.created_to:
                return False
        return True

    return [candidate for candidate in candidates if matches(candidate)]


class RAGServiceProtocol(Protocol):
    def index_document(
        self,
        filename: str,
        content: bytes,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        metadata: dict[str, object] | None = None,
    ) -> DocumentInfo: ...
    def list_documents(
        self,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        access: RetrievalAccessContext | None = None,
    ) -> list[DocumentInfo]: ...
    def delete_document(
        self, document_id: str, knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID
    ) -> bool: ...
    def update_document_metadata(
        self,
        document_id: str,
        metadata: dict[str, object],
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> bool: ...
    def update_document_acl(
        self,
        document_id: str,
        allow_user_ids: list[str],
        deny_user_ids: list[str],
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> int | None: ...
    def query(
        self,
        question: str,
        retrieve_k: int,
        rerank_k: int,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        filters: QueryMetadataFilter | None = None,
        access: RetrievalAccessContext | None = None,
        event_callback: QueryEventCallback | None = None,
        conversation_history: list[dict[str, object]] | None = None,
        execution_id: str | None = None,
    ) -> QueryResponse: ...
    def list_index_versions(
        self, knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID
    ) -> list[dict[str, object]]: ...


class RAGService:
    def __init__(
        self,
        settings: Settings,
        store: Any,
        embedder: EmbeddingModel,
        reranker: Reranker,
        generator: AnswerGenerator,
        lexical: LexicalIndexCache | None = None,
        policy_repository: Any | None = None,
        web_provider: SearXNGWebSearchProvider | None = None,
        module_registry: ModuleRegistry | None = None,
    ):
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self.generator = generator
        # 未注入词法索引时只走向量召回；离线评测入口按需省略它。
        self.lexical = lexical
        self.policy_repository = policy_repository
        self.web_provider = web_provider
        self.module_registry = module_registry or DEFAULT_MODULE_REGISTRY

    def list_documents(
        self,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        access: RetrievalAccessContext | None = None,
    ) -> list[DocumentInfo]:
        """列出知识库里的资料。

        ``access`` 给了就按检索侧同一套 ACL 判据过滤，用于普通成员的资料视图；
        不给表示管理视图（管理员要能看到并管理受限资料的 ACL，否则一份被 deny 到
        没人可见的资料就再也改不回来了）。

        **过滤必须用 can_retrieve_metadata，不能在这里另写一套。** 清单与检索对
        「谁能看见这份资料」给出不同答案，本身就是漏洞：此前清单侧一条 ACL 判据都没有，
        被 deny 的成员照样拿到整份清单，而 DocumentInfo 里带着 filename、
        owner_user_id、department、sensitivity，以及 allow_user_ids / deny_user_ids
        本身——授权名单原样外泄。「知道有这份文件、它叫什么、归谁、多敏感、谁能看」
        在企业场景里就是泄漏，哪怕正文取不到。
        """

        items = self.store.list_documents(knowledge_base_id)
        if access is not None:
            items = [item for item in items if can_retrieve_metadata(item, access)]
        return [DocumentInfo(**item) for item in items]

    def delete_document(
        self,
        document_id: str,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> bool:
        return self.store.delete_document(document_id, knowledge_base_id)

    def update_document_metadata(
        self,
        document_id: str,
        metadata: dict[str, object],
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> bool:
        return self.store.update_document_metadata(document_id, metadata, knowledge_base_id)

    def update_document_acl(
        self,
        document_id: str,
        allow_user_ids: list[str],
        deny_user_ids: list[str],
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> int | None:
        return self.store.update_document_acl(document_id, allow_user_ids, deny_user_ids, knowledge_base_id)

    def list_index_versions(
        self,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> list[dict[str, object]]:
        """基类没有索引版本概念；pgvector 运行时在子类里覆盖为真实查询。"""

        return []

    def _resolve_category_names(
        self, knowledge_base_id: str, filters: QueryMetadataFilter | None
    ) -> QueryMetadataFilter | None:
        """把按名称的分类过滤换成按分类 ID。

        基类没有分类字典，原样返回；pgvector 运行时覆盖为真实查询。名称匹配看起来
        等价，实则不是：分类改名后资料 metadata 里还留着旧名字，于是新名字查不到、
        旧名字反而查得到，而分类 ID 从不改变。
        """

        return filters

    def retrieve_candidates(
        self,
        question: str,
        embedding: list[float],
        retrieve_k: int,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        retrieval_mode: str | None = None,
        filters: QueryMetadataFilter | None = None,
        access: RetrievalAccessContext | None = None,
    ) -> list[RetrievedChunk]:
        """产出召回候选。在线查询与离线评测共用此方法，避免两个入口得出不同结论。

        hybrid 用 RRF 合并向量与词法两路名次：RRF 只看名次不看分数，因此余弦与 BM25
        的量纲差异无需归一化即可合并。词法独有的候选会补算真实余弦，否则它们的
        ``retrieval_score`` 会留在 0，被后续 ``rank_candidates`` 的归一化压到最低。
        """

        filters = self._resolve_category_names(knowledge_base_id, filters)
        mode = retrieval_mode or self.settings.retrieval_mode
        # 整条召回链只在这里解析一次索引版本，向量、词法与补分共用它。中途发生索引
        # 切换时，本次请求仍完整地读同一个版本，不会出现向量 vN、词法 vN-1 的混合结果。
        index_version_id = self.store.resolve_active_version(knowledge_base_id)
        if mode == "vector" or self.lexical is None:
            return _filter_candidates(
                self.store.query(
                    embedding,
                    retrieve_k,
                    knowledge_base_id,
                    query_text=question,
                    **({"filters": filters} if filters else {}),
                    **({"access": access} if access else {}),
                    index_version_id=index_version_id,
                ),
                filters,
                access,
            )

        hits = self.lexical.get(knowledge_base_id, index_version_id).search(question, retrieve_k)
        current_chunks = (
            self.store.load_current_chunks(
                knowledge_base_id,
                **({"access": access} if access else {}),
                index_version_id=index_version_id,
            )
            if filters or access
            else []
        )
        allowed_ids = {item.chunk_id for item in _filter_candidates(current_chunks, filters, access)}
        if filters or access:
            hits = [hit for hit in hits if hit.chunk_id in allowed_ids]
        lexical_scores = {hit.chunk_id: hit.score for hit in hits}
        if mode == "lexical":
            fused_ids = [hit.chunk_id for hit in hits]
            vector_candidates: list[RetrievedChunk] = []
        else:
            vector_candidates = _filter_candidates(
                self.store.query(
                    embedding,
                    retrieve_k,
                    knowledge_base_id,
                    query_text=question,
                    **({"filters": filters} if filters else {}),
                    **({"access": access} if access else {}),
                    index_version_id=index_version_id,
                ),
                filters,
                access,
            )
            fused_ids = [
                chunk_id
                for chunk_id, _ in reciprocal_rank_fusion(
                    [
                        [item.chunk_id for item in vector_candidates],
                        [hit.chunk_id for hit in hits],
                    ],
                    retrieve_k,
                )
            ]

        by_id = {item.chunk_id: item for item in vector_candidates}
        missing = [chunk_id for chunk_id in fused_ids if chunk_id not in by_id]
        lookup: dict[str, RetrievedChunk] = {}
        scores: dict[str, float] = {}
        if missing:
            wanted = set(missing)
            lookup = {
                item.chunk_id: item
                for item in self.store.load_current_chunks(
                    knowledge_base_id, index_version_id=index_version_id
                )
                if item.chunk_id in wanted
            }
            scores = self.store.score_by_ids(
                missing, embedding, knowledge_base_id, index_version_id=index_version_id
            )

        candidates: list[RetrievedChunk] = []
        for chunk_id in fused_ids:
            if chunk_id in by_id:
                candidate = by_id[chunk_id]
                channels = ("vector", "lexical") if chunk_id in lexical_scores else ("vector",)
                candidates.append(
                    replace(
                        candidate,
                        channels=channels,
                        lexical_score=lexical_scores.get(chunk_id),
                    )
                )
            elif chunk_id in lookup:
                candidates.append(
                    replace(
                        lookup[chunk_id],
                        retrieval_score=scores.get(chunk_id, 0.0),
                        channels=("lexical",),
                        lexical_score=lexical_scores.get(chunk_id),
                    )
                )
        return candidates

    def query(
        self,
        question: str,
        retrieve_k: int,
        rerank_k: int,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        filters: QueryMetadataFilter | None = None,
        access: RetrievalAccessContext | None = None,
        event_callback: QueryEventCallback | None = None,
        conversation_history: list[dict[str, object]] | None = None,
        execution_id: str | None = None,
    ) -> QueryResponse:
        total_started = time.perf_counter()
        trace = ExecutionTrace(execution_id)
        policy = (
            self.policy_repository.get(knowledge_base_id)
            if self.policy_repository is not None
            else RAGPolicy()
        )
        routing_started = time.perf_counter()
        router = QueryIntentRouter(self.generator, policy.intent_confidence_threshold)
        if event_callback:
            event_callback("module_started", {"module_key": "intent.router"})
        routing = router.route(question, conversation_history)
        trace.record(
            "intent.router",
            "1",
            routing_started,
            {"question": question, "history_count": len(conversation_history or [])},
            routing.as_dict(),
            metrics={"confidence": routing.confidence},
            status="degraded" if routing.fallback_used else "succeeded",
            fallback_reason=routing.reason if routing.fallback_used else None,
        )
        if event_callback:
            event_callback("routing_completed", routing.as_dict())
            event_callback(
                "module_completed",
                {
                    "module_key": "intent.router",
                    "status": "degraded" if routing.fallback_used else "succeeded",
                },
            )
        active_index_version_id: str | None = None
        raw_capabilities: dict[str, object] = {}
        if self.policy_repository is not None:
            active_index_version_id, raw_capabilities = self.policy_repository.active_capabilities(
                knowledge_base_id
            )
        elif hasattr(self.store, "resolve_active_version"):
            active_index_version_id = self.store.resolve_active_version(knowledge_base_id)

        if routing.control_outcome != "route" or routing.intent is None:
            # 问候旁路：固定引导语，没有 Embedding、检索、门禁与生成，也不发任何
            # retrieval / rerank / generation 阶段事件。它是成功终态，不是拒答。
            direct_reply = routing.control_outcome == "social"
            answer_status: AnswerStatus = (
                "direct_response" if direct_reply else "insufficient_evidence"
            )
            if direct_reply:
                answer = SOCIAL_DIRECT_REPLY
            elif routing.control_outcome == "clarify":
                answer = "当前问题缺少明确的查询对象，请补充要查询的资料、对象或范围。"
            else:
                answer = "该问题超出当前知识库及受控外部来源的回答范围。"
            if not direct_reply:
                # 问候不记这一条：门禁从未执行，一条 skipped 轨迹只会让技术抽屉显示一次
                # 没发生过的判定。设计稿 3.2 要求问候只留 Router 轨迹。
                trace.record(
                    "evidence.gate",
                    "1",
                    time.perf_counter(),
                    {"control_outcome": routing.control_outcome},
                    {"answer_status": answer_status},
                    status="skipped",
                )
            return QueryResponse(
                answer=answer,
                answer_status=answer_status,
                sources=[],
                model=self.generator.model_name,
                models={
                    "embedding": self.embedder.model_name,
                    "reranker": self.reranker.model_name,
                    "generation": self.generator.model_name,
                },
                latency_ms={"routing": _elapsed(routing_started), "total": _elapsed(total_started)},
                execution_id=trace.execution_id,
                routing=routing.as_dict(),
                policy_snapshot=policy.snapshot(),
                active_index_version_id=active_index_version_id,
                module_executions=trace.as_list(),
                generation_governance={
                    "minimum_evidence_count": policy.minimum_evidence_count,
                    "evidence_count": 0,
                    "acl_revalidated": True,
                    "current_version_revalidated": True,
                    "retrieval_status_revalidated": True,
                    "citation_indices": [],
                    "citation_valid": True,
                    "claim_citation_coverage": True,
                    "outcome_reason": routing.control_outcome,
                },
            )

        profile = DEFAULT_PIPELINE_PROFILES[routing.pipeline_profile or "fact_lookup_v1"]
        # shadow 只记录推荐 Profile，继续执行当前事实查询链；canary/full 才执行差异参数。
        differential_enabled = policy.rollout_stage in {"canary", "full"}
        effective_profile = profile if differential_enabled else DEFAULT_PIPELINE_PROFILES["fact_lookup_v1"]
        if differential_enabled:
            configured_version = policy.profile_versions.get(routing.intent)
            profile_errors = self.module_registry.validate_profile(
                profile, capability_manifest(raw_capabilities)
            )
            if configured_version and configured_version != profile.version:
                profile_errors.append(
                    f"策略要求 Profile 版本 {configured_version}，运行时仅注册 {profile.version}"
                )
            if profile_errors:
                raise AppError(
                    "RAG_PROFILE_INCOMPATIBLE",
                    "当前生效索引不满足所选 RAG 管线的能力要求。",
                    409,
                    {
                        "profile_errors": profile_errors,
                        "execution_id": trace.execution_id,
                        "routing": routing.as_dict(),
                        "pipeline_profile": profile.profile_id,
                        "profile_version": profile.version,
                        "policy_snapshot": policy.snapshot(),
                        "active_index_version_id": active_index_version_id,
                        "module_executions": trace.as_list(),
                    },
                )

        if event_callback:
            event_callback("stage", {"stage": "retrieval", "message": "正在检索资料"})
        retrieval_started = time.perf_counter()
        query_started = time.perf_counter()
        query_plan = build_query_plan(routing.effective_question)
        query_values = _profile_queries(query_plan.queries, effective_profile.intent)
        query_modules = [item for item in effective_profile.modules if item.startswith("query.")]
        for index, query_module in enumerate(query_modules):
            if event_callback:
                event_callback("module_started", {"module_key": query_module})
            trace.record(
                query_module,
                "1",
                query_started,
                routing.effective_question if index == 0 else query_plan.normalized,
                query_plan.normalized if index == 0 else query_values,
            )
            if event_callback:
                event_callback("module_completed", {"module_key": query_module, "status": "succeeded"})
        retrieval_limit = min(
            50,
            retrieve_k * 2 if effective_profile.intent in {"summarize", "compare"} else retrieve_k,
        )
        original_embedding = self.embedder.encode([query_plan.normalized])[0]
        retrieval_module = next(
            item
            for item in effective_profile.modules
            if item.startswith("retrieval.") and item != "retrieval.web_policy"
        )
        if event_callback:
            event_callback("module_started", {"module_key": retrieval_module})
        original_candidates = self.retrieve_candidates(
            query_plan.normalized,
            original_embedding,
            retrieval_limit,
            knowledge_base_id,
            filters=filters,
            access=access,
        )
        query_rankings = [original_candidates]
        fallback_used = False
        for expanded_query in query_values[1:]:
            try:
                expanded_embedding = self.embedder.encode([expanded_query])[0]
                expanded_candidates = self.retrieve_candidates(
                    expanded_query,
                    expanded_embedding,
                    retrieval_limit,
                    knowledge_base_id,
                    filters=filters,
                    access=access,
                )
            except Exception:
                fallback_used = True
                continue
            if expanded_candidates:
                query_rankings.append(expanded_candidates)
            else:
                fallback_used = True
        candidates = (
            fuse_query_candidates(query_rankings, retrieval_limit)
            if len(query_rankings) > 1
            else original_candidates
        )
        trace.record(
            retrieval_module,
            "1",
            retrieval_started,
            query_values,
            [item.chunk_id for item in candidates],
            metrics={"candidate_count": len(candidates)},
            status="degraded" if fallback_used else "succeeded",
            fallback_reason="部分扩展查询失败" if fallback_used else None,
        )
        if event_callback:
            event_callback(
                "module_completed",
                {"module_key": retrieval_module, "status": "degraded" if fallback_used else "succeeded"},
            )

        # 只有 fact_lookup_v1 声明了两段门禁的闭环链；其余三个 Profile 本轮保持现有兼容
        # 轨迹（设计稿 2.2 把它们的差异化执行排除在外），门禁逻辑共用、轨迹键沿用旧名。
        closed_loop = "evidence.final_gate" in effective_profile.modules

        # 门禁前最多执行一次受控补检；只扩大查询，不循环、不绕过 ACL 或 active version。
        # 闭环链**不走这里**：它的全局约束是单次请求最多一轮 KB 检索和一轮 Web 检索，
        # 查询扩展已经在同一轮里合并完。未改造的三个 Profile 继续路由到现有兼容行为
        # （设计稿第 51 行），补检能力必须原样保留。
        supplemental_candidate_count = 0
        if not closed_loop and len(candidates) < policy.minimum_evidence_count:
            supplemental_started = time.perf_counter()
            supplemental_query = _supplemental_query(routing.effective_question, effective_profile.intent)
            if event_callback:
                event_callback("module_started", {"module_key": retrieval_module})
            try:
                supplemental_embedding = self.embedder.encode([supplemental_query])[0]
                supplemental = self.retrieve_candidates(
                    supplemental_query,
                    supplemental_embedding,
                    retrieval_limit,
                    knowledge_base_id,
                    filters=filters,
                    access=access,
                )
                candidates = _merge_candidates(candidates, supplemental)
                supplemental_candidate_count = len(supplemental)
                trace.record(
                    retrieval_module,
                    "1",
                    supplemental_started,
                    supplemental_query,
                    [item.chunk_id for item in supplemental],
                    attempt=2,
                    status="succeeded" if supplemental else "degraded",
                    metrics={"supplemental": True, "candidate_count": len(supplemental)},
                    fallback_reason=None if supplemental else "补检未返回新证据",
                )
            except Exception as exc:
                fallback_used = True
                trace.record(
                    retrieval_module,
                    "1",
                    supplemental_started,
                    supplemental_query,
                    None,
                    attempt=2,
                    status="degraded",
                    error_code="SUPPLEMENTAL_RETRIEVAL_FAILED",
                    error_message=str(exc),
                    fallback_reason="保留首轮检索结果",
                )
            if event_callback:
                event_callback(
                    "module_completed",
                    {"module_key": retrieval_module, "status": "succeeded" if candidates else "degraded"},
                )

        retrieval_ms = _elapsed(retrieval_started)
        # 两条链共用的零候选描述；只有真要抛错时才被读，但计数必须在补检之后定稿。
        no_candidate_metadata: dict[str, object] = {
            "strategy": _query_strategy(query_plan.original, query_plan.normalized, query_values),
            "query_count": len(query_rankings),
            "expansion_count": max(0, len(query_values) - 1),
            "fallback_used": fallback_used,
            "applied_filters": filters.model_dump(mode="json") if filters else None,
            "retrieved_candidate_count": (
                sum(len(items) for items in query_rankings) + supplemental_candidate_count
            ),
            "fused_candidate_count": 0,
            "returned_source_count": 0,
            "filter_match_count": 0 if filters else None,
            "uncategorized_candidate_count": 0,
        }
        if closed_loop and not candidates:
            # 闭环链：Web 只能补充 KB、不能独立支撑答案，所以一条 KB 候选都没有时联网也救
            # 不回来，直接走既有的零候选错误分类，而不是先发起一次注定无用的外部搜索。
            # 未改造的三个 Profile 的判空仍在 Web 之后（见下），改造前"KB 空但 Web 有结果
            # 就用 Web 候选作答"那条路径必须留着。
            self._raise_no_candidates(
                knowledge_base_id,
                filters,
                access,
                no_candidate_metadata,
                trace,
                routing.as_dict(),
                policy,
                active_index_version_id,
                pipeline_profile=effective_profile.profile_id,
                profile_version=effective_profile.version,
            )

        # KB 精排必须早于 Web 决策：门禁读的是这里写回候选的原始 Reranker 分数
        # （rank_candidates 只把归一化值用于排序，不写回），而不是查询内 Min-Max 结果。
        rerank_started = time.perf_counter()
        if event_callback:
            event_callback("stage", {"stage": "rerank", "message": "正在进行相关性排序"})
            if closed_loop:
                event_callback("module_started", {"module_key": "rerank.knowledge_base"})
        kb_ranked: list[RetrievedChunk] = []
        if candidates:
            # 未改造的三个 Profile 完全不消费这里的结果：它们的统一精排用的是
            # `[*candidates, *web_candidates]`（见下方 unified_pool），`ranked` 全部来自那
            # 第二次精排，`kb_ranked` 只在没有 Web 候选时被原样沿用。也就是说这条链上这次
            # 精排的排序结论一定被丢弃，只剩"把原始 rerank_score 写回候选"这个副作用。
            # 这是 R33 为保住与改造前逐字等价**有意付**的代价，不要为省它加条件。
            kb_scores = self.reranker.score(
                routing.effective_question, _rerank_texts(candidates, effective_profile.intent)
            )
            # 在线查询与正式评测共用融合排序，避免两个入口产生不同的质量结论。
            selection_limit = min(max(rerank_k, policy.minimum_evidence_count), len(candidates))
            kb_ranked = rank_candidates(candidates, kb_scores, selection_limit)
        # 空池只可能出现在非闭环链（闭环链上面已经抛错）：改造前那条路径压根不会走到精排，
        # 而 rank_candidates 要求 limit >= 1，照直调用会变成 ValueError。
        rerank_ms = _elapsed(rerank_started)
        if closed_loop:
            trace.record(
                "rerank.knowledge_base",
                "1",
                rerank_started,
                [item.chunk_id for item in candidates],
                [item.chunk_id for item in kb_ranked],
                metrics={"candidate_count": len(candidates), "selected_count": len(kb_ranked)},
            )
            if event_callback:
                event_callback(
                    "module_completed",
                    {"module_key": "rerank.knowledge_base", "status": "succeeded"},
                )

        web_filter_allows = bool(
            filters is None
            or (
                not filters.category_ids
                and not filters.categories
                and not filters.tags
                and filters.created_from is None
                and filters.created_to is None
                and (not filters.source_types or "web" in filters.source_types)
            )
        )
        # 只判断对象是否存在不够：注入的假 Provider 与 base_url 为空的真 Provider 都会
        # 让 search() 静默返回 []，技术抽屉就会把"根本没配"显示成"搜了但没结果"。
        web_provider_ready = bool(
            self.web_provider is not None
            and str(getattr(self.web_provider, "base_url", "") or "").strip()
        )
        web_available = bool(
            policy.web_search_enabled
            and policy.allowed_domains
            and web_provider_ready
            and web_filter_allows
        )

        # Preliminary Gate：只看 KB 证据，决定要不要补一次 Web。它的结论永远不是终局，
        # 无论返回什么都必须继续走到 Final Gate（详见 evidence_gate.GateOutcome）。
        #
        # 只有闭环 Profile 执行门禁。另外三个 Profile 本轮"继续路由到现有兼容行为"
        # （设计稿第 51 行），沿用下面 web_needed 与 evidence.gate 那套数量判据——
        # 把它们顺手升级成相关性 + 引用完整性判定不在本轮范围内，而且零测试覆盖。
        preliminary_gate: EvidenceGateResult | None = None
        if closed_loop:
            preliminary_started = time.perf_counter()
            if event_callback:
                event_callback("module_started", {"module_key": "evidence.preliminary_gate"})
            try:
                preliminary_gate = evaluate_preliminary_evidence(
                    kb_ranked,
                    reranker_model=self.reranker.model_name,
                    minimum_evidence_count=policy.minimum_evidence_count,
                    requires_freshness=routing.requires_freshness,
                    web_available=web_available,
                )
            except UnsupportedRerankerScoreSemantics as exc:
                raise self._reranker_semantics_error(
                    exc, trace, routing.as_dict(), policy, active_index_version_id, effective_profile
                ) from exc
            trace.record(
                "evidence.preliminary_gate",
                "1",
                preliminary_started,
                {
                    "count": len(kb_ranked),
                    "minimum": policy.minimum_evidence_count,
                    "requires_freshness": routing.requires_freshness,
                    "web_available": web_available,
                },
                {"outcome": preliminary_gate.outcome, "sufficient": preliminary_gate.sufficient},
                metrics={
                    "outcome": preliminary_gate.outcome,
                    "sufficient": preliminary_gate.sufficient,
                    "kb_count": preliminary_gate.kb_count,
                    "evidence_count": preliminary_gate.evidence_count,
                    "requires_freshness": routing.requires_freshness,
                    "web_available": web_available,
                    "reason_codes": list(preliminary_gate.reason_codes),
                },
                status="failed" if preliminary_gate.outcome == "reject" else "succeeded",
                error_code="INSUFFICIENT_EVIDENCE" if preliminary_gate.outcome == "reject" else None,
            )
            if event_callback:
                event_callback(
                    "preliminary_gate_completed",
                    {
                        "outcome": preliminary_gate.outcome,
                        "sufficient": preliminary_gate.sufficient,
                        "evidence_count": preliminary_gate.evidence_count,
                        "kb_count": preliminary_gate.kb_count,
                        "requires_freshness": routing.requires_freshness,
                        "web_available": web_available,
                        "reason_codes": list(preliminary_gate.reason_codes),
                    },
                )
                event_callback(
                    "module_completed",
                    {
                        "module_key": "evidence.preliminary_gate",
                        "status": "failed" if preliminary_gate.outcome == "reject" else "succeeded",
                    },
                )

        # Web 决策：开关、Provider、检索范围与门禁结论按固定优先级产生唯一 decision，
        # 轨迹里不再出现一个解释不了自己的 skipped。rollout_stage 不参与判定。
        web_results: list[WebSearchResult] = []
        web_candidates: list[RetrievedChunk] = []
        web_error_message: str | None = None
        web_started = time.perf_counter()
        if preliminary_gate is not None:
            # 走到 not_needed 时 web_available 必为真，而 web_available 为真时初步门禁只会
            # 给出 pass 或 needs_web——另外两个取值 stale / reject 只在 Web 不可用时产生，
            # 已经被下面三个分支按真实原因认领了，不会掉进 not_needed 这个兜底说法。
            web_needed = preliminary_gate.outcome == "needs_web"
        else:
            # 未改造的三个 Profile 沿用原判据：时效需求，或候选数没达到最低证据数。
            # 这里不再看 differential_enabled——非闭环链只可能出现在 canary/full
            # （shadow 下 effective_profile 恒为 fact_lookup_v1），那个条件在此恒真。
            web_needed = bool(
                routing.requires_freshness or len(candidates) < policy.minimum_evidence_count
            )
        if not (policy.web_search_enabled and policy.allowed_domains):
            web_decision = "disabled"
        elif not web_provider_ready:
            web_decision = "provider_unavailable"
        elif not web_filter_allows:
            web_decision = "scope_limited"
        elif not web_needed:
            web_decision = "not_needed"
        else:
            web_decision = "executed"
        if event_callback:
            event_callback("module_started", {"module_key": "retrieval.web_policy"})
        if web_decision == "executed":
            try:
                web_results = list(
                    self.web_provider.search(
                        routing.effective_question,
                        policy.allowed_domains,
                        policy.max_web_results,
                    )
                )
                web_candidates = [_web_chunk(item, knowledge_base_id) for item in web_results]
                if not web_results:
                    web_decision = "no_result"
            except Exception as exc:
                web_error_message = str(exc)
                web_decision = "failed"
        web_attempted = web_decision in {"executed", "no_result", "failed"}
        web_reason_code = _web_reason_code(web_decision, preliminary_gate)
        web_status = (
            "degraded"
            if web_decision == "failed"
            else "succeeded" if web_attempted else "skipped"
        )
        trace.record(
            "retrieval.web_policy",
            "1",
            web_started,
            {
                "decision": web_decision,
                "enabled": policy.web_search_enabled,
                "allowed_domain_count": len(policy.allowed_domains),
                "provider_ready": web_provider_ready,
                "filter_allows_web": web_filter_allows,
                "requires_freshness": routing.requires_freshness,
                "query": routing.effective_question if web_attempted else None,
            },
            [item.url for item in web_results],
            metrics={
                "decision": web_decision,
                "result_count": len(web_results),
                "reason_code": web_reason_code,
            },
            status=web_status,
            error_code="WEB_RETRIEVAL_FAILED" if web_decision == "failed" else None,
            error_message=web_error_message,
            fallback_reason="继续使用知识库证据" if web_decision == "failed" else None,
        )
        # Web 耗时归入检索阶段，避免为一个阶段新增 latency_ms 键。
        retrieval_ms += _elapsed(web_started)
        if event_callback:
            if web_attempted:
                event_callback(
                    "web_retrieval_completed",
                    {
                        "decision": web_decision,
                        "result_count": len(web_results),
                        "reason_code": web_reason_code,
                    },
                )
            event_callback(
                "module_completed",
                {"module_key": "retrieval.web_policy", "status": web_status},
            )

        if not closed_loop and not candidates and not web_candidates:
            # 未改造的三个 Profile 的判空位置：改造前 Web 结果先 extend 进 candidates 再判空，
            # 所以"KB 零候选 + Web 有结果"要继续作答，只有两边都空才抛错。
            self._raise_no_candidates(
                knowledge_base_id,
                filters,
                access,
                no_candidate_metadata,
                trace,
                routing.as_dict(),
                policy,
                active_index_version_id,
                pipeline_profile=effective_profile.profile_id,
                profile_version=effective_profile.version,
                web_error_message=web_error_message if web_decision == "failed" else None,
            )

        # 没有 Web 候选时融合与统一精排都是空操作，但模块必须留在轨迹里并说明原因，
        # 否则前端只能靠"最终有没有 Web 来源"反推联网状态。
        if web_candidates:
            merged = [*kb_ranked, *web_candidates]
            fuse_reason_code: str | None = None
        else:
            merged = kb_ranked
            fuse_reason_code = web_reason_code if web_attempted else "web_not_executed"
        # 改造前的 selection_limit 是「完整 KB 候选池 + Web」一起算出来的，这里逐字复刻：
        # 上面那个 selection_limit 只覆盖纯 KB 池，拿它给含 Web 的集合截断会把补进来的条数
        # 直接抹掉。非闭环链的统一精排与 diversify 共用这一个上限，闭环链不用它。
        compat_selection_limit = min(
            max(rerank_k, policy.minimum_evidence_count), len(candidates) + len(web_candidates)
        )
        if closed_loop:
            fuse_started = time.perf_counter()
            if event_callback:
                event_callback("module_started", {"module_key": "evidence.fuse"})
            trace.record(
                "evidence.fuse",
                "1",
                fuse_started,
                {"kb_count": len(kb_ranked), "web_count": len(web_candidates)},
                [item.chunk_id for item in merged],
                metrics={
                    "kb_count": len(kb_ranked),
                    "web_count": len(web_candidates),
                    "merged_count": len(merged),
                    "reason_code": fuse_reason_code,
                },
                status="skipped" if fuse_reason_code else "succeeded",
            )
            if event_callback:
                event_callback(
                    "module_completed",
                    {
                        "module_key": "evidence.fuse",
                        "status": "skipped" if fuse_reason_code else "succeeded",
                    },
                )

        unified_started = time.perf_counter()
        if web_candidates:
            # 两条链的输入池不同，不能共用。
            #
            # 闭环链：Web 并到 **KB 精排结果** 上。KB 名额已按 rerank_k 收敛过，随后
            # 不再截断（`len(unified_pool)`）——再截一次可能把唯一一条 KB anchor 挤出去，
            # 让 Final Gate 因为"没有 KB 证据"拒掉本该能答的问题；取舍交给门禁的相关性
            # 与引用完整性判据。
            #
            # 未改造的三个 Profile：Web 并到 **未截断的完整 KB 候选池** 上，逐字复刻改造前
            # `candidates.extend(web)` 之后精排一次的口径。不能拿 `merged` 凑合——被 KB 精排
            # 截掉的候选回不来，而且 rank_candidates 的两次 Min-Max（ranking.py:129-130）
            # 区间随池子成分变化，存留候选之间的次序也会变：同一个问题返回的来源就和
            # 改造前不一样了。代价是这条路径上那次 KB 精排的结果被丢弃，白跑一次 Reranker。
            unified_pool = merged if closed_loop else [*candidates, *web_candidates]
            if closed_loop and event_callback:
                event_callback("module_started", {"module_key": "rerank.unified"})
            unified_scores = self.reranker.score(
                routing.effective_question, _rerank_texts(unified_pool, effective_profile.intent)
            )
            unified_limit = len(unified_pool) if closed_loop else compat_selection_limit
            ranked = rank_candidates(unified_pool, unified_scores, unified_limit)
        else:
            ranked = kb_ranked
        rerank_ms += _elapsed(unified_started)
        if closed_loop:
            trace.record(
                "rerank.unified",
                "1",
                unified_started,
                [item.chunk_id for item in merged],
                [item.chunk_id for item in ranked],
                metrics={
                    "candidate_count": len(merged),
                    "selected_count": len(ranked),
                    "reason_code": fuse_reason_code,
                },
                status="skipped" if fuse_reason_code else "succeeded",
            )
            if event_callback:
                event_callback(
                    "module_completed",
                    {
                        "module_key": "rerank.unified",
                        "status": "skipped" if fuse_reason_code else "succeeded",
                    },
                )
        if differential_enabled and effective_profile.intent in {"summarize", "compare"}:
            # 上限同样用含 Web 的那个：改造前这里传的就是"含 Web 的 selection_limit"，
            # 传纯 KB 池那个会把 Web 补进来的名额在 diversify 阶段又截掉一次。
            # 这两个 intent 只可能走非闭环链（closed_loop 恒为 fact_lookup_v1）。
            ranked = _diversify_by_document(ranked, compat_selection_limit)
        if differential_enabled and effective_profile.intent == "procedure":
            ranked = _order_procedure_evidence(ranked)
        evidence_modules = [
            item
            for item in effective_profile.modules
            if item.startswith("evidence.") and item not in _DEDICATED_EVIDENCE_MODULES
        ]
        for evidence_module in evidence_modules:
            evidence_started = time.perf_counter()
            if event_callback:
                event_callback("module_started", {"module_key": evidence_module})
            metrics: dict[str, object] = {
                "selected_count": len(ranked),
                "web_count": len(web_results),
            }
            if evidence_module == "evidence.conflict":
                metrics["conflict_signal_count"] = _conflict_signal_count(ranked)
            trace.record(
                evidence_module,
                "1",
                evidence_started,
                [item.chunk_id for item in candidates],
                [item.chunk_id for item in ranked],
                metrics=metrics,
            )
            if event_callback:
                event_callback("module_completed", {"module_key": evidence_module, "status": "succeeded"})

        # 下面两条分支都会给 evidence / evidence_sufficient / evidence_count / prompt_chunks /
        # source_items 赋值。final_gate 只在闭环链上存在，非闭环链留 None——它是
        # answered_stale 降级的判据入口，在这里声明是为了让分支后的读取有确定语义。
        final_gate: EvidenceGateResult | None = None
        if closed_loop:
            # Final Gate 是闭环链唯一的终局判定：证据够不够不再按候选数量推断，而是逐条核
            # 相关性、引用完整性、KB anchor 与时效。它选中的 selected 是唯一能进 Prompt
            # 和 Sources 的集合，所以 sources 事件必须等它出结论之后才发。
            gate_started = time.perf_counter()
            if event_callback:
                event_callback("module_started", {"module_key": "evidence.final_gate"})
            try:
                final_gate = evaluate_final_evidence(
                    ranked,
                    reranker_model=self.reranker.model_name,
                    minimum_evidence_count=policy.minimum_evidence_count,
                    requires_freshness=routing.requires_freshness,
                    web_executed=web_attempted,
                )
            except UnsupportedRerankerScoreSemantics as exc:
                raise self._reranker_semantics_error(
                    exc, trace, routing.as_dict(), policy, active_index_version_id, effective_profile
                ) from exc
            evidence = list(final_gate.selected)
            evidence_sufficient = final_gate.sufficient
            evidence_count = final_gate.evidence_count
            trace.record(
                "evidence.final_gate",
                "1",
                gate_started,
                {
                    "count": len(ranked),
                    "minimum": policy.minimum_evidence_count,
                    "requires_freshness": routing.requires_freshness,
                    "web_executed": web_attempted,
                },
                {"outcome": final_gate.outcome, "sufficient": final_gate.sufficient},
                metrics={
                    "outcome": final_gate.outcome,
                    "sufficient": final_gate.sufficient,
                    "selected_count": len(evidence),
                    "kb_count": final_gate.kb_count,
                    "web_count": final_gate.web_count,
                    "requires_freshness": routing.requires_freshness,
                    "freshness_verified": final_gate.freshness_verified,
                    "reason_codes": list(final_gate.reason_codes),
                },
                status="succeeded" if final_gate.sufficient else "failed",
                error_code=None if final_gate.sufficient else "INSUFFICIENT_EVIDENCE",
            )
            if event_callback:
                event_callback(
                    "evidence_gate_completed",
                    {
                        "outcome": final_gate.outcome,
                        "sufficient": final_gate.sufficient,
                        "evidence_count": len(final_gate.selected),
                        "kb_count": final_gate.kb_count,
                        "web_count": final_gate.web_count,
                        "requires_freshness": routing.requires_freshness,
                        "freshness_verified": final_gate.freshness_verified,
                        "reason_codes": list(final_gate.reason_codes),
                    },
                )
                event_callback(
                    "module_completed",
                    {
                        "module_key": "evidence.final_gate",
                        "status": "succeeded" if final_gate.sufficient else "failed",
                    },
                )
            prompt_chunks = evidence
            source_items = [_source(item) for item in evidence]
            if event_callback:
                event_callback(
                    "sources", {"items": [item.model_dump(mode="json") for item in source_items]}
                )
        else:
            # 未改造的三个 Profile：数量判据 + evidence.gate 轨迹，模块顺序也保持原样
            # （context.compress 在门禁之前，消费的是 diversify/order 之后的 ranked）。
            prompt_chunks = ranked
            if "context.compress" in effective_profile.modules:
                compress_started = time.perf_counter()
                if event_callback:
                    event_callback("module_started", {"module_key": "context.compress"})
                prompt_chunks = _compress_context(ranked)
                trace.record(
                    "context.compress",
                    "1",
                    compress_started,
                    {"characters": sum(len(item.text) for item in ranked)},
                    {"characters": sum(len(item.text) for item in prompt_chunks)},
                )
                if event_callback:
                    event_callback(
                        "module_completed",
                        {"module_key": "context.compress", "status": "succeeded"},
                    )

            source_items = [_source(item) for item in ranked]
            if event_callback:
                event_callback(
                    "sources", {"items": [item.model_dump(mode="json") for item in source_items]}
                )

            gate_started = time.perf_counter()
            if event_callback:
                event_callback("module_started", {"module_key": "evidence.gate"})
            # 原判据，只把 Task 3 改名的 requires_web 换成 requires_freshness。
            # differential_enabled 那一项去掉了：这条路径只在 canary/full 出现，它恒真。
            web_requirement_satisfied = bool(not routing.requires_freshness or web_results)
            evidence_sufficient = (
                len(ranked) >= policy.minimum_evidence_count and web_requirement_satisfied
            )
            evidence = ranked
            evidence_count = len(ranked)
            trace.record(
                "evidence.gate",
                "1",
                gate_started,
                {"count": len(ranked), "minimum": policy.minimum_evidence_count},
                {"sufficient": evidence_sufficient},
                status="succeeded" if evidence_sufficient else "failed",
                error_code=(
                    None
                    if evidence_sufficient
                    else "WEB_EVIDENCE_REQUIRED"
                    if not web_requirement_satisfied
                    else "INSUFFICIENT_EVIDENCE"
                ),
            )
            if event_callback:
                event_callback(
                    "evidence_gate_completed",
                    {"sufficient": evidence_sufficient, "evidence_count": len(ranked)},
                )
                event_callback(
                    "module_completed",
                    {
                        "module_key": "evidence.gate",
                        "status": "succeeded" if evidence_sufficient else "failed",
                    },
                )

        if not evidence_sufficient:
            return QueryResponse(
                answer="现有知识库和受控外部来源的证据不足，无法可靠回答该问题。",
                answer_status="insufficient_evidence",
                sources=source_items,
                model=self.generator.model_name,
                models={
                    "embedding": self.embedder.model_name,
                    "reranker": self.reranker.model_name,
                    "generation": self.generator.model_name,
                },
                latency_ms={
                    "routing": _elapsed(routing_started),
                    "retrieval": retrieval_ms,
                    "rerank": rerank_ms,
                    "total": _elapsed(total_started),
                },
                execution_id=trace.execution_id,
                routing=routing.as_dict(),
                pipeline_profile=effective_profile.profile_id,
                profile_version=effective_profile.version,
                active_index_version_id=active_index_version_id,
                policy_snapshot=policy.snapshot(),
                module_executions=trace.as_list(),
                generation_governance={
                    "minimum_evidence_count": policy.minimum_evidence_count,
                    # 闭环链拒答时 selected 为空，但"查到了多少条合格证据仍然不够"要说得
                    # 出来，所以这里用门禁数到的合格数而不是 len(evidence)。
                    "evidence_count": evidence_count,
                    "acl_revalidated": True,
                    "current_version_revalidated": True,
                    "retrieval_status_revalidated": True,
                    "citation_indices": [],
                    "citation_valid": True,
                    "claim_citation_coverage": True,
                    "outcome_reason": "INSUFFICIENT_EVIDENCE",
                },
            )

        # 判据只有 Final Gate，不从 len(sources) / web_decision / requires_freshness 另算一遍。
        # 非闭环链的 final_gate 恒为 None，那三个未改造 Profile 拿到的仍是原提示词。
        freshness_unverified = final_gate is not None and final_gate.outcome == "stale"
        prompt = build_prompt(
            routing.effective_question,
            prompt_chunks,
            effective_profile.intent,
            freshness_unverified=freshness_unverified,
        )
        generation_module = next(
            item
            for item in effective_profile.modules
            if item.startswith("generation.") and item != "generation.verify"
        )
        generation_started = time.perf_counter()
        if event_callback:
            event_callback("stage", {"stage": "generation", "message": "正在生成答案"})
            event_callback("module_started", {"module_key": generation_module})
            parsed_answer, generation_metadata = self._generate_answer_stream(
                prompt.text, len(evidence), event_callback, freshness_unverified
            )
        else:
            parsed_answer, generation_metadata = self._generate_answer(
                prompt.text, len(evidence), freshness_unverified
            )
        generation_ms = _elapsed(generation_started)
        # 时效降级由门禁决定，模型不参与：只有通过引用校验的 answered 才改写成
        # answered_stale。generation_failed / retrieval_only / source_conflict 保持原状——
        # 它们各自已经说明了问题，盖成"有答案但时效未验证"就是伪装成功。
        answer_status: AnswerStatus = parsed_answer.status
        if freshness_unverified and parsed_answer.status == "answered":
            answer_status = "answered_stale"
        used_generation_model = generation_metadata.get("configured_model")
        if not isinstance(used_generation_model, str):
            used_generation_model = self.generator.model_name
        model_metadata = _model_metadata(generation_metadata, used_generation_model)
        trace.record(
            generation_module,
            "1",
            generation_started,
            {"prompt_hash": prompt.sha256, "evidence_count": len(evidence)},
            {"answer_status": parsed_answer.status},
            status="failed" if parsed_answer.status == "generation_failed" else "succeeded",
            error_code=parsed_answer.error_code,
            error_message=parsed_answer.error_message,
        )
        if event_callback:
            event_callback(
                "module_completed",
                {
                    "module_key": generation_module,
                    "status": "failed" if parsed_answer.status == "generation_failed" else "succeeded",
                },
            )
        verify_started = time.perf_counter()
        if event_callback:
            event_callback("module_started", {"module_key": "generation.verify"})
        verified = parsed_answer.citation_valid and parsed_answer.claim_citation_coverage
        trace.record(
            "generation.verify",
            "1",
            verify_started,
            {"answer_status": parsed_answer.status},
            {
                "citation_valid": parsed_answer.citation_valid,
                "claim_coverage": parsed_answer.claim_citation_coverage,
            },
            status="succeeded" if verified else "failed",
            error_code=None if verified else (parsed_answer.error_code or "GENERATION_VERIFICATION_FAILED"),
        )
        if event_callback:
            event_callback(
                "generation_verified",
                {
                    "citation_valid": parsed_answer.citation_valid,
                    "claim_citation_coverage": parsed_answer.claim_citation_coverage,
                },
            )
            event_callback(
                "module_completed",
                {"module_key": "generation.verify", "status": "succeeded" if verified else "failed"},
            )
        return QueryResponse(
            answer=parsed_answer.answer,
            answer_status=answer_status,
            error_code=parsed_answer.error_code,
            error_message=parsed_answer.error_message,
            sources=source_items,
            model=used_generation_model,
            models={
                "embedding": self.embedder.model_name,
                "reranker": self.reranker.model_name,
                "generation": used_generation_model,
            },
            model_metadata=model_metadata,
            prompt_version=prompt.version,
            prompt_hash=prompt.sha256,
            query_metadata={
                "strategy": _query_strategy(query_plan.original, query_plan.normalized, query_values),
                "query_count": len(query_rankings),
                "expansion_count": max(0, len(query_values) - 1),
                "fallback_used": fallback_used,
                "applied_filters": filters,
                # 闭环链不做第二轮补检，那一项恒为 0；未改造的三个 Profile 仍按
                # "首轮 + 补检"计数，与改造前一致。
                "retrieved_candidate_count": (
                    sum(len(items) for items in query_rankings) + supplemental_candidate_count
                ),
                # 下面三个统计口径都只覆盖 KB 候选：Web 候选不再混进 candidates 列表，
                # uncategorized_candidate_count 也不会再把 Web 结果算成"无分类资料"。
                "fused_candidate_count": len(candidates),
                "returned_source_count": len(evidence),
                "filter_match_count": len(candidates) if filters else None,
                "uncategorized_candidate_count": count_uncategorized(candidates),
            },
            generation_governance={
                "minimum_evidence_count": policy.minimum_evidence_count,
                "evidence_count": evidence_count,
                # 候选进入门禁前已经通过统一 ACL、当前版本、有效期和检索状态过滤。
                "acl_revalidated": True,
                "current_version_revalidated": True,
                "retrieval_status_revalidated": True,
                "citation_indices": list(parsed_answer.citation_indices),
                "citation_valid": parsed_answer.citation_valid,
                "claim_citation_coverage": parsed_answer.claim_citation_coverage,
                # 用降级后的状态：正常路径两者相等，stale 时这里写 answered_stale，
                # 执行详情不会出现"状态是 answered_stale、原因写着 answered"。
                "outcome_reason": parsed_answer.error_code or answer_status,
            },
            latency_ms={
                "routing": _elapsed(routing_started),
                "retrieval": retrieval_ms,
                "rerank": rerank_ms,
                "generation": generation_ms,
                "total": _elapsed(total_started),
            },
            execution_id=trace.execution_id,
            routing=routing.as_dict(),
            pipeline_profile=effective_profile.profile_id,
            profile_version=effective_profile.version,
            active_index_version_id=active_index_version_id,
            policy_snapshot=policy.snapshot(),
            module_executions=trace.as_list(),
        )

    def _raise_no_candidates(
        self,
        knowledge_base_id: str,
        filters: QueryMetadataFilter | None,
        access: RetrievalAccessContext | None,
        query_metadata: dict[str, object],
        trace: ExecutionTrace,
        routing: dict[str, object],
        policy: RAGPolicy,
        active_index_version_id: str | None,
        *,
        pipeline_profile: str,
        profile_version: str,
        web_error_message: str | None = None,
    ) -> None:
        """零候选的稳定错误分类。

        两条链的调用点不同，``web_error_message`` 也只对其中一条有意义：
        闭环链在 KB 检索之后、Web 之前就判空（Web 补充不了一条 KB 证据都没有的问题），
        永远传 ``None``；未改造的三个 Profile 沿用改造前的位置，在 Web 之后判空，
        KB 空且 Web 又抛异常时用下面这条 503 解释，而不是笼统说知识库没资料。
        """

        details: dict[str, object] = {
            "query_metadata": query_metadata,
            "execution_id": trace.execution_id,
            "routing": routing,
            "pipeline_profile": pipeline_profile,
            "profile_version": profile_version,
            "policy_snapshot": policy.snapshot(),
            "active_index_version_id": active_index_version_id,
            "module_executions": trace.as_list(),
        }
        if web_error_message:
            raise AppError(
                "WEB_RETRIEVAL_FAILED",
                "知识库证据不足，受控 Web 检索暂时不可用。",
                503,
                {
                    **details,
                    "bad_case_category": "web_retrieval_failed",
                    "web_error_message": web_error_message,
                },
            )
        documents = self.store.list_documents(knowledge_base_id)
        indexed_documents = [
            item
            for item in documents
            if item.get("status") == "ready" and int(item.get("chunk_count", 0)) > 0
        ]
        processing_documents = any(item.get("status") in {"pending", "indexing"} for item in documents)
        visible_documents = [item for item in indexed_documents if can_retrieve_metadata(item, access)]
        if not indexed_documents and processing_documents:
            raise AppError(
                "DOCUMENTS_PROCESSING",
                "当前资料仍在处理，请稍后重试。",
                409,
                {**details, "bad_case_category": "documents_processing"},
            )
        if access is not None and indexed_documents and not visible_documents:
            raise AppError(
                "NO_AUTHORIZED_DOCUMENTS",
                "当前权限范围内没有可检索资料。",
                403,
                {**details, "bad_case_category": "acl_no_visible_documents"},
            )
        if filters and indexed_documents:
            raise AppError(
                "NO_MATCHING_DOCUMENTS",
                "没有符合当前过滤条件的资料，请调整分类、标签或来源范围。",
                409,
                {**details, "bad_case_category": "metadata_filter_no_match"},
            )
        if documents:
            raise AppError(
                "NO_RETRIEVABLE_DOCUMENTS",
                "当前资料尚不可检索，请检查处理状态。",
                409,
                {**details, "bad_case_category": "no_retrievable_documents"},
            )
        raise AppError(
            "NO_DOCUMENTS",
            "知识库为空，请先上传文档。",
            409,
            {**details, "bad_case_category": "knowledge_base_empty"},
        )

    def _reranker_semantics_error(
        self,
        exc: UnsupportedRerankerScoreSemantics,
        trace: ExecutionTrace,
        routing: dict[str, object],
        policy: RAGPolicy,
        active_index_version_id: str | None,
        profile: PipelineProfile,
    ) -> AppError:
        """读不懂 Reranker 分数语义时的稳定配置错误。

        门禁不能因为看不懂分数就跳过相关性检查（设计稿第 163 行），所以这里把它变成与
        canary/full 阶段 Profile 校验同一个错误码，details 也凑齐同一批字段——
        ``main.py`` 的失败记录路径按这些字段落库，少一个就少一段执行上下文。
        """

        return AppError(
            "RAG_PROFILE_INCOMPATIBLE",
            "当前生效索引不满足所选 RAG 管线的能力要求。",
            409,
            {
                "profile_errors": [f"未登记 Reranker 分数语义：{exc.reranker_model}"],
                "execution_id": trace.execution_id,
                "routing": routing,
                "pipeline_profile": profile.profile_id,
                "profile_version": profile.version,
                "policy_snapshot": policy.snapshot(),
                "active_index_version_id": active_index_version_id,
                "module_executions": trace.as_list(),
            },
        )

    def _generate_answer(
        self,
        prompt: str,
        source_count: int,
        freshness_unverified: bool = False,
    ) -> tuple[ParsedAnswer, dict[str, object]]:
        if not getattr(self.generator, "ready", True):
            return ParsedAnswer("retrieval_only", RETRIEVAL_ONLY_ANSWER), {}

        try:
            raw_answer, metadata = self.generator.generate(prompt)
        except AppError as exc:
            if exc.code not in {
                "MODEL_REGION_UNSUPPORTED",
                "MODEL_QUOTA_EXHAUSTED",
                "MODEL_AUTH_FAILED",
                "MODEL_RATE_LIMITED",
                "MODEL_TIMEOUT",
                "MODEL_NOT_FOUND",
                "MODEL_UNAVAILABLE",
            }:
                raise
            return (
                ParsedAnswer(
                    "generation_failed",
                    GENERATION_FAILED_ANSWER,
                    exc.code,
                    exc.message,
                ),
                dict(exc.details) if isinstance(exc.details, dict) else {},
            )
        return parse_answer(raw_answer, source_count, freshness_unverified), metadata

    def _generate_answer_stream(
        self,
        prompt: str,
        source_count: int,
        event_callback: QueryEventCallback,
        freshness_unverified: bool = False,
    ) -> tuple[ParsedAnswer, dict[str, object]]:
        if not getattr(self.generator, "ready", True):
            return ParsedAnswer("retrieval_only", RETRIEVAL_ONLY_ANSWER), {}
        try:
            chunks: list[str] = []
            for chunk in self.generator.generate_stream(prompt):
                chunks.append(chunk)
                event_callback("heartbeat", {})
            raw_answer = "".join(chunks)
        except AppError as exc:
            if exc.code not in {
                "MODEL_REGION_UNSUPPORTED",
                "MODEL_QUOTA_EXHAUSTED",
                "MODEL_AUTH_FAILED",
                "MODEL_RATE_LIMITED",
                "MODEL_TIMEOUT",
                "MODEL_NOT_FOUND",
                "MODEL_UNAVAILABLE",
            }:
                raise
            details = dict(exc.details) if isinstance(exc.details, dict) else {}
            return ParsedAnswer("generation_failed", GENERATION_FAILED_ANSWER, exc.code, exc.message), details

        event_callback("stage", {"stage": "governance", "message": "正在校验引用"})
        parsed = parse_answer(raw_answer, source_count, freshness_unverified)
        provider_status = "unknown"
        for status in ("ANSWERED", "INSUFFICIENT_EVIDENCE", "SOURCE_CONFLICT"):
            if raw_answer.lstrip().startswith(f"[STATUS: {status}]"):
                provider_status = status.lower()
                break
        metadata: dict[str, object] = {
            "provider": getattr(self.generator, "provider_name", "unknown"),
            "configured_model": self.generator.model_name,
            "provider_decision": provider_status,
            "effective_evidence_count": source_count,
            "governance_decision": parsed.status,
        }
        if parsed.status in {"answered", "source_conflict"}:
            for sentence in _answer_sentences(parsed.answer):
                event_callback("answer_delta", {"text": sentence})
        else:
            event_callback("replace", {"answer": parsed.answer, "answer_status": parsed.status})
        return parsed, metadata


def _model_metadata(
    response_metadata: dict[str, object],
    configured_model: str,
) -> dict[str, str | int | float | bool]:
    """只保留可复现且体积稳定的生成元数据，不保存供应商原始响应或 Prompt。"""

    response_model = response_metadata.get("configured_model")
    metadata: dict[str, str | int | float | bool] = {
        "configured_model": response_model if isinstance(response_model, str) else configured_model
    }
    for source_key, target_key in (
        ("provider", "provider"),
        ("model_version", "model_version"),
        ("response_id", "response_id"),
        ("provider_decision", "provider_decision"),
        ("effective_evidence_count", "effective_evidence_count"),
        ("governance_decision", "governance_decision"),
    ):
        value = response_metadata.get(source_key)
        if isinstance(value, (str, int, float, bool)):
            metadata[target_key] = value
    return metadata


def _answer_sentences(answer: str) -> list[str]:
    """将已通过最终治理的答案切成适合 SSE 展示的完整句子。"""
    parts = re.findall(r".*?(?:[。！？；]\s*|\n+|$)", answer, flags=re.S)
    return [part for part in parts if part]


def _elapsed(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _source(item: RetrievedChunk) -> Source:
    metadata = item.metadata
    page = metadata.get("page")
    return Source(
        chunk_id=item.chunk_id,
        knowledge_base_id=str(metadata.get("knowledge_base_id", DEFAULT_KNOWLEDGE_BASE_ID)),
        document_id=str(metadata.get("document_id", "unknown")),
        filename=str(metadata.get("filename", "unknown")),
        page=int(page) if page is not None else None,
        paragraph=int(metadata.get("paragraph", 0)),
        chunk_index=int(metadata.get("chunk_index", 0)),
        char_count=int(metadata.get("char_count", len(item.text))),
        summary=str(metadata.get("summary", item.text[:80])),
        text=item.text,
        retrieval_score=item.retrieval_score,
        rerank_score=item.rerank_score,
        retrieval_channels=list(item.channels),
        lexical_score=item.lexical_score,
        retrieval_methods=(
            [] if metadata.get("evidence_source_type") == "web" else item.retrieval_methods or ["vector"]
        ),
        query_match_count=item.query_match_count,
        document_version_id=(
            str(metadata["document_version_id"]) if metadata.get("document_version_id") else None
        ),
        content_sha256=(str(metadata["content_sha256"]) if metadata.get("content_sha256") else None),
        heading_path=list(metadata.get("heading_path") or []),
        sheet_name=(str(metadata["sheet_name"]) if metadata.get("sheet_name") else None),
        row_start=(int(metadata["row_start"]) if metadata.get("row_start") is not None else None),
        row_end=(int(metadata["row_end"]) if metadata.get("row_end") is not None else None),
        column_start=(int(metadata["column_start"]) if metadata.get("column_start") is not None else None),
        column_end=(int(metadata["column_end"]) if metadata.get("column_end") is not None else None),
        source_url=(str(metadata["source_url"]) if metadata.get("source_url") else None),
        external_resource_id=(
            str(metadata["external_resource_id"]) if metadata.get("external_resource_id") else None
        ),
        evidence_source_type=("web" if metadata.get("evidence_source_type") == "web" else "knowledge_base"),
        retrieved_at=metadata.get("retrieved_at"),
    )


def _web_chunk(item: WebSearchResult, knowledge_base_id: str) -> RetrievedChunk:
    chunk_id = f"web_{item.content_sha256[:20]}"
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=item.content,
        metadata={
            "knowledge_base_id": knowledge_base_id,
            "document_id": chunk_id,
            "filename": item.title,
            "paragraph": 0,
            "chunk_index": 0,
            "char_count": len(item.content),
            "summary": item.snippet or item.content[:160],
            "source_url": item.url,
            "content_sha256": item.content_sha256,
            "evidence_source_type": "web",
            "retrieved_at": item.retrieved_at,
        },
        retrieval_score=round(1 / (60 + item.rank), 8),
        channels=("web",),
        retrieval_methods=[],
    )


def _diversify_by_document(candidates: list[RetrievedChunk], limit: int) -> list[RetrievedChunk]:
    """按文档轮询选择证据，避免总结/对比被单一文档的相邻 Chunk 占满。"""

    buckets: dict[str, list[RetrievedChunk]] = {}
    order: list[str] = []
    for item in candidates:
        key = str(item.metadata.get("document_id") or item.chunk_id)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(item)
    selected: list[RetrievedChunk] = []
    while len(selected) < limit:
        added = False
        for key in order:
            if buckets[key]:
                selected.append(buckets[key].pop(0))
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
    return selected


def _rerank_texts(candidates: list[RetrievedChunk], intent: str) -> list[str]:
    """精排输入文本。KB 精排与 Web 之后的统一精排必须用同一套拼法，否则两轮分数不可比。"""

    return [
        (
            f"{' / '.join(candidate.metadata.get('heading_path') or [])}\n{candidate.text}"
            if intent == "procedure" and candidate.metadata.get("heading_path")
            else candidate.text
        )
        for candidate in candidates
    ]


def _web_reason_code(decision: str, preliminary: EvidenceGateResult | None) -> str | None:
    """``retrieval.web_policy`` 每个 decision 对应的稳定原因码。

    ``preliminary`` 为 ``None`` 表示这条链没有初步门禁（三个未改造的 Profile）。此时
    ``not_needed`` 只有"候选数够且不要求时效"这一种含义，decision 本身已经说完，
    不为它另造一个门禁没产出过的原因码。
    """

    if decision == "executed":
        return None
    if decision == "not_needed":
        # 不需要联网的判断来自门禁，原因就用门禁自己的词汇，不在这里另起一套说法。
        if preliminary is None or not preliminary.reason_codes:
            return None
        return preliminary.reason_codes[0]
    return _WEB_SKIP_REASON_CODES[decision]


def _profile_queries(queries: tuple[str, ...], intent: str) -> list[str]:
    if not queries:
        return []
    if intent == "summarize":
        return [queries[0]]
    values = list(queries)
    if intent == "compare":
        match = re.search(
            r"(.{2,60}?)(?:和|与|同|对比|比较|\bvs\.?\b)(.{2,60})",
            queries[0],
            flags=re.IGNORECASE,
        )
        if match:
            for value in (match.group(1).strip(), match.group(2).strip()):
                if value and value not in values:
                    values.append(value)
    return values[:4]


def _query_strategy(original: str, normalized: str, queries: list[str]) -> str:
    if len(queries) > 1:
        return "controlled_expansion"
    return "normalized" if original.strip() != normalized else "original"


def _supplemental_query(question: str, intent: str) -> str:
    suffix = {
        "fact_lookup": "相关事实与依据",
        "summarize": "主要主题 限制 风险",
        "compare": "比较对象 相同维度 差异",
        "procedure": "前置条件 操作步骤 失败处理",
    }.get(intent, "相关依据")
    return f"{question} {suffix}"[:2000]


def _merge_candidates(
    primary: list[RetrievedChunk], supplemental: list[RetrievedChunk]
) -> list[RetrievedChunk]:
    merged = list(primary)
    seen = {item.chunk_id for item in primary}
    for item in supplemental:
        if item.chunk_id not in seen:
            merged.append(item)
            seen.add(item.chunk_id)
    return merged


def _order_procedure_evidence(candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
    return sorted(
        candidates,
        key=lambda item: (
            str(item.metadata.get("document_id") or ""),
            int(item.metadata.get("page") or 0),
            int(item.metadata.get("paragraph") or 0),
            int(item.metadata.get("chunk_index") or 0),
        ),
    )


def _compress_context(candidates: list[RetrievedChunk], max_chars: int = 4_000) -> list[RetrievedChunk]:
    return [
        replace(item, text=f"{item.text[:max_chars].rstrip()}…") if len(item.text) > max_chars else item
        for item in candidates
    ]


def _conflict_signal_count(candidates: list[RetrievedChunk]) -> int:
    pattern = re.compile(r"冲突|不一致|相反|然而|但是|并非|不支持")
    return sum(bool(pattern.search(item.text)) for item in candidates)
