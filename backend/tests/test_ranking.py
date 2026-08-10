import pytest

from backend.app.ranking import rank_candidates
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
