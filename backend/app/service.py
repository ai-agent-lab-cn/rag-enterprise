import time
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from .chunking import split_sections, stable_document_id
from .config import Settings
from .errors import AppError
from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID
from .lexical import LexicalIndexCache
from .models import EmbeddingModel, GeminiGenerator, Reranker
from .parsers import parse_document
from .prompts import (
    GENERATION_FAILED_ANSWER,
    RETRIEVAL_ONLY_ANSWER,
    ParsedAnswer,
    build_prompt,
    parse_answer,
)
from .query_understanding import build_query_plan
from .ranking import fuse_query_candidates, rank_candidates, reciprocal_rank_fusion
from .schemas import DocumentInfo, QueryResponse, Source
from .store import ChromaStore, RetrievedChunk

# 完整 RAG 编排：入库、召回、精排、Prompt、生成


class RAGServiceProtocol(Protocol):
    stores_source_files: bool

    def index_document(
        self, filename: str, content: bytes, knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID
    ) -> DocumentInfo: ...
    def list_documents(
        self, knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID
    ) -> list[DocumentInfo]: ...
    def delete_document(
        self, document_id: str, knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID
    ) -> bool: ...
    def query(
        self,
        question: str,
        retrieve_k: int,
        rerank_k: int,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> QueryResponse: ...


class RAGService:
    stores_source_files = False
    def __init__(
        self,
        settings: Settings,
        store: ChromaStore,
        embedder: EmbeddingModel,
        reranker: Reranker,
        generator: GeminiGenerator,
        lexical: LexicalIndexCache | None = None,
    ):
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self.generator = generator
        # 未注入词法索引的运行时（如单机 Chroma）只走向量召回。
        self.lexical = lexical

    def index_document(
        self,
        filename: str,
        content: bytes,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> DocumentInfo:
        document_id = stable_document_id(filename, content)
        for document in self.store.list_documents(knowledge_base_id):
            if document["document_id"] == document_id:
                return DocumentInfo(**document)

        sections = parse_document(filename, content)
        chunks = split_sections(
            document_id,
            Path(filename).name,
            sections,
            self.settings.chunk_size,
            self.settings.chunk_overlap,
            knowledge_base_id,
        )
        embeddings = self.embedder.encode([chunk.text for chunk in chunks])
        self.store.upsert(chunks, embeddings)
        return DocumentInfo(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            filename=Path(filename).name,
            chunk_count=len(chunks),
        )

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

    def retrieve_candidates(
        self,
        question: str,
        embedding: list[float],
        retrieve_k: int,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        retrieval_mode: str | None = None,
    ) -> list[RetrievedChunk]:
        """产出召回候选。在线查询与离线评测共用此方法，避免两个入口得出不同结论。

        hybrid 用 RRF 合并向量与词法两路名次：RRF 只看名次不看分数，因此余弦与 BM25
        的量纲差异无需归一化即可合并。词法独有的候选会补算真实余弦，否则它们的
        ``retrieval_score`` 会留在 0，被后续 ``rank_candidates`` 的归一化压到最低。
        """

        mode = retrieval_mode or self.settings.retrieval_mode
        if mode == "vector" or self.lexical is None:
            return self.store.query(
                embedding, retrieve_k, knowledge_base_id, query_text=question
            )

        hits = self.lexical.get(knowledge_base_id).search(question, retrieve_k)
        lexical_scores = {hit.chunk_id: hit.score for hit in hits}
        if mode == "lexical":
            fused_ids = [hit.chunk_id for hit in hits]
            vector_candidates: list[RetrievedChunk] = []
        else:
            vector_candidates = self.store.query(
                embedding, retrieve_k, knowledge_base_id, query_text=question
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
    ) -> QueryResponse:
        total_started = time.perf_counter()
        retrieval_started = time.perf_counter()
        query_plan = build_query_plan(question)
        original_embedding = self.embedder.encode([query_plan.normalized])[0]
        original_candidates = self.retrieve_candidates(
            query_plan.normalized, original_embedding, retrieve_k, knowledge_base_id
        )
        query_rankings = [original_candidates]
        fallback_used = False
        for expanded_query in query_plan.queries[1:]:
            try:
                expanded_embedding = self.embedder.encode([expanded_query])[0]
                expanded_candidates = self.retrieve_candidates(
                    expanded_query, expanded_embedding, retrieve_k, knowledge_base_id
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
            raise AppError("NO_DOCUMENTS", "知识库为空，请先上传文档。", 409)

        rerank_started = time.perf_counter()
        scores = self.reranker.score(question, [candidate.text for candidate in candidates])
        # 在线查询与正式评测共用融合排序，避免两个入口产生不同的质量结论。
        ranked = rank_candidates(candidates, scores, min(rerank_k, len(candidates)))
        rerank_ms = _elapsed(rerank_started)

        prompt = build_prompt(question, ranked)
        generation_started = time.perf_counter()
        parsed_answer, generation_metadata = self._generate_answer(prompt.text, len(ranked))
        generation_ms = _elapsed(generation_started)
        model_metadata = _model_metadata(generation_metadata, self.generator.model_name)
        return QueryResponse(
            answer=parsed_answer.answer,
            answer_status=parsed_answer.status,
            error_code=parsed_answer.error_code,
            error_message=parsed_answer.error_message,
            sources=[_source(item) for item in ranked],
            model=self.generator.model_name,
            models={
                "embedding": self.embedder.model_name,
                "reranker": self.reranker.model_name,
                "generation": self.generator.model_name,
            },
            model_metadata=model_metadata,
            prompt_version=prompt.version,
            prompt_hash=prompt.sha256,
            query_metadata={
                "strategy": query_plan.strategy,
                "query_count": len(query_rankings),
                "expansion_count": query_plan.expansion_count,
                "fallback_used": fallback_used,
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
            if exc.code not in {"MODEL_TIMEOUT", "MODEL_UNAVAILABLE"}:
                raise
            return (
                ParsedAnswer(
                    "generation_failed",
                    GENERATION_FAILED_ANSWER,
                    exc.code,
                    exc.message,
                ),
                {},
            )
        return parse_answer(raw_answer, source_count), metadata


def _model_metadata(
    response_metadata: dict[str, object],
    configured_model: str,
) -> dict[str, str | int | float | bool]:
    """只保留可复现且体积稳定的生成元数据，不保存供应商原始响应或 Prompt。"""

    metadata: dict[str, str | int | float | bool] = {"configured_model": configured_model}
    for source_key, target_key in (
        ("model_version", "model_version"),
        ("response_id", "response_id"),
    ):
        value = response_metadata.get(source_key)
        if isinstance(value, (str, int, float, bool)):
            metadata[target_key] = value
    return metadata


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
    )
