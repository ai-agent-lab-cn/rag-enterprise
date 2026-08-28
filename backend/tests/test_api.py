from datetime import UTC, datetime
from types import SimpleNamespace

from backend.app.config import get_settings
from backend.app.errors import AppError
from backend.app.main import _data_source_response, get_service


def test_data_source_response_normalizes_repository_sync_status() -> None:
    response = _data_source_response(
        {
            "data_source_id": "src_test",
            "name": "guide.md",
            "source_type": "file",
            "knowledge_base_id": "kb_default",
            "knowledge_base_name": "默认知识库",
            "enabled": True,
            "upload_status": "succeeded",
            "sync_status": "succeeded",
            "document_count": 1,
            "source_file_bytes": 128,
            "last_synced_at": datetime.now(UTC),
            "failure_reason": None,
            "updated_at": datetime.now(UTC),
        },
        SimpleNamespace(role="admin"),
    )

    assert response.sync_status == "succeeded"
    assert response.upload_status == "succeeded"
    assert response.index_status == "succeeded"
    assert response.last_indexed_at == response.last_synced_at
    assert response.allowed_actions == ["detail", "update_file", "edit", "disable"]


def test_health_does_not_initialize_rag_service(client) -> None:
    def fail_service_initialization() -> None:
        raise AssertionError("health check must not initialize the RAG service")

    client.app.dependency_overrides[get_service] = fail_service_initialization
    response = client.get("/api/health")
    settings = get_settings()

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "1.0.0",
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
    assert payload["conversation_id"].startswith("conv_")
    assert payload["record_id"].startswith("answer_")


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
    assert response.json()["error"]["details"]["conversation_id"].startswith("conv_")
    assert response.json()["error"]["details"]["record_id"].startswith("answer_")


