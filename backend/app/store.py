"""检索结果的传输类型。

原本这里还有 ChromaStore。Chroma 运行时移除后，向量存储只剩 pgvector 一种实现
（``postgres_documents.PostgresVectorStore``），本模块因此只保留跨层传递的结果类型。
模块名与 import 路径保持不变，避免为一次删除去改十余处引用。
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    retrieval_score: float
    rerank_score: float = 0.0
    # 命中该候选的召回通路。默认只有向量，混合召回会在融合阶段改写。
    channels: tuple[str, ...] = ("vector",)
    lexical_score: float | None = None
    # 兼容已发布的 V5-1 融合接口；新链路优先读取 channels。
    vector_score: float | None = None
    retrieval_methods: list[str] | None = None
    query_match_count: int = 1
