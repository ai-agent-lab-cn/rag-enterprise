"""统一在线查询与离线评测使用的候选排序策略。"""

from collections.abc import Sequence

from .store import RetrievedChunk

VECTOR_SCORE_WEIGHT = 0.15
RRF_RANK_CONSTANT = 60


def fuse_query_candidates(
    rankings: Sequence[Sequence[RetrievedChunk]],
    limit: int,
    rank_constant: int = RRF_RANK_CONSTANT,
) -> list[RetrievedChunk]:
    """以 RRF 稳定合并原查询和扩展查询的候选，并按 chunk ID 去重。"""

    if limit < 1:
        raise ValueError("limit 必须大于等于 1")
    if rank_constant < 1:
        raise ValueError("rank_constant 必须大于等于 1")
    merged: dict[str, RetrievedChunk] = {}
    scores: dict[str, float] = {}
    first_seen: dict[str, tuple[int, int]] = {}
    matches: dict[str, int] = {}
    for query_index, candidates in enumerate(rankings):
        seen_in_query: set[str] = set()
        for rank, candidate in enumerate(candidates, start=1):
            item = merged.setdefault(candidate.chunk_id, candidate)
            first_seen.setdefault(candidate.chunk_id, (query_index, rank))
            scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1 / (
                rank_constant + rank
            )
            if candidate.chunk_id not in seen_in_query:
                matches[candidate.chunk_id] = matches.get(candidate.chunk_id, 0) + 1
                seen_in_query.add(candidate.chunk_id)
            item.retrieval_methods = sorted(
                set((item.retrieval_methods or []) + (candidate.retrieval_methods or []))
            )
            if item.vector_score is None:
                item.vector_score = candidate.vector_score
            if item.lexical_score is None:
                item.lexical_score = candidate.lexical_score

    ordered = sorted(
        merged.values(),
        key=lambda item: (
            -scores[item.chunk_id],
            first_seen[item.chunk_id],
            item.chunk_id,
        ),
    )[:limit]
    for item in ordered:
        item.retrieval_score = round(scores[item.chunk_id], 8)
        item.query_match_count = matches[item.chunk_id]
    return ordered


def fuse_retrieval_candidates(
    vector_candidates: Sequence[RetrievedChunk],
    lexical_candidates: Sequence[RetrievedChunk],
    limit: int,
    rank_constant: int = RRF_RANK_CONSTANT,
) -> list[RetrievedChunk]:
    """使用 Reciprocal Rank Fusion 合并向量与关键词候选。

    RRF 只依赖各检索器的稳定名次，避免直接混合余弦相似度与 trigram 分数。
    相同融合分数时依次按向量名次、关键词名次和 chunk ID 排序，保证结果可复现。
    """

    if limit < 1:
        raise ValueError("limit 必须大于等于 1")
    if rank_constant < 1:
        raise ValueError("rank_constant 必须大于等于 1")

    merged: dict[str, RetrievedChunk] = {}
    ranks: dict[str, dict[str, int]] = {}
    scores: dict[str, float] = {}
    for method, candidates in (("vector", vector_candidates), ("lexical", lexical_candidates)):
        for rank, candidate in enumerate(candidates, start=1):
            item = merged.setdefault(candidate.chunk_id, candidate)
            item.retrieval_methods = sorted(set((item.retrieval_methods or []) + [method]))
            if method == "vector":
                item.vector_score = (
                    candidate.vector_score
                    if candidate.vector_score is not None
                    else candidate.retrieval_score
                )
            else:
                item.lexical_score = (
                    candidate.lexical_score
                    if candidate.lexical_score is not None
                    else candidate.retrieval_score
                )
            ranks.setdefault(candidate.chunk_id, {})[method] = rank
            scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1 / (
                rank_constant + rank
            )

    missing_rank = len(merged) + 1
    ordered = sorted(
        merged.values(),
        key=lambda item: (
            -scores[item.chunk_id],
            ranks[item.chunk_id].get("vector", missing_rank),
            ranks[item.chunk_id].get("lexical", missing_rank),
            item.chunk_id,
        ),
    )[:limit]
    for item in ordered:
        item.retrieval_score = round(scores[item.chunk_id], 8)
    return ordered


def rank_candidates(
    candidates: list[RetrievedChunk],
    rerank_scores: Sequence[float],
    limit: int,
    vector_weight: float = VECTOR_SCORE_WEIGHT,
) -> list[RetrievedChunk]:
    """融合归一化向量分数与 CrossEncoder 分数后返回前 ``limit`` 个候选。

    CrossEncoder 仍占 85% 主导权；15% 向量分数用于减少精排把高置信向量结果意外降级的情况。
    返回对象继续保留原始 retrieval_score 和 rerank_score，方便页面解释两个阶段的真实分数。
    """

    if len(candidates) != len(rerank_scores):
        raise ValueError("候选数量必须与精排分数数量一致")
    if limit < 1:
        raise ValueError("limit 必须大于等于 1")
    if not 0 <= vector_weight <= 1:
        raise ValueError("vector_weight 必须位于 0 和 1 之间")
    if not candidates:
        return []

    vector_scores = _minmax([candidate.retrieval_score for candidate in candidates])
    normalized_rerank_scores = _minmax([float(score) for score in rerank_scores])
    scored: list[tuple[RetrievedChunk, float]] = []
    for candidate, raw_rerank, vector_score, rerank_score in zip(
        candidates,
        rerank_scores,
        vector_scores,
        normalized_rerank_scores,
        strict=True,
    ):
        candidate.rerank_score = float(raw_rerank)
        fused_score = vector_weight * vector_score + (1 - vector_weight) * rerank_score
        scored.append((candidate, fused_score))

    return [
        candidate
        for candidate, _ in sorted(scored, key=lambda item: item[1], reverse=True)[:limit]
    ]


def _minmax(values: Sequence[float]) -> list[float]:
    """把不同量纲的分数缩放到 0–1；全相同时视为没有区分度。"""

    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return [0.0] * len(values)
    return [(value - minimum) / (maximum - minimum) for value in values]
