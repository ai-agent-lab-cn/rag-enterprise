"""使用真实模型和隔离 ChromaStore 生成正式检索质量基线。"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from huggingface_hub import HfApi

from backend.app.chunking import Chunk
from backend.app.config import get_settings
from backend.app.models import get_embedding_model, get_reranker
from backend.app.ranking import VECTOR_SCORE_WEIGHT, rank_candidates
from backend.app.store import ChromaStore

from .dataset import EvaluationDataset, load_dataset
from .metrics import evaluate_rankings
from .report import RetrievalEvaluationReport, assess_metric

RECALL_AT_5_THRESHOLD = 0.80
VECTOR_MRR_THRESHOLD = 0.60
RERANK_MRR_THRESHOLD = 0.70


def run_baseline(
    dataset: EvaluationDataset,
    commit: str,
    baseline: RetrievalEvaluationReport | None = None,
) -> RetrievalEvaluationReport:
    """运行一次真实检索基线，并返回带完整上下文的正式报告。

    评测只验证 embedding、Chroma 向量召回和 CrossEncoder 精排；生成模型不参与
    V2 指标。传入的 commit 应指向被评测的应用基线，而不是报告文件自身的提交。
    """

    settings = get_settings()
    embedder = get_embedding_model()
    reranker = get_reranker()

    # 正式评测仍使用临时索引，避免污染开发者通过页面上传的文档和持久化 Chroma 数据。
    with TemporaryDirectory(prefix="rag-enterprise-baseline-") as directory:
        store = ChromaStore(
            Path(directory),
            f"retrieval_baseline_{dataset.version.replace('.', '_')}",
            settings.embedding_model,
        )
        chunks = [_evaluation_chunk(item, index) for index, item in enumerate(dataset.chunks)]
        store.upsert(chunks, embedder.encode([chunk.text for chunk in chunks]))

        vector_rankings: dict[str, list[str]] = {}
        reranked_rankings: dict[str, list[str]] = {}
        for query in dataset.queries:
            # 先保存 Chroma 返回的原始顺序，向量 MRR 必须在精排改序前计算。
            candidates = store.query(embedder.encode([query.question])[0], limit=10)
            vector_rankings[query.query_id] = [candidate.chunk_id for candidate in candidates]

            # CrossEncoder 只对同一批候选重新排序，不能补召回向量阶段遗漏的分块。
            scores = reranker.score(query.question, [candidate.text for candidate in candidates])
            reranked = rank_candidates(candidates, scores, 5)
            reranked_rankings[query.query_id] = [candidate.chunk_id for candidate in reranked]

    metrics = evaluate_rankings(dataset.queries, vector_rankings, reranked_rankings)
    run_at = datetime.now(UTC)
    # 阈值来自 ADR #23；基线不通过时必须如实保留失败结论，不能在运行后降低门槛。
    return RetrievalEvaluationReport(
        report_id=f"retrieval-{run_at:%Y%m%dT%H%M%SZ}",
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        commit=commit,
        run_at=run_at,
        official=True,
        models={
            "embedding": _resolved_model(settings.embedding_model),
            "reranker": _resolved_model(settings.reranker_model),
        },
        parameters={
            "retrieve_k": 10,
            "rerank_k": 5,
            "distance": "cosine",
            "normalize_embeddings": True,
            "ranking_strategy": "minmax_weighted_fusion",
            "vector_score_weight": VECTOR_SCORE_WEIGHT,
        },
        query_count=metrics.query_count,
        recall_at_5=assess_metric(
            metrics.recall_at_5,
            RECALL_AT_5_THRESHOLD,
            baseline.recall_at_5.value if baseline else None,
        ),
        vector_mrr=assess_metric(
            metrics.vector_mrr,
            VECTOR_MRR_THRESHOLD,
            baseline.vector_mrr.value if baseline else None,
        ),
        rerank_mrr=assess_metric(
            metrics.rerank_mrr,
            RERANK_MRR_THRESHOLD,
            baseline.rerank_mrr.value if baseline else None,
        ),
    )


def _evaluation_chunk(item, index: int) -> Chunk:
    """把版本化评测分块转换成现有 ChromaStore 所需的应用 Chunk。"""

    return Chunk(
        chunk_id=item.chunk_id,
        document_id=item.document_id,
        filename=item.filename,
        text=item.text,
        page=None,
        paragraph=index,
        chunk_index=index,
        char_count=len(item.text),
        summary=item.text[:80],
    )


def _resolved_model(model_name: str) -> str:
    """解析 Hugging Face 精确 revision，使后续运行可以识别实际模型版本。"""

    revision = HfApi().model_info(model_name).sha
    if not revision:
        raise RuntimeError(f"无法解析模型 revision：{model_name}")
    return f"{model_name}@{revision}"


def main() -> None:
    """读取命令行参数，运行评测并将机器可读报告写入指定路径。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-report", type=Path)
    args = parser.parse_args()

    baseline = None
    if args.baseline_report:
        baseline = RetrievalEvaluationReport.model_validate_json(
            args.baseline_report.read_text(encoding="utf-8")
        )
    report = run_baseline(load_dataset(args.dataset), args.commit, baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    print(f"passed={str(report.passed).lower()}")


if __name__ == "__main__":
    main()
