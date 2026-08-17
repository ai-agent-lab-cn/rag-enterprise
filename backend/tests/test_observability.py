from __future__ import annotations

import json
import logging
import stat
from pathlib import Path

import pytest

from backend.app.audit import AuditRepository
from backend.app.main import get_service


def test_request_id_is_validated_echoed_and_used_in_structured_log(client, caplog) -> None:
    caplog.set_level(logging.INFO, logger="uvicorn.error.rongrag.observability")

    accepted = client.get("/api/health", headers={"X-Request-ID": "request-demo-1234"})
    generated = client.get("/api/health", headers={"X-Request-ID": "bad id"})

    assert accepted.headers["x-request-id"] == "request-demo-1234"
    assert generated.headers["x-request-id"].startswith("req_")
    records = [
        json.loads(item.message)
        for item in caplog.records
        if item.name == "uvicorn.error.rongrag.observability"
    ]
    accepted_log = next(item for item in records if item["request_id"] == "request-demo-1234")
    assert accepted_log == {
        **accepted_log,
        "event": "request.completed",
        "method": "GET",
        "route": "/api/health",
        "status_code": 200,
        "result": "success",
    }
    assert "query" not in accepted_log
    assert "body" not in accepted_log


def test_authenticated_request_log_uses_anonymous_actor(client, caplog) -> None:
    caplog.set_level(logging.INFO, logger="uvicorn.error.rongrag.observability")

    response = client.get("/api/knowledge-bases")

    assert response.status_code == 200
    record = next(
        json.loads(item.message)
        for item in caplog.records
        if item.name == "uvicorn.error.rongrag.observability" and "/api/knowledge-bases" in item.message
    )
    assert len(record["actor_hash"]) == 16
    assert "test-admin" not in item_messages(caplog)


def test_health_endpoints_do_not_initialize_rag_models(client) -> None:
    def fail_service_initialization() -> None:
        raise AssertionError("health endpoints must not initialize the RAG service")

    client.app.dependency_overrides[get_service] = fail_service_initialization

    live = client.get("/api/health/live")
    ready = client.get("/api/health/ready")

    assert live.json() == {"status": "alive"}
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert set(ready.json()["checks"]) == {
        "auth_store",
        "audit_store",
        "knowledge_base_registry",
        "conversation_store",
    }


def test_admin_metrics_cover_requests_rag_and_indexing(client) -> None:
    assert client.post(
        "/api/documents",
        files={"file": ("metrics.md", "指标资料", "text/markdown")},
    ).status_code == 201
    assert client.post(
        "/api/query",
        json={"question": "指标资料是什么？", "retrieve_k": 5, "rerank_k": 3},
    ).status_code == 200

    response = client.get("/api/system/metrics")
    payload = response.json()

    assert response.status_code == 200
    assert payload["requests"]["total"] >= 3
    assert payload["rag"]["queries"] == 1
    assert payload["rag"]["total_ms_total"] == 6
    assert payload["indexing"]["attempts"] == 1
    assert payload["indexing"]["successes"] == 1


def test_audit_api_records_sensitive_changes_without_business_content(client) -> None:
    member = client.post(
        "/api/members",
        json={
            "username": "audit-member",
            "password": "audit-member-password-long",
            "display_name": "审计成员",
            "role": "member",
        },
    )
    knowledge_base = client.post(
        "/api/knowledge-bases",
        json={"name": "审计知识库", "description": "不应进入审计正文"},
    )
    knowledge_base_id = knowledge_base.json()["knowledge_base_id"]
    assert client.put(
        f"/api/knowledge-bases/{knowledge_base_id}/members/{member.json()['user_id']}"
    ).status_code == 204
    assert client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("audit.md", "机密业务正文", "text/markdown")},
    ).status_code == 201

    response = client.get("/api/audit/events", params={"limit": 100})
    serialized = response.text
    actions = {event["action"] for event in response.json()}

    assert response.status_code == 200
    assert {
        "auth.bootstrap",
        "member.create",
        "knowledge_base.create",
        "knowledge_base.member_grant",
        "document.upload",
    }.issubset(actions)
    assert "audit-member-password-long" not in serialized
    assert "机密业务正文" not in serialized
    assert "不应进入审计正文" not in serialized


def test_member_cannot_read_metrics_or_audit(client) -> None:
    member = client.post(
        "/api/members",
        json={
            "username": "read-denied",
            "password": "member-password-is-long-enough",
            "display_name": "普通成员",
            "role": "member",
        },
    ).json()
    login = client.post(
        "/api/auth/login",
        json={"username": "read-denied", "password": "member-password-is-long-enough"},
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    assert client.get("/api/system/metrics", headers=headers).status_code == 403
    assert client.get("/api/audit/events", headers=headers).status_code == 403
    assert member["role"] == "member"


def test_audit_chain_detects_tampering_and_uses_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "audit" / "events.json"
    repository = AuditRepository(path)
    repository.record(
        "member.create",
        actor_id="usr_actor",
        actor_role="admin",
        resource_type="user",
        resource_id="usr_target",
        result="success",
    )

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["events"][0]["result"] = "failed"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="hash"):
        AuditRepository(path)


def test_audit_api_is_read_only(client) -> None:
    schema = client.app.openapi()
    assert set(schema["paths"]["/api/audit/events"]) == {"get"}


def item_messages(caplog) -> str:
    return "\n".join(item.message for item in caplog.records)
