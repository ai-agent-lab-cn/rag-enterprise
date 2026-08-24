"""使用真实解析与切分管线生成语料级检索基线。

与 1.0.0 的 ``run_baseline`` 不同，这里的候选分块由 ``parse_document`` 和
``split_sections`` 现场产出，因此解析实现、chunk_size 与 overlap 都在被测范围内。
指标按段落粒度统计：命中同一段落的多个分块会各自占用 top-k 名额，切分过碎会
直接反映为 recall 与 MRR 下降。
"""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import psycopg

from backend.app.config import get_settings
from backend.app.database import check_schema_version
from backend.app.models import get_embedding_model, get_reranker
from backend.app.postgres_documents import IndexWorker, PostgresAsyncRAGService
from backend.app.ranking import VECTOR_SCORE_WEIGHT, rank_candidates
from backend.app.store import RetrievedChunk

from .corpus_dataset import (
    CorpusEvaluationDataset,
    CorpusQuery,
    load_corpus_dataset,
    paragraph_key,
)
from .dataset import EvaluationQuery
from .metrics import evaluate_rankings
from .report import RetrievalEvaluationReport, assess_metric
from .run_baseline import resolved_model

# 阈值在首次运行之前确定，低于 1.0.0 的原因是语料为真实技术文档且指标改为段落粒度，
# 而不是依据实测结果倒推。首轮不通过时如实保留失败结论，并作为内核改进目标。
RECALL_AT_5_THRESHOLD = 0.70
VECTOR_MRR_THRESHOLD = 0.55
RERANK_MRR_THRESHOLD = 0.65

RETRIEVE_K = 10
RERANK_K = 5


def run_corpus_baseline(
    dataset: CorpusEvaluationDataset,
    contents: dict[str, bytes],
    commit: str,
    chunk_size: int,
    chunk_overlap: int,
    baseline: RetrievalEvaluationReport | None = None,
    database_url: str | None = None,
    embedder=None,
    reranker=None,
) -> RetrievalEvaluationReport:
    settings = get_settings()
    database_url = database_url or settings.database_url
    if not database_url:
        raise ValueError("语料评测必须通过 --database-url 或 DATABASE_URL 指定隔离数据库")
    check_schema_version(database_url, 3)
    _require_empty_evaluation_database(database_url)
    embedder = embedder or get_embedding_model()
    reranker = reranker or get_reranker()
    knowledge_base_id = f"kb_eval_{uuid4().hex[:20]}"

    with TemporaryDirectory(prefix="rag-enterprise-corpus-") as directory:
        evaluation_settings = settings.model_copy(
            update={
                "database_url": database_url,
                "upload_path": Path(directory),
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
            }
        )
        service = PostgresAsyncRAGService(evaluation_settings, embedder, reranker, None)
        try:
            _create_evaluation_knowledge_base(database_url, knowledge_base_id)
            for document in dataset.documents:
                service.index_document(document.filename, contents[document.filename], knowledge_base_id)
            worker = IndexWorker(evaluation_settings, embedder)
            while worker.run_once():
                pass

            failed = _failed_index_jobs(database_url, knowledge_base_id)
            if failed:
                raise RuntimeError(f"语料索引失败：{failed}")

            vector_rankings: dict[str, list[str]] = {}
            reranked_rankings: dict[str, list[str]] = {}
            for query in dataset.queries:
                candidates = service.store.query(
                    embedder.encode([query.question])[0], RETRIEVE_K, knowledge_base_id
                )
                vector_rankings[query.query_id] = [_position(item) for item in candidates]

                scores = reranker.score(query.question, [item.text for item in candidates])
                reranked = rank_candidates(candidates, scores, min(RERANK_K, len(candidates)))
                reranked_rankings[query.query_id] = [_position(item) for item in reranked]
            chunk_count = service.store.count(knowledge_base_id)
        finally:
            _delete_evaluation_knowledge_base(database_url, knowledge_base_id)

    metrics = evaluate_rankings(
        [_as_evaluation_query(query) for query in dataset.queries],
        vector_rankings,
        reranked_rankings,
    )
    run_at = datetime.now(UTC)
    report = RetrievalEvaluationReport(
        report_id=f"corpus-{run_at:%Y%m%dT%H%M%SZ}",
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        commit=commit,
        run_at=run_at,
        # 与 promote_official_report 保持同一原则：未通过冻结门槛的基线如实保留，
        # 但不标记 official，因此不会进入只读评测 API 的正式报告列表。
        official=False,
        models={
            "embedding": resolved_model(settings.embedding_model),
            "reranker": resolved_model(settings.reranker_model),
        },
        parameters={
            "retrieve_k": RETRIEVE_K,
            "rerank_k": RERANK_K,
            "distance": "cosine",
            "normalize_embeddings": True,
            "ranking_strategy": "minmax_weighted_fusion",
            "vector_score_weight": VECTOR_SCORE_WEIGHT,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "chunk_count": chunk_count,
            "metric_granularity": "paragraph",
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
    return report.model_copy(update={"official": report.passed})


def _require_empty_evaluation_database(database_url: str) -> None:
    """拒绝在含业务数据的数据库运行会写入临时语料的基线任务。"""

    with psycopg.connect(database_url) as connection:
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM knowledge_bases)"
        ).fetchone()
    if counts != (0, 0):
        raise RuntimeError("语料评测数据库必须为空；请勿指向开发或生产业务数据库")


