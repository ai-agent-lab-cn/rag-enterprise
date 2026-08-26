import pytest

from backend.app.config import Settings
from backend.app.errors import AppError
from backend.app.query_understanding import build_query_plan, normalize_query
from backend.app.schemas import QueryMetadataFilter
from backend.app.service import RAGService
from backend.app.store import RetrievedChunk


def _candidate(chunk_id: str, score: float = 0.8) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=f"内容 {chunk_id}",
        metadata={
            "knowledge_base_id": "kb_default",
            "document_id": f"doc_{chunk_id}",
            "filename": f"{chunk_id}.md",
            "paragraph": 0,
            "chunk_index": 0,
        },
        retrieval_score=score,
        vector_score=score,
        retrieval_methods=["vector", "lexical"],
    )


def test_query_plan_normalizes_and_expands_in_stable_order() -> None:
    question = '  ＲＡＧ “权限隔离” ACL-42  '

    first = build_query_plan(question)
    second = build_query_plan(question)

    assert normalize_query(question) == 'RAG "权限隔离" ACL-42'
    assert first == second
    assert first.strategy == "controlled_expansion"
    assert first.queries == (
        'RAG "权限隔离" ACL-42',
        "权限隔离",
        "ACL-42",
        "RAG 检索增强生成",
    )
    assert first.expansion_count == 3


def test_query_plan_without_expansion_preserves_original_strategy() -> None:
    assert build_query_plan("如何删除文档?").strategy == "original"
    assert build_query_plan("  如何   删除文档？ ").strategy == "normalized"


class _Embedder:
    model_name = "test/embedder"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0] for text in texts]


class _Reranker:
    model_name = "test/reranker"

    def score(self, question: str, chunks: list[str]) -> list[float]:
        return [float(index) for index, _ in enumerate(chunks, start=1)]


class _Generator:
    model_name = "test/generator"
    ready = False


class _Store:
    def __init__(self, results: dict[str, list[RetrievedChunk] | Exception]):
        self.results = results
        self.queries: list[str] = []

    def query(self, embedding, limit, knowledge_base_id, query_text=None, filters=None, access=None):
        self.queries.append(query_text)
        result = self.results.get(query_text, [])
        if isinstance(result, Exception):
            raise result
        return result[:limit]

    def list_documents(self, knowledge_base_id):
        return [{"document_id": "doc_ready", "filename": "ready.md", "chunk_count": 1, "status": "ready"}]


def test_service_fuses_multiple_queries_and_reports_strategy() -> None:
    original = 'RAG "权限隔离"'
    store = _Store(
        {
            original: [_candidate("shared"), _candidate("original")],
            "权限隔离": [_candidate("shared"), _candidate("phrase")],
            "RAG 检索增强生成": [_candidate("shared"), _candidate("alias")],
        }
    )
    service = RAGService(Settings(), store, _Embedder(), _Reranker(), _Generator())

    response = service.query(original, retrieve_k=5, rerank_k=5)

    assert store.queries == [original, "权限隔离", "RAG 检索增强生成"]
    assert len({item.chunk_id for item in response.sources}) == len(response.sources)
    assert next(item for item in response.sources if item.chunk_id == "shared").query_match_count == 3
    assert response.query_metadata is not None
    assert response.query_metadata.strategy == "controlled_expansion"
    assert response.query_metadata.query_count == 3
    assert response.query_metadata.expansion_count == 2
    assert response.query_metadata.fallback_used is False
    assert response.query_metadata.retrieved_candidate_count == 6
    assert response.query_metadata.fused_candidate_count == 4
    assert response.query_metadata.returned_source_count == 4
    assert response.query_metadata.filter_match_count is None


def test_query_expansion_reuses_the_same_metadata_filter() -> None:
    original = 'RAG "权限隔离"'
    allowed = _candidate("allowed")
    allowed.metadata.update({"category": "安全", "tags": ["ACL"], "source_type": "file"})
    blocked = _candidate("blocked")
    blocked.metadata.update({"category": "运维", "tags": ["备份"], "source_type": "file"})
    store = _Store({original: [allowed, blocked], "权限隔离": [blocked, allowed]})
    service = RAGService(Settings(), store, _Embedder(), _Reranker(), _Generator())

    response = service.query(
        original,
        retrieve_k=5,
        rerank_k=5,
        filters=QueryMetadataFilter(categories=["安全"], tags=["ACL"]),
    )

    assert [item.chunk_id for item in response.sources] == ["allowed"]
    assert response.query_metadata is not None
    assert response.query_metadata.applied_filters is not None
    assert response.query_metadata.applied_filters.categories == ["安全"]
    assert response.query_metadata.applied_filters.tags == ["ACL"]
    assert response.query_metadata.retrieved_candidate_count == 2
    assert response.query_metadata.fused_candidate_count == 1
    assert response.query_metadata.returned_source_count == 1
    assert response.query_metadata.filter_match_count == 1


def test_filtered_query_records_a_distinct_no_match_bad_case() -> None:
    service = RAGService(Settings(), _Store({}), _Embedder(), _Reranker(), _Generator())

    with pytest.raises(AppError) as raised:
        service.query(
            "只查询安全资料",
            retrieve_k=5,
            rerank_k=5,
            filters=QueryMetadataFilter(categories=["安全"]),
        )

    assert raised.value.code == "NO_MATCHING_DOCUMENTS"
    assert raised.value.details["bad_case_category"] == "metadata_filter_no_match"
    assert raised.value.details["query_metadata"]["applied_filters"]["categories"] == ["安全"]


def test_service_falls_back_when_expansions_fail_or_return_empty() -> None:
    original = "RAG 如何检索"
    store = _Store(
        {
            original: [_candidate("original")],
            "RAG 检索增强生成": RuntimeError("expanded query failed"),
        }
    )
    service = RAGService(Settings(), store, _Embedder(), _Reranker(), _Generator())

    response = service.query(original, retrieve_k=5, rerank_k=5)

    assert [item.chunk_id for item in response.sources] == ["original"]
    assert response.query_metadata is not None
    assert response.query_metadata.query_count == 1
    assert response.query_metadata.fallback_used is True


def test_query_candidate_fusion_rejects_invalid_limit() -> None:
    from backend.app.ranking import fuse_query_candidates

    with pytest.raises(ValueError, match="limit"):
        fuse_query_candidates([], 0)
