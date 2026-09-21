import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from backend.app.config import get_settings
from backend.app.errors import AppError
from backend.app.evaluation_reports import EvaluationReportRepository
from backend.app.index_evaluation_runs import FAILURE_MESSAGES
from backend.app.main import (
    _data_source_response,
    get_data_sources,
    get_evaluation_reports,
    get_service,
)
from backend.app.models import get_generator
from backend.app.schemas import DocumentInfo, IndexEvaluationRunResponse


class _DataSourcesStub:
    def __init__(self):
        self.created = False
        self.database_url = "postgresql://unused"

    def create(self, *_args):
        self.created = True
        return "ds_external"

    def get(self, data_source_id: str):
        if data_source_id != "ds_external":
            return None
        return {
            "data_source_id": data_source_id,
            "knowledge_base_id": "kb_default",
            "source_type": "object_storage",
            "configuration": {
                "endpoint": "127.0.0.1:9000", "bucket": "docs", "secure": False,
                "credential_env": "DOCS", "metadata_defaults": {},
            },
        }

    def list_sync_runs(self, data_source_id: str, limit: int = 50):
        return [{
            "sync_run_id": "run_1", "data_source_id": data_source_id,
            "status": "succeeded", "stage": "complete", "added_count": 2,
            "updated_count": 0, "deleted_count": 0, "skipped_count": 0,
            "failed_count": 0, "retry_count": 0, "error_code": None,
            "failure_reason": None, "started_at": None, "finished_at": None,
            "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC),
        }]

    def get_citation(self, knowledge_base_id: str, chunk_id: str, user_id: str):
        if (knowledge_base_id, chunk_id) != ("kb_default", "chunk_1"):
            return None
        return {
            "chunk_id": chunk_id,
            "knowledge_base_id": knowledge_base_id,
            "document_id": "doc_1",
            "document_version_id": "ver_1",
            "content_sha256": "a" * 64,
            "filename": "guide.md",
            "text": "可信原文片段",
            "page": 3,
            "paragraph": 2,
            "heading_path": ["安全", "权限"],
            "sheet_name": None,
            "row_start": None,
            "row_end": None,
            "source_url": None,
            "external_resource_id": "docs/guide.md",
        }


def test_admin_can_create_external_source_and_read_sync_runs(client, monkeypatch) -> None:
    repository = _DataSourcesStub()
    client.app.dependency_overrides[get_data_sources] = lambda: repository
    monkeypatch.setattr(
        "backend.app.main.enqueue_sync",
        lambda _url, source_id: {
            "index_job_id": "job_1", "sync_run_id": "run_1", "data_source_id": source_id,
        },
    )

    created = client.post(
        "/api/knowledge-bases/kb_default/data-sources",
        json={
            "name": "产品资料桶", "source_type": "object_storage",
            "configuration": {
                "endpoint": "127.0.0.1:9000", "bucket": "docs", "secure": False,
                "credential_env": "DOCS",
            },
            "metadata_defaults": {"department": "产品"},
        },
    )
    assert created.status_code == 201
    assert created.json() == {"data_source_id": "ds_external"}

    queued = client.post("/api/data-sources/ds_external/sync")
    assert queued.status_code == 202
    assert queued.json()["sync_run_id"] == "run_1"

    runs = client.get("/api/data-sources/ds_external/sync-runs")
    assert runs.status_code == 200
    assert runs.json()[0]["added_count"] == 2


