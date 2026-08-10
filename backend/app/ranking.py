"""统一在线查询与离线评测使用的候选排序策略。"""

from collections.abc import Sequence

from .store import RetrievedChunk

VECTOR_SCORE_WEIGHT = 0.15


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
