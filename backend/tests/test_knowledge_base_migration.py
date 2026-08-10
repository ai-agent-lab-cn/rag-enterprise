from pathlib import Path

import chromadb
import pytest

from backend.app.chunking import Chunk
from backend.app.knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID, KnowledgeBaseScope
from backend.app.store import ChromaStore


def _chunk(chunk_id: str, knowledge_base_id: str, document_id: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        filename=f"{document_id}.md",
        text=f"{knowledge_base_id} 的资料",
        page=None,
        paragraph=0,
        chunk_index=0,
        char_count=8,
        summary="测试资料",
    )


def test_v2_chroma_metadata_is_migrated_to_default_knowledge_base(tmp_path: Path) -> None:
    path = tmp_path / "chroma"
    collection_name = "legacy_documents"
    model_name = "test-embedding"
    collection = chromadb.PersistentClient(path=str(path)).get_or_create_collection(
        name=collection_name,
        metadata={"embedding_model": model_name, "hnsw:space": "cosine"},
    )
    collection.add(
        ids=["legacy:chunk:00000"],
        documents=["V2 旧资料"],
        embeddings=[[1.0, 0.0]],
        metadatas=[
            {
                "document_id": "legacy",
                "filename": "legacy.md",
                "paragraph": 0,
                "chunk_index": 0,
                "char_count": 5,
                "summary": "V2 旧资料",
            }
        ],
    )

    store = ChromaStore(path, collection_name, model_name)

    migrated = store.collection.get(ids=["legacy:chunk:00000"], include=["metadatas"])
    assert migrated["metadatas"][0]["knowledge_base_id"] == DEFAULT_KNOWLEDGE_BASE_ID
    assert store.list_documents() == [
        {
            "knowledge_base_id": DEFAULT_KNOWLEDGE_BASE_ID,
            "document_id": "legacy",
            "filename": "legacy.md",
            "chunk_count": 1,
            "status": "ready",
        }
    ]

    # 再次初始化验证迁移幂等，不会复制或丢失 chunk。
    assert ChromaStore(path, collection_name, model_name).count() == 1


def test_chroma_operations_are_isolated_by_knowledge_base(tmp_path: Path) -> None:
    store = ChromaStore(tmp_path / "chroma", "scoped_documents", "test-embedding")
    default_chunk = _chunk("default:chunk:00000", "kb_default", "shared_doc")
    team_chunk = _chunk("team:chunk:00000", "kb_team", "shared_doc")
    store.upsert([default_chunk, team_chunk], [[1.0, 0.0], [0.0, 1.0]])

    assert store.count("kb_default") == 1
    assert store.count("kb_team") == 1
    assert store.query([1.0, 0.0], 5, "kb_default")[0].chunk_id == default_chunk.chunk_id
    assert store.query([0.0, 1.0], 5, "kb_team")[0].chunk_id == team_chunk.chunk_id

    assert store.delete_document("shared_doc", "kb_default") is True
    assert store.count("kb_default") == 0
    assert store.count("kb_team") == 1


def test_legacy_uploads_are_moved_under_default_knowledge_base(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    legacy_file = upload_root / "doc_legacy.md"
    legacy_file.write_text("旧文件", encoding="utf-8")
    scope = KnowledgeBaseScope(DEFAULT_KNOWLEDGE_BASE_ID, upload_root)

    scope.migrate_legacy_uploads()
    scope.migrate_legacy_uploads()

    assert not legacy_file.exists()
    assert (scope.upload_path / legacy_file.name).read_text(encoding="utf-8") == "旧文件"


def test_conflicting_legacy_upload_is_not_overwritten(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    target_root = upload_root / DEFAULT_KNOWLEDGE_BASE_ID
    target_root.mkdir(parents=True)
    (upload_root / "doc_conflict.md").write_text("旧内容", encoding="utf-8")
    (target_root / "doc_conflict.md").write_text("新内容", encoding="utf-8")

    with pytest.raises(RuntimeError, match="无法自动迁移"):
        KnowledgeBaseScope(DEFAULT_KNOWLEDGE_BASE_ID, upload_root).migrate_legacy_uploads()
