"""使用真实解析与切分管线生成语料级检索基线。

与 1.0.0 的 ``run_baseline`` 不同，这里的候选分块由 ``parse_document`` 和
``split_sections`` 现场产出，因此解析实现、chunk_size 与 overlap 都在被测范围内。
指标按段落粒度统计：命中同一段落的多个分块会各自占用 top-k 名额，切分过碎会
直接反映为 recall 与 MRR 下降。
"""

import argparse
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.chunking import chunking_version
from backend.app.config import get_settings
from backend.app.database import check_schema_version
from backend.app.index_versions import component_manifest, config_fingerprint
from backend.app.models import get_embedding_model, get_reranker
from backend.app.postgres_documents import IndexWorker, PostgresAsyncRAGService
from backend.app.ranking import VECTOR_SCORE_WEIGHT, rank_candidates
from backend.app.retrieval_access import RetrievalAccessContext
from backend.app.schemas import QueryMetadataFilter
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
RECALL_AT_10_THRESHOLD = 0.85
NDCG_AT_10_THRESHOLD = 0.70

RETRIEVE_K = 10
RERANK_K = 10

# vector 为既有单路基线；lexical 单独衡量 BM25；hybrid 用 RRF 合并两路名次。
RETRIEVAL_MODES = ("vector", "lexical", "hybrid")


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
    retrieval_mode: str = "vector",
    retrieve_k: int = RETRIEVE_K,
    official: bool = False,
    on_stage: Callable[[str], None] | None = None,
) -> RetrievalEvaluationReport:
    """跑一次语料级检索基线。

    ``official`` 表示这次运行本身是否可信，与指标有没有达到阈值无关。之前这里执行的是
    ``official = passed``：未达标的报告一律不标 official，于是 ``load_official_model``
    找不到它，页面上「执行三层验证」永远只能选到已通过绝对阈值的报告。可发布与否本来
    应该由三层门禁给结论（配置指纹一致、证据完整、相对基线不回退），把绝对阈值提前
    当成来源可信度的判据，等于让门禁失去了「跑过但没达标」这个真实结论。

    只有受控的正式运行才允许传 ``official=True``：调用方要保证数据集在服务端白名单里、
    评测库是隔离的空库、配置来自候选版本的冻结快照。命令行手跑与测试替身保持默认
    ``False``——它们证明不了这几件事。

    ``on_stage`` 在每个阶段真正开始时被调用一次，用于把长任务进度投影到 ``operations``。
    阶段名与 ``index_evaluation_runs.EVALUATION_STAGES`` 逐字对应；不传时整个函数行为
    与之前完全一致。
    """

    notify = on_stage or (lambda _stage: None)
    settings = get_settings()
    if retrieval_mode not in RETRIEVAL_MODES:
        raise ValueError(f"retrieval_mode 必须是 {RETRIEVAL_MODES} 之一")
    if retrieve_k < RERANK_K:
        raise ValueError(f"retrieve_k 不能小于 rerank_k（{RERANK_K}）")
    database_url = database_url or settings.database_url
    if not database_url:
        raise ValueError("语料评测必须通过 --database-url 或 DATABASE_URL 指定隔离数据库")
    notify("prepare_dataset")
    check_schema_version(database_url, settings.required_database_schema_version)
    _require_empty_evaluation_database(database_url)
    embedder = embedder or get_embedding_model()
    reranker = reranker or get_reranker()
    # 口径必须与 IndexWorker 写入索引版本的那份逐字一致（postgres_documents.py 中
    # active_or_bootstrap_version 的调用处），否则切换放行时的指纹比对永远不成立。
    fingerprint = config_fingerprint(
        chunking_version(chunk_size, chunk_overlap),
        embedder.model_name,
        len(embedder.encode(["维度探测"])[0]),
        {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap},
        component_manifest(reranker_model=settings.reranker_model),
    )
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
            notify("build_corpus")
            _create_evaluation_knowledge_base(database_url, knowledge_base_id)
            for document in dataset.documents:
                service.index_document(document.filename, contents[document.filename], knowledge_base_id)
            worker = IndexWorker(evaluation_settings, embedder)
            while worker.run_once():
                pass

            unfinished = _unfinished_index_jobs(database_url, knowledge_base_id)
            if unfinished:
                raise RuntimeError(f"语料索引未全部成功：{unfinished}")

            # 召回与精排分两趟，而不是在一个循环里交替：两个阶段各自完整跑完，
            # 报告的进度才真的对应「现在在做召回」还是「现在在做精排」。合成一趟时
            # 无论怎么标记阶段都是猜的——第一个问题一进循环，两个阶段就同时开始了。
            notify("retrieve")
            vector_rankings: dict[str, list[str]] = {}
            candidates_by_query: dict[str, list[RetrievedChunk]] = {}
            for query in dataset.queries:
                embedding = embedder.encode([query.question])[0]
                # 与在线查询共用同一份召回实现，两个入口不会得出不同的质量结论。
                candidates = service.retrieve_candidates(
                    query.question,
                    embedding,
                    retrieve_k,
                    knowledge_base_id,
                    retrieval_mode,
                )
                # 在 lexical/hybrid 模式下这一路记录的是召回阶段的融合名次，
                # 因此报告里的 vector_mrr 应结合 parameters.retrieval_mode 解读。
                vector_rankings[query.query_id] = [_position(item) for item in candidates]
                candidates_by_query[query.query_id] = candidates

            notify("rerank")
            reranked_rankings: dict[str, list[str]] = {}
            for query in dataset.queries:
                candidates = candidates_by_query[query.query_id]
                # 纯词法模式下可能一个词元都匹配不上，此时该问题的两项指标均计 0，
                # 而不是让空候选传进精排。
                if candidates:
                    scores = reranker.score(query.question, [item.text for item in candidates])
                    reranked = rank_candidates(candidates, scores, min(RERANK_K, len(candidates)))
                else:
                    reranked = []
                reranked_rankings[query.query_id] = [_position(item) for item in reranked]
            metadata_filter_accuracy, acl_leak_count = _governance_quality_probes(
                database_url,
                service,
                embedder,
                knowledge_base_id,
                retrieval_mode,
                retrieve_k,
            )
            chunk_count = service.store.count(knowledge_base_id)
        finally:
            _delete_evaluation_knowledge_base(database_url, knowledge_base_id)

    notify("calculate_metrics")
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
        # 由调用方声明这次运行是否受控，不由指标是否达标推导（见函数 docstring）。
        official=official,
        models={
            "embedding": resolved_model(settings.embedding_model),
            "reranker": resolved_model(settings.reranker_model),
        },
        parameters={
            "retrieve_k": retrieve_k,
            "rerank_k": RERANK_K,
            "distance": "cosine",
            "normalize_embeddings": True,
            "ranking_strategy": (
                "minmax_weighted_fusion"
                if retrieval_mode == "vector"
                else f"rrf_recall_then_minmax_weighted_fusion({retrieval_mode})"
            ),
            "vector_score_weight": VECTOR_SCORE_WEIGHT,
            "retrieval_mode": retrieval_mode,
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
        recall_at_10=assess_metric(
            metrics.recall_at_10,
            RECALL_AT_10_THRESHOLD,
            baseline.recall_at_10.value if baseline and baseline.recall_at_10 else None,
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
        rerank_recall_at_5=assess_metric(
            metrics.rerank_recall_at_5,
            RECALL_AT_5_THRESHOLD,
            baseline.rerank_recall_at_5.value
            if baseline and baseline.rerank_recall_at_5
            else None,
        ),
        ndcg_at_5=assess_metric(metrics.ndcg_at_5, 0.70),
        ndcg_at_10=assess_metric(
            metrics.ndcg_at_10,
            NDCG_AT_10_THRESHOLD,
            baseline.ndcg_at_10.value if baseline and baseline.ndcg_at_10 else None,
        ),
        metadata_filter_accuracy=assess_metric(
            metadata_filter_accuracy,
            1.0,
            (
                baseline.metadata_filter_accuracy.value
                if baseline and baseline.metadata_filter_accuracy
                else None
            ),
        ),
        acl_leak_count=acl_leak_count,
        config_fingerprint=fingerprint,
    )
    return report


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


def _unfinished_index_jobs(database_url: str, knowledge_base_id: str) -> list[str]:
    """列出未成功完成的索引任务。

    只查 ``failed`` 会漏掉重试中停在 ``queued`` 的任务——那种情况下评测会安静地
    产出一份 chunk_count=0 的报告，比直接失败更危险。
    """

    with psycopg.connect(database_url) as connection:
        return [
            f"{row[0]}: {row[1] or '无失败原因'}"
            for row in connection.execute(
                """SELECT status, failure_reason FROM index_jobs
                   WHERE knowledge_base_id = %s AND status <> 'succeeded'
                   ORDER BY index_job_id""",
                (knowledge_base_id,),
            ).fetchall()
        ]


def _governance_quality_probes(
    database_url: str,
    service: PostgresAsyncRAGService,
    embedder: Any,
    knowledge_base_id: str,
    retrieval_mode: str,
    retrieve_k: int,
) -> tuple[float, int]:
    """通过线上同款召回路径验证 Metadata filter 与 ACL deny。

    评测知识库是本次运行独占的临时数据，因此可以给一个真实文档临时写入探针标签与
    deny 用户。这里不直接检查 SQL：只有调用 ``retrieve_candidates``，才能同时覆盖
    向量、词法、Hybrid、元数据过滤和访问控制在服务层的组合行为。
    """

    probe_tag = "evaluation-metadata-probe"
    missing_tag = "evaluation-metadata-probe-missing"
    denied_user = "evaluation-denied-user"
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.transaction():
        protected = connection.execute(
            """SELECT c.metadata->>'document_id' AS document_id, c.content
               FROM chunks c
               JOIN knowledge_bases kb
                 ON kb.knowledge_base_id=c.knowledge_base_id
                AND kb.active_index_version_id=c.index_version_id
               WHERE c.knowledge_base_id=%s
               ORDER BY c.chunk_id
               LIMIT 1""",
            (knowledge_base_id,),
        ).fetchone()
        if protected is None:
            raise RuntimeError("无法执行治理质量探针：评测索引没有可检索分块")
        document_id = str(protected["document_id"])
        question = str(protected["content"])[:500]
        connection.execute(
            """UPDATE chunks
               SET metadata=metadata || %s
               WHERE knowledge_base_id=%s
                 AND index_version_id=(
                     SELECT active_index_version_id FROM knowledge_bases
                     WHERE knowledge_base_id=%s)
                 AND metadata->>'document_id'=%s""",
            (
                Jsonb({"tags": [probe_tag]}),
                knowledge_base_id,
                knowledge_base_id,
                document_id,
            ),
        )

    embedding = embedder.encode([question])[0]
    tagged = service.retrieve_candidates(
        question,
        embedding,
        retrieve_k,
        knowledge_base_id,
        retrieval_mode,
        filters=QueryMetadataFilter(tags=[probe_tag]),
    )
    missing = service.retrieve_candidates(
        question,
        embedding,
        retrieve_k,
        knowledge_base_id,
        retrieval_mode,
        filters=QueryMetadataFilter(tags=[missing_tag]),
    )
    metadata_checks = (
        bool(tagged),
        bool(tagged) and all(str(item.metadata.get("document_id")) == document_id for item in tagged),
        not missing,
    )
    metadata_filter_accuracy = sum(metadata_checks) / len(metadata_checks)

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """UPDATE chunks
               SET metadata=metadata || %s
               WHERE knowledge_base_id=%s
                 AND index_version_id=(
                     SELECT active_index_version_id FROM knowledge_bases
                     WHERE knowledge_base_id=%s)
                 AND metadata->>'document_id'=%s""",
            (
                Jsonb({"deny_user_ids": [denied_user]}),
                knowledge_base_id,
                knowledge_base_id,
                document_id,
            ),
        )

    denied = service.retrieve_candidates(
        question,
        embedding,
        retrieve_k,
        knowledge_base_id,
        retrieval_mode,
        access=RetrievalAccessContext(denied_user),
    )
    acl_leak_count = sum(
        str(item.metadata.get("document_id")) == document_id for item in denied
    )
    return metadata_filter_accuracy, acl_leak_count


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
    parser.add_argument("--retrieval-mode", choices=RETRIEVAL_MODES, default="vector")
    parser.add_argument("--retrieve-k", type=int, default=RETRIEVE_K)
    # 命令行默认不标 official：手跑的运行证明不了数据集、评测库与配置都是受控的。
    # 要产出可用于三层验证的正式报告，走产品内的「运行正式评测」，由 Evaluation Worker
    # 在校验完这几件事之后执行。
    parser.add_argument("--official", action="store_true")
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
        retrieval_mode=args.retrieval_mode,
        retrieve_k=args.retrieve_k,
        official=args.official,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    print(f"official={str(report.official).lower()}")
    print(f"passed={str(report.passed).lower()}")


if __name__ == "__main__":
    main()
