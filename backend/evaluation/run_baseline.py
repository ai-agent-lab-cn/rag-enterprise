"""使用真实模型和隔离 pgvector 数据库生成正式检索质量基线。

1.0.0 数据集的候选分块是写死的，解析与切分本就不在被测范围，因此这里把冻结分块直接
写入 ``chunks`` 表，不经过 ``index_document`` 的解析与切分链路——让解析实现混进指标
会与该数据集的设计意图相反。被测的是 embedding、pgvector 向量召回、CrossEncoder 精排
与 15/85 融合排序。

存储层从 ChromaStore 迁到 pgvector。迁移前该模块已无法运行：``_evaluation_chunk``
构造 ``Chunk`` 时缺少 V3 引入的 ``knowledge_base_id``，而该模块没有任何测试或 CI 覆盖，
因此这个 TypeError 静默存在了一个大版本。

``seed_evaluation_chunks`` 与 ``drop_evaluation_knowledge_base`` 是公开的，
``evaluations/evaluate.py`` 复用它们构造隔离评测环境——那段关联行 SQL 抄第二份会在
下次 schema 变更时漂移。
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from huggingface_hub import HfApi
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from backend.app.chunking import Chunk, chunking_version
from backend.app.config import get_settings
from backend.app.database import check_schema_version
from backend.app.index_versions import config_fingerprint
from backend.app.models import get_embedding_model, get_reranker
from backend.app.postgres_documents import PostgresVectorStore
from backend.app.ranking import VECTOR_SCORE_WEIGHT, rank_candidates

from .dataset import EvaluationDataset, load_dataset
from .metrics import evaluate_rankings
from .report import RetrievalEvaluationReport, assess_metric

RECALL_AT_5_THRESHOLD = 0.80
VECTOR_MRR_THRESHOLD = 0.60
RERANK_MRR_THRESHOLD = 0.70

EVALUATION_CHUNK_SIZE = 700
EVALUATION_CHUNK_OVERLAP = 100


def run_baseline(
    dataset: EvaluationDataset,
    commit: str,
    baseline: RetrievalEvaluationReport | None = None,
    database_url: str | None = None,
    embedder=None,
    reranker=None,
) -> RetrievalEvaluationReport:
    """运行一次真实检索基线，并返回带完整上下文的正式报告。

    评测只验证 embedding、pgvector 向量召回和 CrossEncoder 精排；生成模型不参与
    V2 指标。传入的 commit 应指向被评测的应用基线，而不是报告文件自身的提交。
    """

    settings = get_settings()
    database_url = database_url or settings.database_url
    if not database_url:
        raise ValueError("检索基线必须通过 --database-url 或 DATABASE_URL 指定隔离数据库")
    check_schema_version(database_url, settings.required_database_schema_version)
    embedder = embedder or get_embedding_model()
    reranker = reranker or get_reranker()

    knowledge_base_id = f"kb_baseline_{uuid4().hex[:16]}"
    chunks = [
        _evaluation_chunk(item, index, knowledge_base_id)
        for index, item in enumerate(dataset.chunks)
    ]
    embeddings = embedder.encode([chunk.text for chunk in chunks])

    # 隔离知识库在运行结束后清理，避免污染同一数据库里的其他评测数据。
    seed_evaluation_chunks(
        database_url, knowledge_base_id, chunks, embeddings, embedder.model_name
    )
    try:
        store = PostgresVectorStore(database_url, Path("."))
        vector_rankings: dict[str, list[str]] = {}
        reranked_rankings: dict[str, list[str]] = {}
        for query in dataset.queries:
            # 先保存向量召回的原始顺序，向量 MRR 必须在精排改序前计算。
            candidates = store.query(
                embedder.encode([query.question])[0], 10, knowledge_base_id
            )
            vector_rankings[query.query_id] = [candidate.chunk_id for candidate in candidates]

            # CrossEncoder 只对同一批候选重新排序，不能补召回向量阶段遗漏的分块。
            scores = reranker.score(query.question, [candidate.text for candidate in candidates])
            reranked = rank_candidates(candidates, scores, 5)
            reranked_rankings[query.query_id] = [candidate.chunk_id for candidate in reranked]
    finally:
        drop_evaluation_knowledge_base(database_url, knowledge_base_id)

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
            "embedding": resolved_model(settings.embedding_model),
            "reranker": resolved_model(settings.reranker_model),
        },
        parameters={
            "retrieve_k": 10,
            "rerank_k": 5,
            "distance": "cosine",
            "normalize_embeddings": True,
            "ranking_strategy": "minmax_weighted_fusion",
            "vector_score_weight": VECTOR_SCORE_WEIGHT,
            "vector_store": "pgvector",
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


def _evaluation_chunk(item, index: int, knowledge_base_id: str) -> Chunk:
    """把版本化评测分块转换成应用 Chunk。"""

    return Chunk(
        chunk_id=item.chunk_id,
        knowledge_base_id=knowledge_base_id,
        document_id=item.document_id,
        filename=item.filename,
        text=item.text,
        page=None,
        paragraph=index,
        chunk_index=index,
        char_count=len(item.text),
        summary=item.text[:80],
    )


def seed_evaluation_chunks(
    database_url: str,
    knowledge_base_id: str,
    chunks: list[Chunk],
    embeddings: list[list[float]],
    embedding_model: str,
) -> None:
    """在隔离知识库里直接写入冻结分块，构造检索读路径需要的全部关联行。

    读路径要 JOIN documents 与 data_sources，并按 active 索引版本过滤，因此这些行
    必须齐备，否则查询恒为空。
    """

    now = datetime.now(UTC)
    dimension = len(embeddings[0])
    data_source_id = f"ds_{uuid4().hex[:16]}"
    index_version_id = f"iv_{uuid4().hex[:16]}"
    options = {"chunk_size": EVALUATION_CHUNK_SIZE, "chunk_overlap": EVALUATION_CHUNK_OVERLAP}
    with psycopg.connect(database_url) as connection:
        register_vector(connection)
        with connection.transaction():
            connection.execute(
                """INSERT INTO knowledge_bases
                   (knowledge_base_id, name, name_normalized, description, is_default,
                    created_at, updated_at)
                   VALUES (%s, %s, %s, '', false, %s, %s)""",
                (knowledge_base_id, knowledge_base_id, knowledge_base_id, now, now),
            )
            connection.execute(
                """INSERT INTO index_versions
                   (index_version_id, knowledge_base_id, status, chunking_version,
                    parser_version, embedding_model, embedding_dimension, processing_options,
                    config_fingerprint, evaluation_report_id, activated_at)
                   VALUES (%s, %s, 'active', %s, 'frozen-dataset', %s, %s, %s, %s,
                           'frozen-dataset', %s)""",
                (
                    index_version_id,
                    knowledge_base_id,
                    chunking_version(EVALUATION_CHUNK_SIZE, EVALUATION_CHUNK_OVERLAP),
                    embedding_model,
                    dimension,
                    Jsonb(options),
                    config_fingerprint(
                        chunking_version(EVALUATION_CHUNK_SIZE, EVALUATION_CHUNK_OVERLAP),
                        embedding_model,
                        dimension,
                        options,
                    ),
                    now,
                ),
            )
            connection.execute(
                "UPDATE knowledge_bases SET active_index_version_id=%s WHERE knowledge_base_id=%s",
                (index_version_id, knowledge_base_id),
            )
            connection.execute(
                """INSERT INTO data_sources
                   (data_source_id, knowledge_base_id, source_type, name, created_at, updated_at)
                   VALUES (%s, %s, 'file', %s, %s, %s)""",
                (data_source_id, knowledge_base_id, "frozen-dataset", now, now),
            )
            seen: dict[str, str] = {}
            for chunk in chunks:
                if chunk.document_id in seen:
                    continue
                version_id = f"ver_{uuid4().hex[:16]}"
                seen[chunk.document_id] = version_id
                connection.execute(
                    """INSERT INTO documents
                       (document_id, knowledge_base_id, data_source_id, filename,
                        created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (chunk.document_id, knowledge_base_id, data_source_id, chunk.filename, now, now),
                )
                connection.execute(
                    """INSERT INTO document_versions
                       (document_version_id, knowledge_base_id, document_id, version_number,
                        content_sha256, source_file_bytes, source_path, status, created_at,
                        indexed_at, chunking_version, parser_version)
                       VALUES (%s, %s, %s, 1, %s, 0, %s, 'ready', %s, %s, %s, 'frozen-dataset')""",
                    (
                        version_id,
                        knowledge_base_id,
                        chunk.document_id,
                        f"{abs(hash(chunk.document_id)):064x}"[:64],
                        chunk.filename,
                        now,
                        now,
                        chunking_version(EVALUATION_CHUNK_SIZE, EVALUATION_CHUNK_OVERLAP),
                    ),
                )
                connection.execute(
                    """UPDATE documents SET current_version_id=%s
                       WHERE knowledge_base_id=%s AND document_id=%s""",
                    (version_id, knowledge_base_id, chunk.document_id),
                )
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                connection.execute(
                    """INSERT INTO chunks
                       (chunk_id, document_version_id, index_version_id, knowledge_base_id,
                        chunk_index, content, metadata, embedding, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        chunk.chunk_id,
                        seen[chunk.document_id],
                        index_version_id,
                        knowledge_base_id,
                        chunk.chunk_index,
                        chunk.text,
                        Jsonb(chunk.metadata()),
                        embedding,
                        now,
                    ),
                )


def drop_evaluation_knowledge_base(database_url: str, knowledge_base_id: str) -> None:
    """清理临时评测数据。索引版本随知识库级联删除，分块与文档需显式删。"""

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("DELETE FROM chunks WHERE knowledge_base_id = %s", (knowledge_base_id,))
        connection.execute(
            "UPDATE documents SET current_version_id = NULL WHERE knowledge_base_id = %s",
            (knowledge_base_id,),
        )
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id = NULL WHERE knowledge_base_id = %s",
            (knowledge_base_id,),
        )
        connection.execute(
            "DELETE FROM document_versions WHERE knowledge_base_id = %s", (knowledge_base_id,)
        )
        connection.execute(
            "DELETE FROM documents WHERE knowledge_base_id = %s", (knowledge_base_id,)
        )
        connection.execute(
            "DELETE FROM data_sources WHERE knowledge_base_id = %s", (knowledge_base_id,)
        )
        connection.execute(
            "DELETE FROM knowledge_bases WHERE knowledge_base_id = %s", (knowledge_base_id,)
        )


def resolved_model(model_name: str) -> str:
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
    parser.add_argument("--database-url")
    parser.add_argument("--baseline-report", type=Path)
    args = parser.parse_args()

    baseline = None
    if args.baseline_report:
        baseline = RetrievalEvaluationReport.model_validate_json(
            args.baseline_report.read_text(encoding="utf-8")
        )
    report = run_baseline(
        load_dataset(args.dataset), args.commit, baseline, database_url=args.database_url
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
