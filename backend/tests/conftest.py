from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.main import create_app, get_service
from backend.app.schemas import DocumentInfo, QueryResponse, Source


class FakeService:
    def __init__(self):
        self.documents: dict[str, DocumentInfo] = {}
        self.generator = type("Generator", (), {"ready": True})()

    def index_document(self, filename: str, content: bytes) -> DocumentInfo:
        if filename.endswith(".exe"):
            from backend.app.errors import AppError

            raise AppError("UNSUPPORTED_FILE", "仅支持 Markdown、TXT 和 PDF 文件。", 415)
        document = DocumentInfo(document_id="doc_test", filename=filename, chunk_count=2)
        self.documents[document.document_id] = document
        return document

    def list_documents(self) -> list[DocumentInfo]:
        return list(self.documents.values())

    def delete_document(self, document_id: str) -> bool:
        return self.documents.pop(document_id, None) is not None

    def query(self, question: str, retrieve_k: int, rerank_k: int) -> QueryResponse:
        return QueryResponse(
            answer=f"回答：{question}",
            sources=[
                Source(
                    chunk_id="doc_test:chunk:00000",
                    document_id="doc_test",
                    filename="profile.md",
                    paragraph=0,
                    chunk_index=0,
                    char_count=4,
                    summary="测试资料",
                    text="测试资料",
                    retrieval_score=0.81,
                    rerank_score=1.2,
                )
            ],
            model="fake-model",
            latency_ms={"retrieval": 1, "rerank": 2, "generation": 3, "total": 6},
        )


@pytest.fixture
def fake_service() -> FakeService:
    return FakeService()


@pytest.fixture
def client(fake_service: FakeService, tmp_path) -> Iterator[TestClient]:
    settings = get_settings()
    original_upload_path = settings.upload_path
    settings.upload_path = tmp_path / "uploads"
    app = create_app()
    app.dependency_overrides[get_service] = lambda: fake_service
    with TestClient(app) as test_client:
        yield test_client
    settings.upload_path = original_upload_path