def _create_evaluation_knowledge_base(database_url: str, knowledge_base_id: str) -> None:
    now = datetime.now(UTC)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO knowledge_bases
               (knowledge_base_id, name, name_normalized, description, is_default,
                created_at, updated_at)
               VALUES (%s, %s, %s, '', false, %s, %s)""",
            (knowledge_base_id, knowledge_base_id, knowledge_base_id, now, now),
        )


def _failed_index_jobs(database_url: str, knowledge_base_id: str) -> list[str]:
    with psycopg.connect(database_url) as connection:
        return [
            str(row[0] or "未知错误")
            for row in connection.execute(
                """SELECT failure_reason FROM index_jobs
                   WHERE knowledge_base_id = %s AND status = 'failed'""",
                (knowledge_base_id,),
            ).fetchall()
        ]


def _delete_evaluation_knowledge_base(database_url: str, knowledge_base_id: str) -> None:
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "UPDATE documents SET current_version_id = NULL WHERE knowledge_base_id = %s",
            (knowledge_base_id,),
        )
        connection.execute("DELETE FROM index_jobs WHERE knowledge_base_id = %s", (knowledge_base_id,))
        connection.execute(
            "DELETE FROM document_versions WHERE knowledge_base_id = %s", (knowledge_base_id,)
        )
        connection.execute("DELETE FROM documents WHERE knowledge_base_id = %s", (knowledge_base_id,))
        connection.execute("DELETE FROM data_sources WHERE knowledge_base_id = %s", (knowledge_base_id,))
        connection.execute(
            "DELETE FROM knowledge_bases WHERE knowledge_base_id = %s", (knowledge_base_id,)
        )


def _position(candidate: RetrievedChunk) -> str:
    """把召回分块折回它所属的原始段落，指标因此不受 chunk_id 命名影响。"""

    return paragraph_key(
        str(candidate.metadata["filename"]),
        int(candidate.metadata["paragraph"]),
    )


def _as_evaluation_query(query: CorpusQuery) -> EvaluationQuery:
    return EvaluationQuery(
        query_id=query.query_id,
        question=query.question,
        relevant_chunk_ids=[paragraph_key(item.filename, item.paragraph) for item in query.relevant],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--chunk-size", type=int)
    parser.add_argument("--chunk-overlap", type=int)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    args = parser.parse_args()

    settings = get_settings()
    baseline = None
    if args.baseline_report:
        baseline = RetrievalEvaluationReport.model_validate_json(
            args.baseline_report.read_text(encoding="utf-8")
        )
    dataset, contents = load_corpus_dataset(args.dataset)
    report = run_corpus_baseline(
        dataset,
        contents,
        args.commit,
        args.chunk_size if args.chunk_size is not None else settings.chunk_size,
        args.chunk_overlap if args.chunk_overlap is not None else settings.chunk_overlap,
        baseline,
        args.database_url,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    print(f"passed={str(report.passed).lower()}")


if __name__ == "__main__":
    main()
