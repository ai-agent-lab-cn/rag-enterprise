from backend.app.config import get_settings
from backend.app.errors import AppError
from backend.app.main import get_service


def test_health_does_not_initialize_rag_service(client) -> None:
    def fail_service_initialization() -> None:
        raise AssertionError("health check must not initialize the RAG service")

    client.app.dependency_overrides[get_service] = fail_service_initialization
    response = client.get("/api/health")
    settings = get_settings()

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "collection_ready": True,
        "generation_ready": bool(settings.gemini_api_key),
        "models": {
            "embedding": settings.embedding_model,
            "reranker": settings.reranker_model,
            "generation": settings.generation_model,
        },
    }


def test_document_lifecycle(client) -> None:
    response = client.post("/api/documents", files={"file": ("profile.md", "个人项目资料", "text/markdown")})
    assert response.status_code == 201
    assert response.json()["chunk_count"] == 2
    assert response.json()["knowledge_base_id"] == "kb_default"

    listed = client.get("/api/documents")
    assert listed.status_code == 200
    assert listed.json()[0]["filename"] == "profile.md"

    deleted = client.delete("/api/documents/doc_test")
    assert deleted.status_code == 204
    assert client.delete("/api/documents/doc_test").status_code == 404


def test_duplicate_upload_is_idempotent(client) -> None:
    files = {"file": ("profile.md", "个人项目资料", "text/markdown")}
    first = client.post("/api/documents", files=files)
    second = client.post("/api/documents", files=files)
    assert first.status_code == second.status_code == 201
    assert first.json()["document_id"] == second.json()["document_id"]


def test_query_returns_sources_and_metrics(client) -> None:
    response = client.post("/api/query", json={"question": "项目做了什么？", "retrieve_k": 8, "rerank_k": 3})
    assert response.status_code == 200
    payload = response.json()
    assert payload["sources"][0]["filename"] == "profile.md"
    assert payload["sources"][0]["knowledge_base_id"] == "kb_default"
    assert payload["latency_ms"]["total"] == 6


def test_invalid_top_k_returns_machine_readable_error(client) -> None:
    response = client.post("/api/query", json={"question": "项目是什么？", "retrieve_k": 2, "rerank_k": 3})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_TOP_K"


def test_unsupported_file(client) -> None:
    response = client.post(
        "/api/documents",
        files={"file": ("app.exe", b"binary", "application/octet-stream")},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE"


def test_generation_failure_returns_stable_error(client, fake_service) -> None:
    def fail_query(*_args) -> None:
        raise AppError("MODEL_UNAVAILABLE", "生成模型暂时不可用。", 502)

    fake_service.query = fail_query
    response = client.post(
        "/api/query",
        json={"question": "项目是什么？", "retrieve_k": 5, "rerank_k": 3},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"
