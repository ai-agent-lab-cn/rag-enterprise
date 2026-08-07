from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .dataset import EvaluationQuery


def recall_at_k(ranked_chunk_ids: Sequence[str], relevant_chunk_ids: set[str], k: int) -> float:
    """计算单个问题在前 k 个结果中覆盖的相关分块比例。"""

    if k < 1:
        raise ValueError("k 必须大于等于 1")
    if not relevant_chunk_ids:
        raise ValueError("相关分块不能为空")
    retrieved = set(ranked_chunk_ids[:k])
    return len(retrieved & relevant_chunk_ids) / len(relevant_chunk_ids)


def reciprocal_rank(ranked_chunk_ids: Sequence[str], relevant_chunk_ids: set[str]) -> float:
    """返回第一个相关分块的倒数排名；未召回时返回 0。"""

    if not relevant_chunk_ids:
        raise ValueError("相关分块不能为空")
    for rank, chunk_id in enumerate(ranked_chunk_ids, start=1):
        if chunk_id in relevant_chunk_ids:
            return 1.0 / rank
    return 0.0


@dataclass(frozen=True)
class RetrievalMetrics:
    query_count: int
    recall_at_5: float
    vector_mrr: float
    rerank_mrr: float


def evaluate_rankings(
    queries: Sequence[EvaluationQuery],
    vector_rankings: Mapping[str, Sequence[str]],
    reranked_rankings: Mapping[str, Sequence[str]],
) -> RetrievalMetrics:
    """以宏平均方式计算固定数据集的三项 V2 指标。"""

    if not queries:
        raise ValueError("评测问题不能为空")
    expected_ids = {query.query_id for query in queries}
    for name, rankings in (("向量召回", vector_rankings), ("精排", reranked_rankings)):
        missing = expected_ids - set(rankings)
        if missing:
            raise ValueError(f"{name}缺少问题结果：{sorted(missing)}")

    recalls: list[float] = []
    vector_rrs: list[float] = []
    rerank_rrs: list[float] = []
    for query in queries:
        relevant = set(query.relevant_chunk_ids)
        recalls.append(recall_at_k(vector_rankings[query.query_id], relevant, 5))
        vector_rrs.append(reciprocal_rank(vector_rankings[query.query_id], relevant))
        rerank_rrs.append(reciprocal_rank(reranked_rankings[query.query_id], relevant))

    count = len(queries)
    return RetrievalMetrics(
        query_count=count,
        recall_at_5=sum(recalls) / count,
        vector_mrr=sum(vector_rrs) / count,
        rerank_mrr=sum(rerank_rrs) / count,
    )