def test_citation_endpoint_returns_acl_checked_original_location(client) -> None:
    client.app.dependency_overrides[get_data_sources] = lambda: _DataSourcesStub()

    response = client.get("/api/knowledge-bases/kb_default/citations/chunk_1")

    assert response.status_code == 200
    assert response.json() == {
        "chunk_id": "chunk_1",
        "knowledge_base_id": "kb_default",
        "document_id": "doc_1",
        "document_version_id": "ver_1",
        "content_sha256": "a" * 64,
        "filename": "guide.md",
        "text": "可信原文片段",
        "page": 3,
        "paragraph": 2,
        "heading_path": ["安全", "权限"],
        "sheet_name": None,
        "row_start": None,
        "row_end": None,
        "column_start": None,
        "column_end": None,
        "source_url": None,
        "external_resource_id": "docs/guide.md",
    }


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
    # 上传型数据源没有「同步」可停，所以没有 disable；能停的是检索。两个开关拆开之后
    # 这里一直期待着旧的 disable，四个动作里错了最后一个。
    assert response.allowed_actions == ["detail", "update_file", "edit", "disable_retrieval"]


def test_health_does_not_initialize_rag_service(client) -> None:
    def fail_service_initialization() -> None:
        raise AssertionError("health check must not initialize the RAG service")

    client.app.dependency_overrides[get_service] = fail_service_initialization
    response = client.get("/api/health")
    settings = get_settings()
    generator = get_generator()

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "1.0.0",
        "collection_ready": True,
        "generation_ready": generator.ready,
        "models": {
            "embedding": settings.embedding_model,
            "reranker": settings.reranker_model,
            "generation": generator.model_name,
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
    assert saved.json()["policy_snapshot"] == {}
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


def test_validation_reports_require_postgres_rather_than_returning_empty(client) -> None:
    """JSON 形态下明确报 503，而不是返回空列表。

    空列表会被读成「这个版本没有验证报告」，而事实是「这个部署形态根本不记录验证报告」。
    两者对操作者的含义完全相反——前者暗示可以直接激活，后者说明这里做不了发布门禁。
    """

    response = client.get(
        "/api/knowledge-bases/kb_default/index-versions/iv_missing/validations"
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "POSTGRES_REQUIRED"


def test_activate_uses_the_version_bound_report_without_request_body(
    client, fake_service, monkeypatch
) -> None:
    """Activate 只发布 ready Version，不得再次接收评测报告或创建验证记录。"""

    repository = _DataSourcesStub()
    client.app.dependency_overrides[get_data_sources] = lambda: repository
    fake_service.list_index_versions = lambda _knowledge_base_id: [
        {"index_version_id": "iv_ready", "status": "ready"}
    ]
    calls: list[tuple[str, str]] = []

    def activate(database_url, index_version_id, _audit, _actor):
        calls.append((database_url, index_version_id))
        return {
            "knowledge_base_id": "kb_default",
            "active": index_version_id,
            "previous": "iv_active",
            "validation_report_id": "vr_bound",
        }

    monkeypatch.setattr("backend.app.main.switch_to_version", activate)

    response = client.put(
        "/api/knowledge-bases/kb_default/index-versions/iv_ready/active"
    )

    assert response.status_code == 200
    assert response.json()["validation_report_id"] == "vr_bound"
    assert calls == [(repository.database_url, "iv_ready")]


def test_document_listing_filters_by_acl_for_members_but_not_admins(
    client, fake_service
) -> None:
    """资料清单对普通成员按 ACL 过滤，对管理员不过滤。

    此前这个接口只校验知识库可访问，一条 ACL 判据都没有：被 deny 的成员照样拿到整份
    清单，而 DocumentInfo 带着 filename、owner_user_id、department、sensitivity，
    以及 allow_user_ids / deny_user_ids 本身——授权名单原样外泄。

    管理员必须**不**过滤：ACL 管理入口就在这份清单上，过滤掉的话一份被 deny 到没人
    可见的资料就再也改不回来了。

    直接往替身里放两份文档，不走上传接口——FakeService.index_document 给所有文档写死
    document_id="doc_test"，两次上传会互相覆盖。
    """

    member = client.post(
        "/api/members",
        json={
            "username": "plain-member",
            "password": "correct-horse-battery-staple",
            "display_name": "普通成员",
            "role": "member",
        },
    )
    assert member.status_code == 201
    member_id = member.json()["user_id"]

    # 泄漏的前提正是「已授权访问知识库、但个别资料被 deny」。
    assert client.put(f"/api/knowledge-bases/kb_default/members/{member_id}").status_code == 204

    fake_service.documents["kb_default"] = {
        "doc_public": DocumentInfo(
            document_id="doc_public", filename="public.md", chunk_count=2
        ),
        "doc_secret": DocumentInfo(
            document_id="doc_secret",
            filename="secret.md",
            chunk_count=2,
            allow_user_ids=["usr_0123456789abcdef"],
            deny_user_ids=[member_id],
            sensitivity="restricted",
        ),
    }

    token = client.post(
        "/api/auth/login",
        json={"username": "plain-member", "password": "correct-horse-battery-staple"},
    )
    assert token.status_code == 200

    as_member = client.get(
        "/api/knowledge-bases/kb_default/documents",
        headers={"Authorization": f"Bearer {token.json()['access_token']}"},
    )
    assert as_member.status_code == 200
    member_names = {item["filename"] for item in as_member.json()}
    assert member_names == {"public.md"}, f"被 deny 的成员拿到了受限资料的清单：{member_names}"

    admin_names = {
        item["filename"] for item in client.get("/api/knowledge-bases/kb_default/documents").json()
    }
    assert admin_names == {"public.md", "secret.md"}, f"管理员被过滤了，没法管理 ACL：{admin_names}"


# --- 正式评测运行（index_evaluation）的 API 契约 ---------------------------------

_EVALUATION_RUN_ID = "eval_0123456789abcdef"
_REPORT_SOURCE = Path("backend/evaluation/reports/retrieval_v1_optimized.json")


def _report_payload() -> dict:
    """直接用仓库里冻结的正式报告当 report_payload。

    手工拼一份会绕不过 RetrievalEvaluationReport 的校验——EvaluationMetric 要求
    ``passed == (value >= threshold and not regressed)``，拼错了测试报的是构造错误，
    而不是「详情路由有没有把报告透出来」这件被测的事。
    """

    return json.loads(_REPORT_SOURCE.read_text(encoding="utf-8"))


def _evaluation_run_row(**changes: object) -> dict[str, object]:
    """一行 evaluation_runs 记录的替身。

    字段照 `create_evaluation_run` 的 INSERT 与 `_COLUMNS` 写：入队时 official 落的是
    false、status 落的是 'queued'、passed 落的是 NULL——「还没跑」不能显示成「没达标」。
    available_at / created_at / updated_at 在 `_evaluation_run_response` 里是直接下标
    （不是 `.get`），少一个就是 KeyError 而不是 None，所以必须给全。
    """

    moment = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
    row: dict[str, object] = {
        "evaluation_run_id": _EVALUATION_RUN_ID,
        "knowledge_base_id": "kb_default",
        "index_version_id": "iv_candidate",
        "operation_id": "op_evaluation",
        "dataset_id": "rag-enterprise-corpus",
        "dataset_version": "2.0.0",
        "parameters": {
            "dataset_slug": "corpus_v2",
            "config_snapshot": {"chunk_size": 500},
            "component_manifest": {"chunking_version": "3"},
            "embedding_dimension": 768,
        },
        "models": {},
        "metrics": {},
        "status": "queued",
        "config_fingerprint": "a" * 64,
        "baseline_report_id": None,
        "report_payload": None,
        "official": False,
        "passed": None,
        "attempt_count": 0,
        "max_attempts": 3,
        "requested_by": "usr_requester",
        "error_code": None,
        "error_message": None,
        "available_at": moment,
        "started_at": None,
        "finished_at": None,
        "created_at": moment,
        "updated_at": moment,
    }
    row.update(changes)
    return row


def _stub_sources(client) -> _DataSourcesStub:
    """让 DataSourcesDependency 拿到一个带 database_url 的替身。

    默认测试环境没有 DATABASE_URL，`get_data_sources()` 返回 None，评测路由会先被
    503 POSTGRES_REQUIRED 挡掉，后面的断言一条都走不到。
    """

    stub = _DataSourcesStub()
    client.app.dependency_overrides[get_data_sources] = lambda: stub
    return stub


def _allow_evaluation_database(monkeypatch) -> None:
    """放行评测库守卫。

    路由调用的是 `require_isolated_evaluation_database(settings)`，settings 是
    `create_app()` 闭包捕获的实例；打模块级名字最直接，也避免改动全局单例影响别的用例。
    默认环境没有 EVALUATION_DATABASE_URL，不打这个桩会一律 503。
    """

    monkeypatch.setattr(
        "backend.app.main.require_isolated_evaluation_database",
        lambda _settings: "postgresql://evaluation",
    )


def _member_headers(client, username: str) -> dict[str, str]:
    """建一个普通成员并登录。一次登录一个成员：LOGIN_RATE_LIMIT 默认 10 次/窗口。"""

    password = "correct-horse-battery-staple"
    created = client.post(
        "/api/members",
        json={
            "username": username,
            "password": password,
            "display_name": "普通成员",
            "role": "member",
        },
    )
    assert created.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_member_cannot_enqueue_official_evaluation(client) -> None:
    """普通成员不得发起正式评测。

    这次入队会占用隔离的评测库跑完整语料，并产出可以直接用于三层发布门禁的证据——
    它是发布链路的一环，不是只读查询。`_require_admin` 排在知识库校验与 PostgreSQL
    判断之前，所以无论后端形态如何，成员拿到的都必须是 403 ADMIN_REQUIRED。
    """

    headers = _member_headers(client, "eval-member")

    response = client.post(
        "/api/knowledge-bases/kb_default/index-versions/iv_candidate/evaluation-runs",
        json={"dataset_id": "corpus_v2"},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ADMIN_REQUIRED"


def test_creating_evaluation_run_is_accepted_and_audited(
    client, fake_service, monkeypatch
) -> None:
    """入队返回 202 而不是 201，并且记一条 index_evaluation.create 审计。

    202 是这条路由的语义本身：真正的评测由独立的 Evaluation Worker 执行，接口只把任务
    排进队列。用 201 会让调用方以为评测已经完成。

    响应字段直接对齐 IndexEvaluationRunResponse——前端按这份契约渲染流水线，少一个
    字段（比如把 official 和 passed 合成一个）页面就再也说不清「跑过但没达标」。
    """

    stub = _stub_sources(client)
    _allow_evaluation_database(monkeypatch)
    fake_service.list_index_versions = lambda _knowledge_base_id: [
        {"index_version_id": "iv_candidate", "status": "validating"}
    ]
    calls: list[tuple[object, ...]] = []

    def enqueue(database_url, index_version_id, dataset_id, requested_by):
        calls.append((database_url, index_version_id, dataset_id, requested_by))
        return _evaluation_run_row()

    monkeypatch.setattr("backend.app.main.create_evaluation_run", enqueue)

    response = client.post(
        "/api/knowledge-bases/kb_default/index-versions/iv_candidate/evaluation-runs",
        json={"dataset_id": "corpus_v2"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert set(payload) == set(IndexEvaluationRunResponse.model_fields)
    assert payload["evaluation_run_id"] == _EVALUATION_RUN_ID
    assert payload["index_version_id"] == "iv_candidate"
    assert payload["status"] == "queued"
    assert payload["dataset_id"] == "rag-enterprise-corpus"
    assert payload["dataset_version"] == "2.0.0"
    assert payload["dataset_slug"] == "corpus_v2"
    # 入队时 official=false、passed=NULL：证据资格和阈值结论都要等 Worker 跑完才有。
    assert payload["official"] is False
    assert payload["passed"] is None
    assert payload["report_id"] is None
    assert payload["failure_code"] is None
    assert payload["failure_reason"] is None
    assert [call[:3] for call in calls] == [(stub.database_url, "iv_candidate", "corpus_v2")]

    events = client.get("/api/audit/events", params={"action": "index_evaluation.create"})
    assert events.status_code == 200
    assert [
        (item["resource_type"], item["resource_id"], item["result"]) for item in events.json()
    ] == [("index_version", "iv_candidate", "success")]


def test_duplicate_evaluation_run_is_reported_as_conflict(
    client, fake_service, monkeypatch
) -> None:
    """同一候选版本已有 queued/running 的评测时返回 409，而不是再排一条。

    重复入队会让两个 Worker 同时往评测库里建同一份临时语料；接口必须把仓储抛出的
    EVALUATION_RUN_IN_PROGRESS 原样透出去，页面才能说明「已经在跑了，去看那一条」。
    """

    _stub_sources(client)
    _allow_evaluation_database(monkeypatch)
    fake_service.list_index_versions = lambda _knowledge_base_id: [
        {"index_version_id": "iv_candidate", "status": "validating"}
    ]

    def conflict(*_args):
        raise AppError("EVALUATION_RUN_IN_PROGRESS", "该版本已有正在进行的正式评测。", 409)

    monkeypatch.setattr("backend.app.main.create_evaluation_run", conflict)

    response = client.post(
        "/api/knowledge-bases/kb_default/index-versions/iv_candidate/evaluation-runs",
        json={"dataset_id": "corpus_v2"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EVALUATION_RUN_IN_PROGRESS"


def test_listing_knowledge_base_evaluation_runs_is_not_scoped_to_a_version(
    client, fake_service, monkeypatch
) -> None:
    """整库列表不按版本过滤，否则版本一激活它那次评测的详情就再也打不开。

    运行记录列的是 Operation，而 Operation 上没有 evaluation_run_id；页面只能拿这份
    列表按 operation_id 对回去。按候选版本取的话，`active` 不在候选状态集合里，刚刚
    放行线上版本的那次评测点开只剩「读取不到这次评测的明细」——浏览器里实测过。
    """

    stub = _stub_sources(client)
    calls: list[tuple[object, ...]] = []

    def listing(database_url, knowledge_base_id, index_version_id=None):
        calls.append((database_url, knowledge_base_id, index_version_id))
        return [_evaluation_run_row(index_version_id="iv_activated")]

    monkeypatch.setattr("backend.app.main.list_evaluation_runs", listing)

    response = client.get("/api/knowledge-bases/kb_default/evaluation-runs")

    assert response.status_code == 200
    assert [item["index_version_id"] for item in response.json()] == ["iv_activated"]
    # 第三个参数留空 = 不限版本；传了值就退回「只有候选版本可见」的老行为。
    assert calls == [(stub.database_url, "kb_default", None)]


def test_evaluation_run_cannot_target_a_version_outside_the_knowledge_base(
    client, fake_service, monkeypatch
) -> None:
    """版本不属于这个知识库时 404，且根本不进入入队。

    index_version_id 是可以猜的。归属校验必须发生在写库之前——否则 A 库的管理员能让
    Worker 去跑 B 库候选版本的语料，评测记录也会挂到错误的知识库下。
    """

    _stub_sources(client)
    _allow_evaluation_database(monkeypatch)
    fake_service.list_index_versions = lambda _knowledge_base_id: [
        {"index_version_id": "iv_other", "status": "validating"}
    ]

    def unreachable(*_args):
        raise AssertionError("跨知识库的版本不得进入入队")

    monkeypatch.setattr("backend.app.main.create_evaluation_run", unreachable)

    response = client.post(
        "/api/knowledge-bases/kb_default/index-versions/iv_candidate/evaluation-runs",
        json={"dataset_id": "corpus_v2"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INDEX_VERSION_NOT_FOUND"


def test_evaluation_run_requires_a_configured_evaluation_database(client, monkeypatch) -> None:
    """没配评测库就不许入队，返回 503 EVALUATION_DATABASE_NOT_CONFIGURED。

    放行的话任务会永远停在 queued，而页面显示「评测中」——用户看到的是一个不会结束的
    进度条，而不是「本部署没开这个功能」。这里改的是 create_app() 闭包捕获的那个
    settings 单例，走的是真实的 require_isolated_evaluation_database 分支。
    """

    _stub_sources(client)
    monkeypatch.setattr(get_settings(), "evaluation_database_url", None)

    response = client.post(
        "/api/knowledge-bases/kb_default/index-versions/iv_candidate/evaluation-runs",
        json={"dataset_id": "corpus_v2"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EVALUATION_DATABASE_NOT_CONFIGURED"


def test_retrying_evaluation_run_is_accepted_and_audited(client, monkeypatch) -> None:
    """重试同样是 202，并按 index_version 记 index_evaluation.retry。

    审计的 resource_id 取的是仓储返回行里的 index_version_id，不是 URL 上的
    evaluation_run_id——审计要回答「谁又给哪个候选版本跑了一次评测」。
    """

    _stub_sources(client)
    _allow_evaluation_database(monkeypatch)
    monkeypatch.setattr(
        "backend.app.main.retry_evaluation_run",
        lambda *_args: _evaluation_run_row(status="queued", attempt_count=1),
    )

    response = client.post(
        f"/api/knowledge-bases/kb_default/evaluation-runs/{_EVALUATION_RUN_ID}/retry"
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["attempt_count"] == 1

    events = client.get("/api/audit/events", params={"action": "index_evaluation.retry"})
    assert [
        (item["resource_type"], item["resource_id"], item["result"]) for item in events.json()
    ] == [("index_version", "iv_candidate", "success")]


def test_cancelling_evaluation_run_is_audited(client, monkeypatch) -> None:
    """取消返回 200（不是 202：它不排新任务），并记 index_evaluation.cancel。"""

    _stub_sources(client)
    monkeypatch.setattr(
        "backend.app.main.cancel_evaluation_run",
        lambda *_args: _evaluation_run_row(status="cancelled"),
    )

    response = client.post(
        f"/api/knowledge-bases/kb_default/evaluation-runs/{_EVALUATION_RUN_ID}/cancel"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    events = client.get("/api/audit/events", params={"action": "index_evaluation.cancel"})
    assert [
        (item["resource_type"], item["resource_id"], item["result"]) for item in events.json()
    ] == [("index_version", "iv_candidate", "success")]


def test_evaluation_run_detail_exposes_the_stored_report(client, monkeypatch) -> None:
    """详情把 report_payload 转成与 /api/evaluations 同一套指标结构。

    两处走同一个 `EvaluationReportRepository.detail_from_payload`，页面上「运行记录」
    和「评测报告」看到的字段名才不会漂移成两份映射。
    """

    _stub_sources(client)
    payload = _report_payload()
    monkeypatch.setattr(
        "backend.app.main.get_evaluation_run",
        lambda *_args: _evaluation_run_row(
            status="succeeded",
            official=True,
            passed=True,
            report_payload=payload,
            models=payload["models"],
            metrics={"recall_at_5": payload["recall_at_5"]},
        ),
    )

    response = client.get(
        f"/api/knowledge-bases/kb_default/evaluation-runs/{_EVALUATION_RUN_ID}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["report_id"] == payload["report_id"]
    assert body["report"] is not None
    assert body["report"]["report_id"] == payload["report_id"]
    assert body["report"]["query_count"] == payload["query_count"]
    assert body["report"]["recall_at_5"] == payload["recall_at_5"]
    assert body["report"]["vector_mrr"] == payload["vector_mrr"]
    assert body["report"]["rerank_mrr"] == payload["rerank_mrr"]
    assert body["report"]["parameters"] == payload["parameters"]
    assert body["report"]["official"] is True
    assert body["report"]["passed"] is True
    assert body["models"] == payload["models"]
    assert body["config_snapshot"] == {"chunk_size": 500}
    assert body["component_manifest"] == {"chunking_version": "3"}


def test_unknown_evaluation_run_detail_returns_404(client, monkeypatch) -> None:
    """仓储返回 None 时是 404，不是 200 带一个空壳。

    空壳会被页面读成「这次评测存在但还没有任何结果」，而事实是这个 ID 在本知识库下
    不存在——跨库猜 ID 也走这一条。
    """

    _stub_sources(client)
    monkeypatch.setattr("backend.app.main.get_evaluation_run", lambda *_args: None)

    response = client.get("/api/knowledge-bases/kb_default/evaluation-runs/eval_missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EVALUATION_RUN_NOT_FOUND"


def test_failed_evaluation_run_never_echoes_technical_details(client, monkeypatch) -> None:
    """失败详情只给稳定错误码与中文文案，不回显 error_message 原文。

    error_message 存的是技术详情——评测库连接串、宿主绝对路径、模型加载栈。它留在库里
    供管理员排查，接口输出的是 FAILURE_MESSAGES 里能指导下一步动作的文案。这条断言
    覆盖的是「响应体里根本没有那段字符串」，而不只是「有个 failure_reason 字段」。
    """

    _stub_sources(client)
    secret = "postgresql://evaluator:s3cret@10.0.0.9:5432/rag_evaluation"
    monkeypatch.setattr(
        "backend.app.main.get_evaluation_run",
        lambda *_args: _evaluation_run_row(
            status="failed",
            attempt_count=3,
            error_code="EVALUATION_DATABASE_NOT_FOUND",
            error_message=f"connection to {secret} failed: FATAL database does not exist",
        ),
    )

    response = client.get(
        f"/api/knowledge-bases/kb_default/evaluation-runs/{_EVALUATION_RUN_ID}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_code"] == "EVALUATION_DATABASE_NOT_FOUND"
    assert body["failure_reason"] == FAILURE_MESSAGES["EVALUATION_DATABASE_NOT_FOUND"]
    assert "error_message" not in body
    assert secret not in response.text


def test_evaluation_run_routes_require_postgres(client) -> None:
    """JSON 形态下六条评测运行路由一律 503，而不是空列表或 404。

    空列表会被读成「这个版本还没跑过正式评测」，而事实是「这个部署形态根本不记录评测
    运行」——前者暗示可以再点一次「运行评测」，后者说明这里做不了。
    """

    base = "/api/knowledge-bases/kb_default"
    responses = {
        "create": client.post(
            f"{base}/index-versions/iv_candidate/evaluation-runs",
            json={"dataset_id": "corpus_v2"},
        ),
        "list": client.get(f"{base}/index-versions/iv_candidate/evaluation-runs"),
        "list_all": client.get(f"{base}/evaluation-runs"),
        "detail": client.get(f"{base}/evaluation-runs/{_EVALUATION_RUN_ID}"),
        "retry": client.post(f"{base}/evaluation-runs/{_EVALUATION_RUN_ID}/retry"),
        "cancel": client.post(f"{base}/evaluation-runs/{_EVALUATION_RUN_ID}/cancel"),
    }

    assert {name: item.status_code for name, item in responses.items()} == dict.fromkeys(
        responses, 503
    )
    assert {item.json()["error"]["code"] for item in responses.values()} == {"POSTGRES_REQUIRED"}


def test_evaluation_summary_exposes_official_separately_from_passed(client, tmp_path) -> None:
    """/api/evaluations 的每一项都带 official。

    只暴露 passed 时，页面无法区分「受控运行但没达标」和「来源不可信」，两者都显示成
    「缺少可用报告」。official 说明这份报告能不能作为三层门禁的证据，passed 只说明它
    有没有达到冻结阈值——发布结论由门禁给，不由绝对阈值提前筛掉报告。
    """

    payload = _report_payload()
    (tmp_path / "official.json").write_text(json.dumps(payload), encoding="utf-8")
    client.app.dependency_overrides[get_evaluation_reports] = (
        lambda: EvaluationReportRepository(tmp_path)
    )

    response = client.get("/api/evaluations")

    assert response.status_code == 200
    item = response.json()[0]
    assert item["report_id"] == payload["report_id"]
    assert item["official"] is True
    assert item["passed"] is True