def test_knowledge_base_crud_and_default_protection(client) -> None:
    listed = client.get("/api/knowledge-bases")
    assert listed.status_code == 200
    assert listed.json()[0]["knowledge_base_id"] == "kb_default"
    assert listed.json()[0]["is_default"] is True

    created = client.post(
        "/api/knowledge-bases",
        json={"name": "产品资料", "description": "产品知识库"},
    )
    assert created.status_code == 201
    knowledge_base_id = created.json()["knowledge_base_id"]
    assert knowledge_base_id.startswith("kb_")
    assert created.json()["document_count"] == 0

    fetched = client.get(f"/api/knowledge-bases/{knowledge_base_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "产品资料"

    updated = client.put(
        f"/api/knowledge-bases/{knowledge_base_id}",
        json={"name": "产品手册", "description": "已更新"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "产品手册"

    assert client.delete("/api/knowledge-bases/kb_default").status_code == 409
    assert client.delete(f"/api/knowledge-bases/{knowledge_base_id}").status_code == 204
    assert client.get(f"/api/knowledge-bases/{knowledge_base_id}").status_code == 404


def test_knowledge_base_names_are_unique(client) -> None:
    payload = {"name": "团队资料", "description": ""}
    assert client.post("/api/knowledge-bases", json=payload).status_code == 201
    conflict = client.post("/api/knowledge-bases", json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "KNOWLEDGE_BASE_NAME_CONFLICT"


def test_scoped_document_and_query_routes_are_isolated(client) -> None:
    created = client.post(
        "/api/knowledge-bases",
        json={"name": "隔离资料", "description": ""},
    ).json()
    knowledge_base_id = created["knowledge_base_id"]

    uploaded = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("isolated.md", "隔离内容", "text/markdown")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["knowledge_base_id"] == knowledge_base_id
    assert client.get("/api/documents").json() == []
    assert len(client.get(f"/api/knowledge-bases/{knowledge_base_id}/documents").json()) == 1

    metadata = client.patch(
        f"/api/knowledge-bases/{knowledge_base_id}/documents/doc_test/metadata",
        json={"category": "安全", "tags": ["ACL", "企业"]},
    )
    assert metadata.status_code == 200
    assert metadata.json()["category"] == "安全"
    assert metadata.json()["tags"] == ["ACL", "企业"]

    queried = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/query",
        json={"question": "隔离内容是什么？", "retrieve_k": 5, "rerank_k": 3},
    )
    assert queried.status_code == 200
    assert queried.json()["sources"][0]["knowledge_base_id"] == knowledge_base_id

    non_empty = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")
    assert non_empty.status_code == 409
    assert non_empty.json()["error"]["code"] == "KNOWLEDGE_BASE_NOT_EMPTY"

    assert client.delete(
        f"/api/knowledge-bases/{knowledge_base_id}/documents/doc_test"
    ).status_code == 204
    assert client.delete(
        f"/api/knowledge-bases/{knowledge_base_id}/conversations/"
        + queried.json()["conversation_id"]
    ).status_code == 204
    assert client.delete(f"/api/knowledge-bases/{knowledge_base_id}").status_code == 204


def test_document_acl_update_is_versioned(client) -> None:
    uploaded = client.post(
        "/api/knowledge-bases/kb_default/documents",
        files={"file": ("acl.md", "权限资料", "text/markdown")},
    )
    assert uploaded.status_code == 201

    updated = client.put(
        "/api/knowledge-bases/kb_default/documents/doc_test/acl",
        json={
            "allow_user_ids": ["usr_0123456789abcdef"],
            "deny_user_ids": ["usr_fedcba9876543210"],
        },
    )

    assert updated.status_code == 200
    assert updated.json() == {
        "version": 2,
        "allow_user_ids": ["usr_0123456789abcdef"],
        "deny_user_ids": ["usr_fedcba9876543210"],
    }
    document = client.get("/api/knowledge-bases/kb_default/documents").json()[0]
    assert document["acl_version"] == 2
    assert document["allow_user_ids"] == ["usr_0123456789abcdef"]


def test_acl_update_rejects_conflicting_users(client) -> None:
    user_id = "usr_0123456789abcdef"
    response = client.put(
        "/api/knowledge-bases/kb_default/documents/doc_test/acl",
        json={"allow_user_ids": [user_id], "deny_user_ids": [user_id]},
    )

    assert response.status_code == 422


def test_unknown_knowledge_base_is_rejected_before_scoped_operation(client) -> None:
    response = client.get("/api/knowledge-bases/kb_missing/documents")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "KNOWLEDGE_BASE_NOT_FOUND"


def test_conversation_history_records_success_and_continuation(client) -> None:
    first = client.post(
        "/api/query",
        json={"question": "项目是什么？", "retrieve_k": 5, "rerank_k": 3},
    )
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]
    record_id = first.json()["record_id"]

    second = client.post(
        "/api/query",
        json={
            "question": "还有哪些特点？",
            "retrieve_k": 5,
            "rerank_k": 3,
            "conversation_id": conversation_id,
        },
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id

    listed = client.get("/api/knowledge-bases/kb_default/conversations")
    assert listed.status_code == 200
    assert listed.json()[0]["turn_count"] == 2
    assert listed.json()[0]["last_status"] == "success"

    detail = client.get(f"/api/knowledge-bases/kb_default/conversations/{conversation_id}")
    assert detail.status_code == 200
    assert [item["question"] for item in detail.json()["records"]] == [
        "项目是什么？",
        "还有哪些特点？",
    ]
    assert detail.json()["records"][0]["sources"][0]["filename"] == "profile.md"

    answer = client.get(f"/api/knowledge-bases/kb_default/answers/{record_id}")
    assert answer.status_code == 200
    assert answer.json()["status"] == "success"

    deleted = client.delete(
        f"/api/knowledge-bases/kb_default/conversations/{conversation_id}"
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/knowledge-bases/kb_default/answers/{record_id}").status_code == 404


def test_failed_query_is_saved_in_history(client, fake_service) -> None:
    def fail_query(*_args) -> None:
        raise AppError("MODEL_UNAVAILABLE", "生成模型暂时不可用。", 502)

    fake_service.query = fail_query
    response = client.post(
        "/api/query",
        json={"question": "项目是什么？", "retrieve_k": 5, "rerank_k": 3},
    )
    details = response.json()["error"]["details"]

    saved = client.get(
        f"/api/knowledge-bases/kb_default/answers/{details['record_id']}"
    )
    assert saved.status_code == 200
    assert saved.json()["status"] == "failed"
    assert saved.json()["error_code"] == "MODEL_UNAVAILABLE"
    assert saved.json()["answer"] is None
    bad_cases = client.get(
        "/api/knowledge-bases/kb_default/bad-cases?category=unclassified&error_code=MODEL_UNAVAILABLE"
    )
    assert bad_cases.status_code == 200
    assert [item["record_id"] for item in bad_cases.json()] == [details["record_id"]]


def test_conversation_cannot_cross_knowledge_base_boundary(client) -> None:
    conversation_id = client.post(
        "/api/query",
        json={"question": "默认库问题？", "retrieve_k": 5, "rerank_k": 3},
    ).json()["conversation_id"]
    knowledge_base_id = client.post(
        "/api/knowledge-bases",
        json={"name": "另一个知识库", "description": ""},
    ).json()["knowledge_base_id"]

    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/query",
        json={
            "question": "尝试串库？",
            "retrieve_k": 5,
            "rerank_k": 3,
            "conversation_id": conversation_id,
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
    assert client.get(
        f"/api/knowledge-bases/{knowledge_base_id}/conversations"
    ).json() == []


def test_knowledge_base_with_history_cannot_be_deleted_until_conversation_is_removed(client) -> None:
    knowledge_base_id = client.post(
        "/api/knowledge-bases",
        json={"name": "历史保护测试", "description": ""},
    ).json()["knowledge_base_id"]
    queried = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/query",
        json={"question": "空库问题？", "retrieve_k": 5, "rerank_k": 3},
    )
    conversation_id = queried.json()["conversation_id"]

    assert client.delete(f"/api/knowledge-bases/{knowledge_base_id}").status_code == 409
    assert client.delete(
        f"/api/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}"
    ).status_code == 204
    assert client.delete(f"/api/knowledge-bases/{knowledge_base_id}").status_code == 204


def test_knowledge_base_with_orphan_original_file_cannot_be_deleted(client) -> None:
    created = client.post(
        "/api/knowledge-bases",
        json={"name": "孤立文件测试", "description": ""},
    ).json()
    knowledge_base_id = created["knowledge_base_id"]
    upload_path = get_settings().upload_path / knowledge_base_id
    upload_path.mkdir(parents=True)
    (upload_path / "orphan.md").write_text("不能被静默遗留", encoding="utf-8")

    response = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "KNOWLEDGE_BASE_NOT_EMPTY"


def test_index_versions_are_admin_only_and_readable(client) -> None:
    response = client.get("/api/knowledge-bases/kb_default/index-versions")

    assert response.status_code == 200
    assert response.json() == []
