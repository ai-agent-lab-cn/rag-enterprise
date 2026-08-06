from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb

from .chunking import Chunk


# ChromaDB 持久化、查询、列出和删除
@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    retrieval_score: float
    rerank_score: float = 0.0


class ChromaStore:
    def __init__(self, path: Path, collection_name: str, embedding_model_name: str):
        path.mkdir(parents=True, exist_ok=True)
        self.collection = chromadb.PersistentClient(path=str(path)).get_or_create_collection(
            name=collection_name,
            metadata={"embedding_model": embedding_model_name, "hnsw:space": "cosine"},
        )
        stored_model = (self.collection.metadata or {}).get("embedding_model")
        if stored_model != embedding_model_name:
            raise RuntimeError(
                f"索引使用 {stored_model}，当前配置为 {embedding_model_name}。请清空索引后重新入库。"
            )

    def upsert(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")
        self.collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            embeddings=embeddings,
            metadatas=[chunk.metadata() for chunk in chunks],
        )

    def query(self, embedding: list[float], limit: int) -> list[RetrievedChunk]:
        if self.collection.count() == 0:
            return []
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=min(limit, self.collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        return [
            RetrievedChunk(
                chunk_id=chunk_id,
                text=text,
                metadata=dict(metadata or {}),
                retrieval_score=round(1.0 - float(distance), 6),
            )
            for chunk_id, text, metadata, distance in zip(ids, documents, metadatas, distances, strict=True)
        ]

    def list_documents(self) -> list[dict[str, Any]]:
        result = self.collection.get(include=["metadatas"])
        grouped: dict[str, dict[str, Any]] = {}
        for metadata in result.get("metadatas") or []:
            if not metadata:
                continue
            document_id = str(metadata["document_id"])
            item = grouped.setdefault(
                document_id,
                {
                    "document_id": document_id,
                    "filename": metadata["filename"],
                    "chunk_count": 0,
                    "status": "ready",
                },
            )
            item["chunk_count"] += 1
        return sorted(grouped.values(), key=lambda item: str(item["filename"]).lower())

    def delete_document(self, document_id: str) -> bool:
        existing = self.collection.get(where={"document_id": document_id}, include=[])
        if not existing.get("ids"):
            return False
        self.collection.delete(where={"document_id": document_id})
        return True

    def count(self) -> int:
        return self.collection.count()
