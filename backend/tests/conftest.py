from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID
from backend.app.main import (
    create_app,
    get_auth_repository,
    get_conversations,
    get_knowledge_bases,
    get_service,
)
from backend.app.schemas import DocumentInfo, QueryResponse, Source


class FakeService:
    def __init__(self):
        self.documents: dict[str, dict[str, DocumentInfo]] = {}
        self.generator = type("Generator", (), {"ready": True})()

    def index_document(
        self,
        filename: str,
        content: bytes,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> DocumentInfo:
        if filename.endswith(".exe"):
            from backend.app.errors import AppError

            raise AppError("UNSUPPORTED_FILE", "仅支持 Markdown、TXT 和 PDF 文件。", 415)
        document = DocumentInfo(
            knowledge_base_id=knowledge_base_id,
            document_id="doc_test",
            filename=filename,
            chunk_count=2,
        )
        self.documents.setdefault(knowledge_base_id, {})[document.document_id] = document
        return document

    def list_documents(
        self,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> list[DocumentInfo]:
        return list(self.documents.get(knowledge_base_id, {}).values())

    def delete_document(
        self,
        document_id: str,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> bool:
        return self.documents.get(knowledge_base_id, {}).pop(document_id, None) is not None

    def query(
        self,
        question: str,
        retrieve_k: int,
        rerank_k: int,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> QueryResponse:
        return QueryResponse(
            answer=f"回答：{question}",
            sources=[
                Source(
                    chunk_id="doc_test:chunk:00000",
                    knowledge_base_id=knowledge_base_id,
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
    original_knowledge_bases_path = settings.knowledge_bases_path
    original_conversations_path = settings.conversations_path
    original_auth_path = settings.auth_path
    settings.upload_path = tmp_path / "uploads"
    settings.knowledge_bases_path = tmp_path / "knowledge-bases" / "registry.json"
    settings.conversations_path = tmp_path / "conversations" / "records.json"
    settings.auth_path = tmp_path / "auth" / "store.json"
    get_knowledge_bases.cache_clear()
    get_conversations.cache_clear()
    get_auth_repository.cache_clear()
    app = create_app()
    app.dependency_overrides[get_service] = lambda: fake_service
    with TestClient(app) as test_client:
        bootstrap = test_client.post(
            "/api/auth/bootstrap",
            json={
                "username": "test-admin",
                "password": "correct-horse-battery-staple",
                "display_name": "测试管理员",
            },
        )
        assert bootstrap.status_code == 201
        test_client.headers["Authorization"] = f"Bearer {bootstrap.json()['access_token']}"
        yield test_client
    settings.upload_path = original_upload_path
    settings.knowledge_bases_path = original_knowledge_bases_path
    settings.conversations_path = original_conversations_path
    settings.auth_path = original_auth_path
    get_knowledge_bases.cache_clear()
    get_conversations.cache_clear()
    get_auth_repository.cache_clear()
