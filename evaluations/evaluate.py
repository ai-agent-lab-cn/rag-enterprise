"""Evaluate retrieval and reranking quality against a small labelled question set."""

import json
from pathlib import Path

from backend.app.config import get_settings
from backend.app.models import get_embedding_model, get_reranker
from backend.app.store import ChromaStore


def reciprocal_rank(results, expected_filename: str, expected_paragraph: int) -> float:
    for rank, result in enumerate(results, start=1):
        if (
            result.metadata.get("filename") == expected_filename
            and result.metadata.get("paragraph") == expected_paragraph
        ):
            return 1.0 / rank
    return 0.0


def main() -> None:
    settings = get_settings()
    store = ChromaStore(settings.chroma_path, settings.collection_name, settings.embedding_model)
    embedder = get_embedding_model()
    reranker = get_reranker()
    cases = json.loads(Path("evaluations/questions.json").read_text(encoding="utf-8"))
    retrieval_rr: list[float] = []
    rerank_rr: list[float] = []

    for case in cases:
        results = store.query(embedder.encode([case["question"]])[0], limit=10)
        retrieval_rr.append(reciprocal_rank(results, case["expected_filename"], case["expected_paragraph"]))
        scores = reranker.score(case["question"], [result.text for result in results])
        for result, score in zip(results, scores, strict=True):
            result.rerank_score = score
        ranked = sorted(results, key=lambda item: item.rerank_score, reverse=True)
        rerank_rr.append(reciprocal_rank(ranked, case["expected_filename"], case["expected_paragraph"]))

    total = len(cases)
    print(f"cases={total}")
    print(f"vector_recall@10={sum(score > 0 for score in retrieval_rr) / total:.3f}")
    print(f"vector_mrr={sum(retrieval_rr) / total:.3f}")
    print(f"reranked_mrr={sum(rerank_rr) / total:.3f}")


if __name__ == "__main__":
    main()
