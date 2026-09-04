import json
import re
import time
from dataclasses import replace
from datetime import datetime
from collections.abc import Callable
from typing import Any, Protocol

from .config import Settings
from .errors import AppError
from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID
from .lexical import LexicalIndexCache
from .models import AnswerGenerator, EmbeddingModel, Reranker
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
        self, knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID
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
    ):
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self.generator = generator
        # 未注入词法索引时只走向量召回；离线评测入口按需省略它。
        self.lexical = lexical

    def list_documents(
        self,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> list[DocumentInfo]:
        return [DocumentInfo(**item) for item in self.store.list_documents(knowledge_base_id)]

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
        if mode == "vector" or self.lexical is None:
            return _filter_candidates(self.store.query(
                embedding, retrieve_k, knowledge_base_id, query_text=question,
                **({"filters": filters} if filters else {}),
                **({"access": access} if access else {}),
            ), filters, access)

        hits = self.lexical.get(knowledge_base_id).search(question, retrieve_k)
        current_chunks = self.store.load_current_chunks(
            knowledge_base_id, **({"access": access} if access else {})
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
                for item in self.store.load_current_chunks(knowledge_base_id)
                if item.chunk_id in wanted
            }
            scores = self.store.score_by_ids(missing, embedding, knowledge_base_id)

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
    ) -> QueryResponse:
        total_started = time.perf_counter()
        if event_callback:
            event_callback("stage", {"stage": "retrieval", "message": "正在检索资料"})
        retrieval_started = time.perf_counter()
        query_plan = build_query_plan(question)
        original_embedding = self.embedder.encode([query_plan.normalized])[0]
        original_candidates = self.retrieve_candidates(
            query_plan.normalized, original_embedding, retrieve_k, knowledge_base_id,
            filters=filters, access=access,
        )
        query_rankings = [original_candidates]
        fallback_used = False
        for expanded_query in query_plan.queries[1:]:
            try:
                expanded_embedding = self.embedder.encode([expanded_query])[0]
                expanded_candidates = self.retrieve_candidates(
                    expanded_query, expanded_embedding, retrieve_k, knowledge_base_id,
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
            fuse_query_candidates(query_rankings, retrieve_k)
            if len(query_rankings) > 1
            else original_candidates
        )
        retrieval_ms = _elapsed(retrieval_started)
        if not candidates:
            query_metadata = {
                "strategy": query_plan.strategy,
                "query_count": len(query_rankings),
                "expansion_count": query_plan.expansion_count,
                "fallback_used": fallback_used,
                "applied_filters": filters.model_dump(mode="json") if filters else None,
                "retrieved_candidate_count": sum(len(items) for items in query_rankings),
                "fused_candidate_count": 0,
                "returned_source_count": 0,
                "filter_match_count": 0 if filters else None,
                "uncategorized_candidate_count": 0,
            }
            documents = self.store.list_documents(knowledge_base_id)
            indexed_documents = [
                item for item in documents
                if item.get("status") == "ready" and int(item.get("chunk_count", 0)) > 0
            ]
            processing_documents = any(
                item.get("status") in {"pending", "indexing"} for item in documents
            )
            visible_documents = [
                item for item in indexed_documents if can_retrieve_metadata(item, access)
            ]
            if not indexed_documents and processing_documents:
                raise AppError(
                    "DOCUMENTS_PROCESSING",
                    "当前资料仍在处理，请稍后重试。",
                    409,
                    {"bad_case_category": "documents_processing", "query_metadata": query_metadata},
                )
            if access is not None and indexed_documents and not visible_documents:
                raise AppError(
                    "NO_AUTHORIZED_DOCUMENTS",
                    "当前权限范围内没有可检索资料。",
                    403,
                    {"bad_case_category": "acl_no_visible_documents", "query_metadata": query_metadata},
                )
            if filters and indexed_documents:
                raise AppError(
                    "NO_MATCHING_DOCUMENTS",
                    "没有符合当前过滤条件的资料，请调整分类、标签或来源范围。",
                    409,
                    {
                        "bad_case_category": "metadata_filter_no_match",
                        "query_metadata": query_metadata,
                    },
                )
            if documents:
                raise AppError(
                    "NO_RETRIEVABLE_DOCUMENTS",
                    "当前资料尚不可检索，请检查处理状态。",
                    409,
                    {"bad_case_category": "no_retrievable_documents", "query_metadata": query_metadata},
                )
            raise AppError(
                "NO_DOCUMENTS",
                "知识库为空，请先上传文档。",
                409,
                {"bad_case_category": "knowledge_base_empty", "query_metadata": query_metadata},
            )

        rerank_started = time.perf_counter()
        if event_callback:
            event_callback("stage", {"stage": "rerank", "message": "正在进行相关性排序"})
        scores = self.reranker.score(question, [candidate.text for candidate in candidates])
        # 在线查询与正式评测共用融合排序，避免两个入口产生不同的质量结论。
        ranked = rank_candidates(candidates, scores, min(rerank_k, len(candidates)))
        rerank_ms = _elapsed(rerank_started)

        source_items = [_source(item) for item in ranked]
        if event_callback:
            event_callback("sources", {"items": [item.model_dump(mode="json") for item in source_items]})

        prompt = build_prompt(question, ranked)
        generation_started = time.perf_counter()
        if event_callback:
            event_callback("stage", {"stage": "generation", "message": "正在生成答案"})
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
                "strategy": query_plan.strategy,
                "query_count": len(query_rankings),
                "expansion_count": query_plan.expansion_count,
                "fallback_used": fallback_used,
                "applied_filters": filters,
                "retrieved_candidate_count": sum(len(items) for items in query_rankings),
                "fused_candidate_count": len(candidates),
                "returned_source_count": len(ranked),
                "filter_match_count": len(candidates) if filters else None,
                "uncategorized_candidate_count": count_uncategorized(candidates),
            },
            generation_governance={
                "minimum_evidence_count": 1,
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
                "retrieval": retrieval_ms,
                "rerank": rerank_ms,
                "generation": generation_ms,
                "total": _elapsed(total_started),
            },
        )

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
        retrieval_methods=item.retrieval_methods or ["vector"],
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
    )
