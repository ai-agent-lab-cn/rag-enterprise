"""检索质量评测的数据结构与指标工具。"""

from .dataset import EvaluationDataset, EvaluationQuery, load_dataset
from .metrics import RetrievalMetrics, evaluate_rankings, recall_at_k, reciprocal_rank
from .report import EvaluationMetric, RetrievalEvaluationReport, assess_metric

__all__ = [
    "EvaluationDataset",
    "EvaluationMetric",
    "EvaluationQuery",
    "RetrievalEvaluationReport",
    "RetrievalMetrics",
    "assess_metric",
    "evaluate_rankings",
    "load_dataset",
    "recall_at_k",
    "reciprocal_rank",
]
