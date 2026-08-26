import pytest

from backend.app.ranking import fuse_query_candidates, fuse_retrieval_candidates, rank_candidates
from backend.app.store import RetrievedChunk


def candidate(chunk_id: str, retrieval_score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=chunk_id,
        metadata={},
        retrieval_score=retrieval_score,
    )


def test_rank_candidates_fuses_normalized_scores_and_preserves_raw_scores() -> None:
    candidates = [candidate("vector-first", 1.0), candidate("rerank-first", 0.8), candidate("last", 0.0)]

    ranked = rank_candidates(candidates, [0.9, 1.0, 0.0], limit=3)

    assert [item.chunk_id for item in ranked] == ["rerank-first", "vector-first", "last"]
    assert candidates[0].rerank_score == pytest.approx(0.9)
    assert candidates[1].rerank_score == pytest.approx(1.0)


def test_rank_candidates_validates_inputs_and_empty_candidates() -> None:
    with pytest.raises(ValueError, match="候选数量"):
        rank_candidates([candidate("one", 1.0)], [], limit=1)
    with pytest.raises(ValueError, match="limit"):
        rank_candidates([], [], limit=0)
    with pytest.raises(ValueError, match="vector_weight"):
        rank_candidates([], [], limit=1, vector_weight=1.1)

    assert rank_candidates([], [], limit=1) == []


def test_fuse_retrieval_candidates_uses_stable_rrf_and_preserves_paths() -> None:
    vectors = [candidate("semantic", 0.95), candidate("both", 0.8)]
    lexical = [candidate("exact", 0.9), candidate("both", 0.7)]
    for item in lexical:
        item.lexical_score = item.retrieval_score

    fused = fuse_retrieval_candidates(vectors, lexical, limit=3)

    assert [item.chunk_id for item in fused] == ["both", "semantic", "exact"]
    assert fused[0].retrieval_methods == ["lexical", "vector"]
    assert fused[0].vector_score == pytest.approx(0.8)
    assert fused[0].lexical_score == pytest.approx(0.7)


def test_fuse_retrieval_candidates_validates_parameters_and_ties() -> None:
    tied = fuse_retrieval_candidates(
        [candidate("b", 0.5), candidate("a", 0.5)],
        [],
        limit=2,
    )
    assert [item.chunk_id for item in tied] == ["b", "a"]
    with pytest.raises(ValueError, match="limit"):
        fuse_retrieval_candidates([], [], limit=0)
    with pytest.raises(ValueError, match="rank_constant"):
        fuse_retrieval_candidates([], [], limit=1, rank_constant=0)


def test_fuse_query_candidates_deduplicates_and_counts_query_matches() -> None:
    shared = candidate("shared", 0.8)
    first = [shared, candidate("first", 0.7)]
    second = [candidate("shared", 0.9), candidate("second", 0.6)]

    fused = fuse_query_candidates([first, second], limit=3)

    assert [item.chunk_id for item in fused] == ["shared", "first", "second"]
    assert fused[0].query_match_count == 2
