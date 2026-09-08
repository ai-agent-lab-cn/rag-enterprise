import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID
from backend.app.main import (
    create_app,
    get_audit_repository,
    get_auth_repository,
    get_conversations,
    get_knowledge_bases,
    get_service,
)
from backend.app.retrieval_access import RetrievalAccessContext, can_retrieve_metadata
from backend.app.schemas import DocumentInfo, QueryResponse, Source

# 依赖外部服务的测试在本地缺服务时跳过，在 CI 里缺服务必须直接失败。
# 跳过在 workflow 日志里和通过长得一模一样：MinIO 起不来 → 对象存储测试全被跳过 →
# 依然绿灯。这个项目已经四次发现「没有 CI 覆盖的东西会静默腐烂」，不再给第五次机会。
if os.getenv("CI") and not os.getenv("MINIO_ENDPOINT"):
    raise RuntimeError(
        "CI 环境缺少 MINIO_ENDPOINT，对象存储测试会被静默跳过。"
        "请确认 workflow 里的 MinIO 已启动。"
    )
# 数据库同理，而且面更大：整条同步与索引治理链路都挂在 TEST_DATABASE_URL 上。
# 它此前没有守卫——CI 里靠 workflow 显式设置这个变量才没出事，谁把那一行删掉，
# 几百条集成测试会一起变成静默跳过，日志上依然全绿。
if os.getenv("CI") and not os.getenv("TEST_DATABASE_URL"):
    raise RuntimeError(
        "CI 环境缺少 TEST_DATABASE_URL，同步与索引治理的集成测试会被静默跳过。"
        "请确认 workflow 里的 PostgreSQL service 已启动且变量已注入。"
    )


class FakeService:
    def __init__(self):
        self.documents: dict[str, dict[str, DocumentInfo]] = {}
        self.generator = type("Generator", (), {"ready": True})()

    def index_document(
        self,
        filename: str,
        content: bytes,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        metadata: dict[str, object] | None = None,
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
        access: RetrievalAccessContext | None = None,
    ) -> list[DocumentInfo]:
        # 过滤逻辑跟真实实现走同一个判据函数，不在这里另写一套——CLAUDE.md 第四条：
        # 双实现只测一个等于没测。替身若不过滤，接口层的 ACL 过滤就永远测不出来。
        items = list(self.documents.get(knowledge_base_id, {}).values())
        if access is None:
            return items
        return [item for item in items if can_retrieve_metadata(item.model_dump(), access)]

    def delete_document(
        self,
        document_id: str,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> bool:
        return self.documents.get(knowledge_base_id, {}).pop(document_id, None) is not None

    def update_document_metadata(
        self,
        document_id: str,
        metadata: dict[str, object],
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> bool:
        document = self.documents.get(knowledge_base_id, {}).get(document_id)
        if document is None:
            return False
        self.documents[knowledge_base_id][document_id] = document.model_copy(update=metadata)
        return True

    def update_document_acl(
        self,
        document_id: str,
        allow_user_ids: list[str],
        deny_user_ids: list[str],
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> int | None:
        document = self.documents.get(knowledge_base_id, {}).get(document_id)
        if document is None:
            return None
        version = document.acl_version + 1
        self.documents[knowledge_base_id][document_id] = document.model_copy(
            update={
                "acl_version": version,
                "allow_user_ids": allow_user_ids,
                "deny_user_ids": deny_user_ids,
            }
        )
        return version

    def list_index_versions(
        self,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    ) -> list[dict[str, object]]:
        return []

    def query(
        self,
        question: str,
        retrieve_k: int,
        rerank_k: int,
        knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
        filters=None,
        access=None,
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
    original_audit_path = settings.audit_path
    settings.upload_path = tmp_path / "uploads"
    settings.knowledge_bases_path = tmp_path / "knowledge-bases" / "registry.json"
    settings.conversations_path = tmp_path / "conversations" / "records.json"
    settings.auth_path = tmp_path / "auth" / "store.json"
    settings.audit_path = tmp_path / "audit" / "events.json"
    get_knowledge_bases.cache_clear()
    get_conversations.cache_clear()
    get_auth_repository.cache_clear()
    get_audit_repository.cache_clear()
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
    settings.audit_path = original_audit_path
    get_knowledge_bases.cache_clear()
    get_conversations.cache_clear()
    get_auth_repository.cache_clear()
    get_audit_repository.cache_clear()
