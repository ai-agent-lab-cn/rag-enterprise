"""统一在线查询与离线评测使用的候选排序策略。"""

from collections.abc import Sequence

from .store import RetrievedChunk

VECTOR_SCORE_WEIGHT = 0.15
# RRF 的平滑常数，取自 Cormack 等人提出的默认值；越大越弱化头部名次的优势。
RRF_K = 60


def fuse_retrieval_candidates(
    vector_candidates: Sequence[RetrievedChunk],
    lexical_candidates: Sequence[RetrievedChunk],
    limit: int,
    rank_constant: int = RRF_K,
) -> list[RetrievedChunk]:
    """兼容 V5-1 的双路候选融合，并同步新旧召回通路字段。"""

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
            methods = set(item.retrieval_methods or item.channels)
            methods.add(method)
            item.retrieval_methods = sorted(methods)
            item.channels = tuple(item.retrieval_methods)
            if method == "vector":
                item.vector_score = candidate.vector_score or candidate.retrieval_score
            else:
                item.lexical_score = candidate.lexical_score or candidate.retrieval_score
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


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]],
    limit: int,
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    """按倒数排名融合多路召回结果，返回前 ``limit`` 个 ``(chunk_id, 融合分数)``。

    RRF 只使用名次、不使用原始分数，因此向量余弦与 BM25 这种量纲完全不同的分数可以
    直接合并，无需先归一化——而归一化在候选很少或分数接近时会退化成全 0（见 ``_minmax``）。
    """

    if limit < 1:
        raise ValueError("limit 必须大于等于 1")
    if k < 1:
        raise ValueError("k 必须大于等于 1")

    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    # 分数相同时按 chunk_id 排序，保证融合结果可复现。
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]


def _minmax(values: Sequence[float]) -> list[float]:
    """把不同量纲的分数缩放到 0–1；全相同时视为没有区分度。"""

    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return [0.0] * len(values)
    return [(value - minimum) / (maximum - minimum) for value in values]
