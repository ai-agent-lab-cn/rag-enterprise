from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.main import create_app, get_service
from backend.app.service import RAGService
from backend.app.store import ChromaStore


class DeterministicEmbedder:
    model_name = "deterministic-embedding-v1"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        if "来源" in text or "追溯" in text or "证据" in text:
            return [1.0, 0.0, 0.0]
        if "部署" in text or "容器" in text:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


class DeterministicReranker:
    model_name = "deterministic-reranker-v1"

    def score(self, question: str, chunks: list[str]) -> list[float]:
        del question
        return [
            2.0 if "原文证据" in chunk else 1.0 if "来源" in chunk or "追溯" in chunk else 0.0
            for chunk in chunks
        ]


class DisabledGenerator:
    model_name = "disabled-generator"

    def generate(self, prompt: str) -> tuple[str, dict[str, object]]:
        del prompt
        return "集成测试仅验证检索链路。", {}


@pytest.fixture
def isolated_service(tmp_path: Path) -> Iterator[RAGService]:
    settings = Settings(
        chroma_path=tmp_path / "chroma",
        upload_path=tmp_path / "uploads",
        collection_name="integration_test_documents",
        embedding_model=DeterministicEmbedder.model_name,
        chunk_size=200,
        chunk_overlap=0,
    )
    store = ChromaStore(
        settings.chroma_path,
        settings.collection_name,
        settings.embedding_model,
    )
    yield RAGService(
        settings=settings,
        store=store,
        embedder=DeterministicEmbedder(),
        reranker=DeterministicReranker(),
        generator=DisabledGenerator(),
    )


def test_real_chroma_store_import_query_sources_and_delete(isolated_service: RAGService) -> None:
    document = isolated_service.index_document(
        "retrieval-evidence.md",
        "# 可追溯问答\n\n答案必须展示来源、段落和原文证据。".encode(),
    )
    isolated_service.index_document(
        "deployment.md",
        "# 部署\n\n应用使用容器启动。".encode(),
    )

    assert isolated_service.store.count() == 4
    assert {item.filename for item in isolated_service.list_documents()} == {
        "deployment.md",
        "retrieval-evidence.md",
    }

    response = isolated_service.query("如何追溯答案来源？", retrieve_k=2, rerank_k=1)

    assert response.sources[0].document_id == document.document_id
    assert response.sources[0].filename == "retrieval-evidence.md"
    assert response.sources[0].paragraph == 1
    assert "原文证据" in response.sources[0].text
    assert response.sources[0].retrieval_score == pytest.approx(1.0)
    assert response.sources[0].rerank_score == pytest.approx(2.0)

    assert isolated_service.delete_document(document.document_id) is True
    assert isolated_service.delete_document(document.document_id) is False
    assert isolated_service.store.count() == 2


def test_retrieval_api_uses_isolated_real_chroma(
    isolated_service: RAGService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_path = tmp_path / "api-uploads"
    monkeypatch.setattr(get_settings(), "upload_path", upload_path)
    app = create_app()
    app.dependency_overrides[get_service] = lambda: isolated_service

    with TestClient(app) as client:
        uploaded = client.post(
            "/api/documents",
            files={
                "file": (
                    "retrieval-evidence.md",
                    "# 可追溯问答\n\n答案必须展示来源、段落和原文证据。",
                    "text/markdown",
                )
            },
        )
        assert uploaded.status_code == 201
        document_id = uploaded.json()["document_id"]
        assert list(upload_path.glob(f"{document_id}.*"))

        listed = client.get("/api/documents")
        assert listed.status_code == 200
        assert listed.json()[0]["document_id"] == document_id

        queried = client.post(
            "/api/query",
            json={"question": "如何追溯答案来源？", "retrieve_k": 5, "rerank_k": 1},
        )
        assert queried.status_code == 200
        assert queried.json()["sources"][0]["document_id"] == document_id
        assert queried.json()["sources"][0]["filename"] == "retrieval-evidence.md"

        deleted = client.delete(f"/api/documents/{document_id}")
        assert deleted.status_code == 204
        assert client.get("/api/documents").json() == []
        assert not list(upload_path.glob(f"{document_id}.*"))
