import json
import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol

from .config import Settings
from .errors import AppError
from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID
from .lexical import LexicalIndexCache
from .models import AnswerGenerator, EmbeddingModel, Reranker
from .modular_rag import (
    DEFAULT_MODULE_REGISTRY,
    DEFAULT_PIPELINE_PROFILES,
    ExecutionTrace,
    ModuleRegistry,
    QueryIntentRouter,
    RAGPolicy,
    capability_manifest,
)
from .prompts import (
    GENERATION_FAILED_ANSWER,
    RETRIEVAL_ONLY_ANSWER,
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


def count_uncategorized(candidates: list[RetrievedChunk]) -> int:
    """召回结果里没有分类的条数。

    它进 query_metadata 供人解释「为什么引用了一份没有分类的资料」：无分类资料本就
    应该出现在不带分类过滤的检索里，但看到它的人需要知道这是设计如此，而不是过滤失效。
    """

    return sum(1 for item in candidates if item.metadata.get("category_id") is None)


def _filter_candidates(
    candidates: list[RetrievedChunk], filters: QueryMetadataFilter | None,
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
        self, filename: str, content: bytes, knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
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
        self, document_id: str, metadata: dict[str, object],
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> bool: ...
    def update_document_acl(
        self, document_id: str, allow_user_ids: list[str], deny_user_ids: list[str],
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
        return self.store.update_document_acl(
            document_id, allow_user_ids, deny_user_ids, knowledge_base_id
        )

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
            return _filter_candidates(self.store.query(
                embedding, retrieve_k, knowledge_base_id, query_text=question,
                **({"filters": filters} if filters else {}),
                **({"access": access} if access else {}),
                index_version_id=index_version_id,
            ), filters, access)

        hits = self.lexical.get(knowledge_base_id, index_version_id).search(question, retrieve_k)
        current_chunks = self.store.load_current_chunks(
            knowledge_base_id, **({"access": access} if access else {}),
            index_version_id=index_version_id,
        ) if filters or access else []
        allowed_ids = {item.chunk_id for item in _filter_candidates(current_chunks, filters, access)}
        if filters or access:
            hits = [hit for hit in hits if hit.chunk_id in allowed_ids]
        lexical_scores = {hit.chunk_id: hit.score for hit in hits}
        if mode == "lexical":
            fused_ids = [hit.chunk_id for hit in hits]
            vector_candidates: list[RetrievedChunk] = []
        else:
            vector_candidates = _filter_candidates(self.store.query(
                embedding, retrieve_k, knowledge_base_id, query_text=question,
                **({"filters": filters} if filters else {}),
                **({"access": access} if access else {}),
                index_version_id=index_version_id,
            ), filters, access)
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
                {"module_key": "intent.router", "status": "degraded" if routing.fallback_used else "succeeded"},
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
            answer = (
                "当前问题缺少明确的查询对象，请补充要查询的资料、对象或范围。"
                if routing.control_outcome == "clarify"
                else "该问题超出当前知识库及受控外部来源的回答范围。"
            )
            trace.record(
                "evidence.gate",
                "1",
                time.perf_counter(),
                {"control_outcome": routing.control_outcome},
                {"answer_status": "insufficient_evidence"},
                status="skipped",
            )
            return QueryResponse(
                answer=answer,
                answer_status="insufficient_evidence",
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
            query_plan.normalized, original_embedding, retrieval_limit, knowledge_base_id,
            filters=filters, access=access,
        )
        query_rankings = [original_candidates]
        fallback_used = False
        for expanded_query in query_values[1:]:
            try:
                expanded_embedding = self.embedder.encode([expanded_query])[0]
                expanded_candidates = self.retrieve_candidates(
                    expanded_query, expanded_embedding, retrieval_limit, knowledge_base_id,
                    filters=filters, access=access,
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

        # 门禁前最多执行一次受控补检；只扩大查询，不循环、不绕过 ACL 或 active version。
        supplemental_candidate_count = 0
        if len(candidates) < policy.minimum_evidence_count:
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

        web_results: list[WebSearchResult] = []
        web_status = "skipped"
        web_error_message: str | None = None
        web_started = time.perf_counter()
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
        should_use_web = bool(
            differential_enabled
            and policy.web_search_enabled
            and policy.allowed_domains
            and self.web_provider is not None
            and web_filter_allows
            and (routing.requires_web or len(candidates) < policy.minimum_evidence_count)
        )
        if event_callback:
            event_callback("module_started", {"module_key": "retrieval.web_policy"})
        if should_use_web:
            try:
                web_results = self.web_provider.search(
                    routing.effective_question,
                    policy.allowed_domains,
                    policy.max_web_results,
                )
                candidates.extend(_web_chunk(item, knowledge_base_id) for item in web_results)
                trace.record(
                    "retrieval.web_policy",
                    "1",
                    web_started,
                    {"query": routing.effective_question, "domains": policy.allowed_domains},
                    [item.url for item in web_results],
                    metrics={"result_count": len(web_results)},
                )
                web_status = "succeeded"
            except Exception as exc:
                web_error_message = str(exc)
                web_status = "degraded" if candidates else "failed"
                trace.record(
                    "retrieval.web_policy",
                    "1",
                    web_started,
                    {"query": routing.effective_question, "domains": policy.allowed_domains},
                    None,
                    status="degraded" if candidates else "failed",
                    error_code="WEB_RETRIEVAL_FAILED",
                    error_message=str(exc),
                    fallback_reason="继续使用知识库证据" if candidates else None,
                )
        else:
            trace.record(
                "retrieval.web_policy",
                "1",
                web_started,
                {
                    "enabled": policy.web_search_enabled,
                    "requires_web": routing.requires_web,
                    "filter_allows_web": web_filter_allows,
                },
                [],
                status="skipped",
            )
        if event_callback:
            event_callback(
                "module_completed",
                {
                    "module_key": "retrieval.web_policy",
                    "status": web_status,
                },
            )
        retrieval_ms = _elapsed(retrieval_started)
        if not candidates:
            query_metadata = {
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
            self._raise_no_candidates(
                knowledge_base_id,
                filters,
                access,
                query_metadata,
                trace,
                routing.as_dict(),
                policy,
                active_index_version_id,
                pipeline_profile=effective_profile.profile_id,
                profile_version=effective_profile.version,
                web_error_message=web_error_message if web_status == "failed" else None,
            )

        rerank_started = time.perf_counter()
        if event_callback:
            event_callback("stage", {"stage": "rerank", "message": "正在进行相关性排序"})
        rerank_texts = [
            (
                f"{' / '.join(candidate.metadata.get('heading_path') or [])}\n{candidate.text}"
                if effective_profile.intent == "procedure" and candidate.metadata.get("heading_path")
                else candidate.text
            )
            for candidate in candidates
        ]
        scores = self.reranker.score(routing.effective_question, rerank_texts)
        # 在线查询与正式评测共用融合排序，避免两个入口产生不同的质量结论。
        selection_limit = min(max(rerank_k, policy.minimum_evidence_count), len(candidates))
        ranked = rank_candidates(candidates, scores, selection_limit)
        if differential_enabled and effective_profile.intent in {"summarize", "compare"}:
            ranked = _diversify_by_document(ranked, selection_limit)
        if differential_enabled and effective_profile.intent == "procedure":
            ranked = _order_procedure_evidence(ranked)
        rerank_ms = _elapsed(rerank_started)
        evidence_modules = [
            item
            for item in effective_profile.modules
            if item.startswith("evidence.") and item != "evidence.gate"
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
                event_callback("module_completed", {"module_key": "context.compress", "status": "succeeded"})

        source_items = [_source(item) for item in ranked]
        if event_callback:
            event_callback("sources", {"items": [item.model_dump(mode="json") for item in source_items]})

        gate_started = time.perf_counter()
        if event_callback:
            event_callback("module_started", {"module_key": "evidence.gate"})
        web_requirement_satisfied = bool(
            not differential_enabled or not routing.requires_web or web_results
        )
        evidence_sufficient = (
            len(ranked) >= policy.minimum_evidence_count and web_requirement_satisfied
        )
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
                else "WEB_EVIDENCE_REQUIRED" if not web_requirement_satisfied else "INSUFFICIENT_EVIDENCE"
            ),
        )
        if event_callback:
            event_callback(
                "evidence_gate_completed",
                {"sufficient": evidence_sufficient, "evidence_count": len(ranked)},
            )
            event_callback(
                "module_completed",
                {"module_key": "evidence.gate", "status": "succeeded" if evidence_sufficient else "failed"},
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
                    "routing": _elapsed(routing_started), "retrieval": retrieval_ms,
                    "rerank": rerank_ms, "total": _elapsed(total_started),
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
                    "evidence_count": len(ranked),
                    "acl_revalidated": True,
                    "current_version_revalidated": True,
                    "retrieval_status_revalidated": True,
                    "citation_indices": [],
                    "citation_valid": True,
                    "claim_citation_coverage": True,
                    "outcome_reason": "INSUFFICIENT_EVIDENCE",
                },
            )

        prompt = build_prompt(routing.effective_question, prompt_chunks, effective_profile.intent)
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
                prompt.text, len(ranked), event_callback
            )
        else:
            parsed_answer, generation_metadata = self._generate_answer(prompt.text, len(ranked))
        generation_ms = _elapsed(generation_started)
        used_generation_model = generation_metadata.get("configured_model")
        if not isinstance(used_generation_model, str):
            used_generation_model = self.generator.model_name
        model_metadata = _model_metadata(generation_metadata, used_generation_model)
        trace.record(
            generation_module,
            "1",
            generation_started,
            {"prompt_hash": prompt.sha256, "evidence_count": len(ranked)},
            {"answer_status": parsed_answer.status},
            status="failed" if parsed_answer.status == "generation_failed" else "succeeded",
            error_code=parsed_answer.error_code,
            error_message=parsed_answer.error_message,
        )
        if event_callback:
            event_callback(
                "module_completed",
                {"module_key": generation_module, "status": "failed" if parsed_answer.status == "generation_failed" else "succeeded"},
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
            {"citation_valid": parsed_answer.citation_valid, "claim_coverage": parsed_answer.claim_citation_coverage},
            status="succeeded" if verified else "failed",
            error_code=None if verified else (parsed_answer.error_code or "GENERATION_VERIFICATION_FAILED"),
        )
        if event_callback:
            event_callback(
                "generation_verified",
                {"citation_valid": parsed_answer.citation_valid, "claim_citation_coverage": parsed_answer.claim_citation_coverage},
            )
            event_callback(
                "module_completed",
                {"module_key": "generation.verify", "status": "succeeded" if verified else "failed"},
            )
        return QueryResponse(
            answer=parsed_answer.answer,
            answer_status=parsed_answer.status,
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
                "retrieved_candidate_count": (
                    sum(len(items) for items in query_rankings) + supplemental_candidate_count
                ),
                "fused_candidate_count": len(candidates),
                "returned_source_count": len(ranked),
                "filter_match_count": len(candidates) if filters else None,
                "uncategorized_candidate_count": count_uncategorized(candidates),
            },
            generation_governance={
                "minimum_evidence_count": policy.minimum_evidence_count,
                "evidence_count": len(ranked),
                # 候选进入 ranked 前已经通过统一 ACL、当前版本、有效期和检索状态过滤。
                "acl_revalidated": True,
                "current_version_revalidated": True,
                "retrieval_status_revalidated": True,
                "citation_indices": list(parsed_answer.citation_indices),
                "citation_valid": parsed_answer.citation_valid,
                "claim_citation_coverage": parsed_answer.claim_citation_coverage,
                "outcome_reason": parsed_answer.error_code or parsed_answer.status,
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
            item for item in documents
            if item.get("status") == "ready" and int(item.get("chunk_count", 0)) > 0
        ]
        processing_documents = any(item.get("status") in {"pending", "indexing"} for item in documents)
        visible_documents = [item for item in indexed_documents if can_retrieve_metadata(item, access)]
        if not indexed_documents and processing_documents:
            raise AppError("DOCUMENTS_PROCESSING", "当前资料仍在处理，请稍后重试。", 409, {**details, "bad_case_category": "documents_processing"})
        if access is not None and indexed_documents and not visible_documents:
            raise AppError("NO_AUTHORIZED_DOCUMENTS", "当前权限范围内没有可检索资料。", 403, {**details, "bad_case_category": "acl_no_visible_documents"})
        if filters and indexed_documents:
            raise AppError("NO_MATCHING_DOCUMENTS", "没有符合当前过滤条件的资料，请调整分类、标签或来源范围。", 409, {**details, "bad_case_category": "metadata_filter_no_match"})
        if documents:
            raise AppError("NO_RETRIEVABLE_DOCUMENTS", "当前资料尚不可检索，请检查处理状态。", 409, {**details, "bad_case_category": "no_retrievable_documents"})
        raise AppError("NO_DOCUMENTS", "知识库为空，请先上传文档。", 409, {**details, "bad_case_category": "knowledge_base_empty"})

    def _generate_answer(
        self,
        prompt: str,
        source_count: int,
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
        return parse_answer(raw_answer, source_count), metadata

    def _generate_answer_stream(
        self,
        prompt: str,
        source_count: int,
        event_callback: QueryEventCallback,
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
                "MODEL_REGION_UNSUPPORTED", "MODEL_QUOTA_EXHAUSTED", "MODEL_AUTH_FAILED",
                "MODEL_RATE_LIMITED", "MODEL_TIMEOUT", "MODEL_NOT_FOUND", "MODEL_UNAVAILABLE",
            }:
                raise
            details = dict(exc.details) if isinstance(exc.details, dict) else {}
            return ParsedAnswer("generation_failed", GENERATION_FAILED_ANSWER, exc.code, exc.message), details

        event_callback("stage", {"stage": "governance", "message": "正在校验引用"})
        parsed = parse_answer(raw_answer, source_count)
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
            []
            if metadata.get("evidence_source_type") == "web"
            else item.retrieval_methods or ["vector"]
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
        column_start=(
            int(metadata["column_start"]) if metadata.get("column_start") is not None else None
        ),
        column_end=(
            int(metadata["column_end"]) if metadata.get("column_end") is not None else None
        ),
        source_url=(str(metadata["source_url"]) if metadata.get("source_url") else None),
        external_resource_id=(
            str(metadata["external_resource_id"])
            if metadata.get("external_resource_id")
            else None
        ),
        evidence_source_type=(
            "web" if metadata.get("evidence_source_type") == "web" else "knowledge_base"
        ),
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
        replace(item, text=f"{item.text[:max_chars].rstrip()}…")
        if len(item.text) > max_chars
        else item
        for item in candidates
    ]


def _conflict_signal_count(candidates: list[RetrievedChunk]) -> int:
    pattern = re.compile(r"冲突|不一致|相反|然而|但是|并非|不支持")
    return sum(bool(pattern.search(item.text)) for item in candidates)
