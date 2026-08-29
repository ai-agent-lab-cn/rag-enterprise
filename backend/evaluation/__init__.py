"""检索质量评测的数据结构与指标工具。"""

from .answer_quality import (
    AnswerEvaluationDataset,
    AnswerEvaluationReport,
    AnswerEvaluationRun,
    AnswerMetric,
    HumanReviewRecord,
    evaluate_answers,
    load_answer_dataset,
    load_answer_run,
    promote_official_report,
)
from .corpus_dataset import (
    CorpusEvaluationDataset,
    CorpusQuery,
    load_corpus_dataset,
    paragraph_key,
)
from .dataset import EvaluationDataset, EvaluationQuery, load_dataset
from .metrics import RetrievalMetrics, evaluate_rankings, ndcg_at_k, recall_at_k, reciprocal_rank
from .report import EvaluationMetric, RetrievalEvaluationReport, assess_metric

__all__ = [
    "AnswerEvaluationDataset",
    "AnswerEvaluationReport",
    "AnswerEvaluationRun",
    "AnswerMetric",
    "HumanReviewRecord",
    "CorpusEvaluationDataset",
    "CorpusQuery",
    "EvaluationDataset",
    "EvaluationMetric",
    "EvaluationQuery",
    "RetrievalEvaluationReport",
    "RetrievalMetrics",
    "assess_metric",
    "evaluate_answers",
    "evaluate_rankings",
    "load_corpus_dataset",
    "load_dataset",
    "ndcg_at_k",
    "paragraph_key",
    "load_answer_dataset",
    "load_answer_run",
    "promote_official_report",
    "recall_at_k",
    "reciprocal_rank",
]
