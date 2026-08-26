"""逐条导出召回明细，用于定位失败到底出在模型、标注还是指标口径。

聚合指标只能告诉我们"召回不够好"，无法区分三种成因：语义确实匹配不上、
问句本身已丢失可定位信息、或者标注段落其实排在第 6~20 名而被 top-5 截断。
因此这里用比 ``RETRIEVE_K`` 更大的窗口检索，记录每个标注段落的真实名次。
"""

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from backend.app.config import get_settings
from backend.app.database import check_schema_version
from backend.app.models import get_embedding_model, get_reranker
from backend.app.postgres_documents import IndexWorker, PostgresAsyncRAGService

from .corpus_dataset import load_corpus_dataset, paragraph_key

# 复用基线运行器的临时知识库管理，避免两套建库与清理逻辑产生差异。
from .run_corpus_baseline import (
    _create_evaluation_knowledge_base,
    _delete_evaluation_knowledge_base,
    _position,
    _require_empty_evaluation_database,
    _unfinished_index_jobs,
)

DIAGNOSE_K = 50


def diagnose(
    dataset_path: Path,
    database_url: str,
    retrieval_mode: str,
    diagnose_k: int = DIAGNOSE_K,
) -> dict:
    settings = get_settings()
    check_schema_version(database_url, settings.required_database_schema_version)
    _require_empty_evaluation_database(database_url)
    dataset, contents = load_corpus_dataset(dataset_path)
    embedder = get_embedding_model()
    reranker = get_reranker()
    knowledge_base_id = f"kb_diag_{uuid4().hex[:20]}"

    records = []
    with TemporaryDirectory(prefix="rag-enterprise-diagnose-") as directory:
        evaluation_settings = settings.model_copy(
            update={"database_url": database_url, "upload_path": Path(directory)}
        )
        service = PostgresAsyncRAGService(evaluation_settings, embedder, reranker, None)
        try:
            _create_evaluation_knowledge_base(database_url, knowledge_base_id)
            for document in dataset.documents:
                service.index_document(
                    document.filename, contents[document.filename], knowledge_base_id
                )
            worker = IndexWorker(evaluation_settings, embedder)
            while worker.run_once():
                pass
            unfinished = _unfinished_index_jobs(database_url, knowledge_base_id)
            if unfinished:
                raise RuntimeError(f"语料索引未全部成功：{unfinished}")

            for query in dataset.queries:
                embedding = embedder.encode([query.question])[0]
                candidates = service.retrieve_candidates(
                    query.question, embedding, diagnose_k, knowledge_base_id, retrieval_mode
                )
                positions = [_position(item) for item in candidates]
                expected = [paragraph_key(item.filename, item.paragraph) for item in query.relevant]
                ranks = {
                    key: (positions.index(key) + 1 if key in positions else None) for key in expected
                }
                best = [rank for rank in ranks.values() if rank is not None]
                records.append(
                    {
                        "query_id": query.query_id,
                        "question": query.question,
                        "expected": expected,
                        "ranks": ranks,
                        "best_rank": min(best) if best else None,
                        "top5": positions[:5],
                    }
                )
        finally:
            _delete_evaluation_knowledge_base(database_url, knowledge_base_id)

    return {
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "retrieval_mode": retrieval_mode,
        "embedding_model": settings.embedding_model,
        "diagnose_k": diagnose_k,
        "query_count": len(records),
        "buckets": _bucket(records, diagnose_k),
        "records": records,
    }


def _bucket(records: list[dict], diagnose_k: int) -> dict[str, int]:
    """把每条问题按最佳标注段落的名次分档，用于判断失败是否只是被 top-5 截断。"""

    counter: Counter[str] = Counter()
    for record in records:
        rank = record["best_rank"]
        if rank is None:
            counter[f"未进前{diagnose_k}"] += 1
        elif rank <= 5:
            counter["前5命中"] += 1
        elif rank <= 10:
            counter["第6-10名"] += 1
        elif rank <= 20:
            counter["第11-20名"] += 1
        else:
            counter[f"第21-{diagnose_k}名"] += 1
    return dict(counter)


def _print_report(result: dict, show: int) -> None:
    print(f"数据集 {result['dataset_id']} {result['dataset_version']}")
    print(f"模式 {result['retrieval_mode']} / 模型 {result['embedding_model']}")
    print(f"问题总数 {result['query_count']}，检索窗口 {result['diagnose_k']}")
    print()
    print("名次分布：")
    total = result["query_count"]
    for name, count in sorted(result["buckets"].items(), key=lambda item: -item[1]):
        print(f"  {name:14s} {count:4d}  ({count / total:.1%})")
    print()
    misses = [item for item in result["records"] if item["best_rank"] is None]
    print(f"完全未召回的问题（前 {show} 条，共 {len(misses)} 条）：")
    for record in misses[:show]:
        print(f"  {record['query_id']}  {record['question']}")
        print(f"      标注 {', '.join(record['expected'])}")
        print(f"      实得 {', '.join(record['top5'][:3])}")
    print()
    truncated = [
        item for item in result["records"] if item["best_rank"] is not None and item["best_rank"] > 5
    ]
    print(f"仅被 top-5 截断的问题（前 {show} 条，共 {len(truncated)} 条）：")
    for record in sorted(truncated, key=lambda item: item["best_rank"])[:show]:
        print(f"  {record['query_id']}  第 {record['best_rank']} 名  {record['question']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--retrieval-mode", default="vector")
    parser.add_argument("--diagnose-k", type=int, default=DIAGNOSE_K)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--show", type=int, default=12)
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit("必须通过 --database-url 或 DATABASE_URL 指定隔离数据库")
    result = diagnose(args.dataset, args.database_url, args.retrieval_mode, args.diagnose_k)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    _print_report(result, args.show)


if __name__ == "__main__":
    main()
