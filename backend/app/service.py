import hashlib
import time
from pathlib import Path
from typing import Protocol

from .chunking import split_sections, stable_document_id
from .config import Settings
from .errors import AppError
from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID
from .models import EmbeddingModel, GeminiGenerator, Reranker
from .parsers import parse_document
from .ranking import rank_candidates
from .schemas import DocumentInfo, QueryResponse, Source
from .store import ChromaStore, RetrievedChunk

# 完整 RAG 编排：入库、召回、精排、Prompt、生成
PROMPT_VERSION = "v2-legacy"


class RAGServiceProtocol(Protocol):
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
    def __init__(
        self,
        settings: Settings,
        store: ChromaStore,
        embedder: EmbeddingModel,
        reranker: Reranker,
        generator: GeminiGenerator,
    ):
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self.generator = generator

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

    def query(
        self,
        question: str,
        retrieve_k: int,
        rerank_k: int,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> QueryResponse:
        total_started = time.perf_counter()
        retrieval_started = time.perf_counter()
        query_embedding = self.embedder.encode([question])[0]
        candidates = self.store.query(query_embedding, retrieve_k, knowledge_base_id)
        retrieval_ms = _elapsed(retrieval_started)
        if not candidates:
            raise AppError("NO_DOCUMENTS", "知识库为空，请先上传文档。", 409)

        rerank_started = time.perf_counter()
        scores = self.reranker.score(question, [candidate.text for candidate in candidates])
        # 在线查询与正式评测共用融合排序，避免两个入口产生不同的质量结论。
        ranked = rank_candidates(candidates, scores, min(rerank_k, len(candidates)))
        rerank_ms = _elapsed(rerank_started)

        generation_started = time.perf_counter()
        prompt = _build_prompt(question, ranked)
        answer, generation_metadata = self.generator.generate(prompt)
        generation_ms = _elapsed(generation_started)
        model_metadata = _model_metadata(generation_metadata, self.generator.model_name)
        return QueryResponse(
            answer=answer,
            sources=[_source(item) for item in ranked],
            model=self.generator.model_name,
            models={
                "embedding": self.embedder.model_name,
                "reranker": self.reranker.model_name,
                "generation": self.generator.model_name,
            },
            model_metadata=model_metadata,
            prompt_version=PROMPT_VERSION,
            prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            latency_ms={
                "retrieval": retrieval_ms,
                "rerank": rerank_ms,
                "generation": generation_ms,
                "total": _elapsed(total_started),
            },
        )


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


def _build_prompt(question: str, chunks: list[RetrievedChunk]) -> str:
    context = "\n\n".join(
        f"[来源 {index}: {item.metadata.get('filename', 'unknown')} / "
        f"第 {item.metadata.get('paragraph', 0) + 1} 段]\n{item.text}"
        for index, item in enumerate(chunks, start=1)
    )
    return f"""你是 RongRAG Studio 的知识助手。请仅根据给定资料回答问题。
如果资料不足，请明确说明无法从资料中确定。回答中的关键事实请使用 [来源 N] 标注。
请使用简洁的纯文本，不要使用 Markdown 加粗标记。

问题：{question}

资料：
{context}
"""


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
    )
