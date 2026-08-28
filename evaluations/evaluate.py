"""用标注问题集评测向量召回与 CrossEncoder 精排质量。

评测在隔离知识库里自建索引：解析 ``knowledge/project-profile.md``、切分、嵌入后写入
pgvector，跑完立即清理。迁到 pgvector 之前这里读的是开发者本地的 Chroma 持久化目录，
指标取决于本地索引过什么；目录不存在时会被建成空 collection，四个问题全部落空，脚本
仍旧打印 0.000 而不报错。

``questions.json`` 的标注是 (filename, paragraph)，paragraph 由 Markdown 解析器编号，
因此解析与切分必须留在链路里，不能像 ``backend/evaluation/run_baseline.py`` 那样直接
写入冻结分块。写库与清理复用它的实现，避免同一套关联行 SQL 在两处各写一遍。
"""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from backend.app.chunking import split_sections
from backend.app.config import get_settings
from backend.app.database import check_schema_version
from backend.app.models import get_embedding_model, get_reranker
from backend.app.parsers import parse_structured_document
from backend.app.postgres_documents import PostgresVectorStore
from backend.app.store import RetrievedChunk
from backend.evaluation.run_baseline import (
    EVALUATION_CHUNK_OVERLAP,
    EVALUATION_CHUNK_SIZE,
    drop_evaluation_knowledge_base,
    seed_evaluation_chunks,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.json"
DOCUMENT_PATH = REPO_ROOT / "knowledge" / "project-profile.md"
RETRIEVE_K = 10


def reciprocal_rank(results, expected_filename: str, expected_paragraph: int) -> float:
    for rank, result in enumerate(results, start=1):
        if (
            result.metadata.get("filename") == expected_filename
            and result.metadata.get("paragraph") == expected_paragraph
        ):
            return 1.0 / rank
    return 0.0


def evaluate(database_url: str, embedder=None, reranker=None) -> dict[str, float]:
    """在隔离知识库上跑一次评测，返回三项指标。

    模型可注入，测试因此不必下载真实权重。
    """

    settings = get_settings()
    check_schema_version(database_url, settings.required_database_schema_version)
    embedder = embedder or get_embedding_model()
    reranker = reranker or get_reranker()
    cases = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))

    knowledge_base_id = f"kb_eval_{uuid4().hex[:16]}"
    parsed = parse_structured_document(DOCUMENT_PATH.name, DOCUMENT_PATH.read_bytes())
    chunks = split_sections(
        DOCUMENT_PATH.stem,
        DOCUMENT_PATH.name,
        parsed.sections,
        # 固定切分配置而不读 settings：指标必须与开发者本地 .env 无关。
        EVALUATION_CHUNK_SIZE,
        EVALUATION_CHUNK_OVERLAP,
        knowledge_base_id,
    )
    if not chunks:
        raise RuntimeError(f"评测语料没有产出任何分块：{DOCUMENT_PATH}")
    embeddings = embedder.encode([chunk.text for chunk in chunks])

    seed_evaluation_chunks(
        database_url, knowledge_base_id, chunks, embeddings, embedder.model_name
    )
    try:
        store = PostgresVectorStore(database_url, settings.upload_path)
        # 读路径按 active 索引版本过滤，写入成功不代表检索得到；对不上就是链路断了，
        # 不能让它退化成一份全 0 的指标。
        indexed = store.count(knowledge_base_id)
        if indexed != len(chunks):
            raise RuntimeError(f"隔离知识库可检索分块数为 {indexed}，写入的是 {len(chunks)}")
        retrieval_rr: list[float] = []
        rerank_rr: list[float] = []
        for case in cases:
            results = store.query(
                embedder.encode([case["question"]])[0], RETRIEVE_K, knowledge_base_id
            )
            retrieval_rr.append(
                reciprocal_rank(results, case["expected_filename"], case["expected_paragraph"])
            )
            scores = reranker.score(case["question"], [result.text for result in results])
            ranked = _reranked(results, scores)
            rerank_rr.append(
                reciprocal_rank(ranked, case["expected_filename"], case["expected_paragraph"])
            )
    finally:
        drop_evaluation_knowledge_base(database_url, knowledge_base_id)

    total = len(cases)
    return {
        "cases": float(total),
        "vector_recall@10": sum(score > 0 for score in retrieval_rr) / total,
        "vector_mrr": sum(retrieval_rr) / total,
        "reranked_mrr": sum(rerank_rr) / total,
    }


def _reranked(results: list[RetrievedChunk], scores: list[float]) -> list[RetrievedChunk]:
    """按精排分重排同一批候选；RetrievedChunk 是 dataclass，就地写回分数。"""

    for result, score in zip(results, scores, strict=True):
        result.rerank_score = score
    return sorted(results, key=lambda item: item.rerank_score, reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="标注问题集上的检索与精排质量评测")
    parser.add_argument("--database-url")
    args = parser.parse_args()

    database_url = args.database_url or get_settings().database_url
    if not database_url:
        raise ValueError("评测必须通过 --database-url 或 DATABASE_URL 指定数据库")
    metrics = evaluate(database_url)
    print(f"cases={int(metrics['cases'])}")
    print(f"vector_recall@10={metrics['vector_recall@10']:.3f}")
    print(f"vector_mrr={metrics['vector_mrr']:.3f}")
    print(f"reranked_mrr={metrics['reranked_mrr']:.3f}")


if __name__ == "__main__":
    main()
