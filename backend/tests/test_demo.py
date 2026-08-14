from pathlib import Path

import pytest

from backend.app.demo import seed_demo_document
from backend.app.knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID
from backend.app.schemas import DocumentInfo


class DemoService:
    def __init__(self, documents: list[DocumentInfo] | None = None):
        self.documents = documents or []
        self.indexed: list[tuple[str, bytes, str]] = []

    def list_documents(self, knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID):
        assert knowledge_base_id == DEFAULT_KNOWLEDGE_BASE_ID
        return self.documents

    def index_document(
        self,
        filename: str,
        content: bytes,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ):
        self.indexed.append((filename, content, knowledge_base_id))
        return DocumentInfo(
            document_id="doc_demo",
            filename=filename,
            chunk_count=1,
            knowledge_base_id=knowledge_base_id,
        )


def test_demo_seed_is_disabled_without_path():
    service = DemoService()

    assert seed_demo_document(None, service) is False
    assert service.indexed == []


def test_demo_seed_indexes_public_document_once(tmp_path: Path):
    path = tmp_path / "project-profile.md"
    path.write_text("# 演示资料", encoding="utf-8")
    service = DemoService()

    assert seed_demo_document(path, service) is True
    assert service.indexed == [
        ("project-profile.md", "# 演示资料".encode(), DEFAULT_KNOWLEDGE_BASE_ID)
    ]


def test_demo_seed_keeps_existing_default_knowledge_base(tmp_path: Path):
    path = tmp_path / "project-profile.md"
    path.write_text("# 演示资料", encoding="utf-8")
    service = DemoService(
        [DocumentInfo(document_id="doc_existing", filename="existing.md", chunk_count=2)]
    )

    assert seed_demo_document(path, service) is False
    assert service.indexed == []


def test_demo_seed_rejects_missing_configured_document(tmp_path: Path):
    with pytest.raises(RuntimeError, match="演示资料不存在"):
        seed_demo_document(tmp_path / "missing.md", DemoService())
