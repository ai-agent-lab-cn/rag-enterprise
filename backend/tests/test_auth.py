import json
from pathlib import Path

import pytest

from backend.app.auth import AuthRepository

ADMIN_PASSWORD = "correct-horse-battery-staple"
MEMBER_PASSWORD = "member-password-is-long-enough"


def test_auth_repository_hashes_secrets_and_revokes_sessions(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    repository = AuthRepository(path, session_ttl_hours=1)

    session = repository.bootstrap_admin("admin-user", ADMIN_PASSWORD, "管理员")
    stored = path.read_text(encoding="utf-8")

    assert ADMIN_PASSWORD not in stored
    assert session.token not in stored
    assert repository.resolve_session(session.token).user.role == "admin"
    assert repository.revoke_session(session.token) is True
    assert repository.resolve_session(session.token) is None


def test_auth_repository_rejects_duplicate_bootstrap_and_last_admin_removal(tmp_path: Path) -> None:
    repository = AuthRepository(tmp_path / "auth.json")
    session = repository.bootstrap_admin("admin-user", ADMIN_PASSWORD, "管理员")

    with pytest.raises(ValueError, match="bootstrap"):
        repository.bootstrap_admin("other-admin", ADMIN_PASSWORD, "其他管理员")
    with pytest.raises(PermissionError, match="last active admin"):
        repository.update_user(
            session.user.user_id,
            display_name=None,
            role="member",
            active=None,
            password=None,
        )


def test_auth_store_fails_closed_when_shape_is_invalid(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"version": 1, "users": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="auth store"):
        AuthRepository(path)


def test_auth_store_fails_closed_for_unknown_session_user(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    repository = AuthRepository(path)
    repository.bootstrap_admin("admin-user", ADMIN_PASSWORD, "管理员")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["sessions"][0]["user_id"] = "usr_0000000000000000"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="session"):
        AuthRepository(path)


def test_anonymous_requests_are_denied_but_health_and_bootstrap_status_are_public(client) -> None:
    authorization = client.headers.pop("Authorization")
    try:
        health = client.get("/api/health")
        status = client.get("/api/auth/bootstrap")
        denied = client.get("/api/knowledge-bases")
        malformed = client.get(
            "/api/knowledge-bases",
            headers={"Authorization": "Basic not-supported"},
        )
    finally:
        client.headers["Authorization"] = authorization

    assert health.status_code == 200
    assert status.json() == {"required": False}
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert malformed.status_code == 401
    assert malformed.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_bootstrap_is_one_time_only(client) -> None:
    response = client.post(
        "/api/auth/bootstrap",
        json={
            "username": "second-admin",
            "password": ADMIN_PASSWORD,
            "display_name": "第二个管理员",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AUTH_BOOTSTRAP_COMPLETED"


def test_member_only_sees_authorized_knowledge_base_and_cannot_administer(client) -> None:
    allowed_id = client.post(
        "/api/knowledge-bases",
        json={"name": "成员可见", "description": ""},
    ).json()["knowledge_base_id"]
    denied_id = client.post(
        "/api/knowledge-bases",
        json={"name": "成员不可见", "description": ""},
    ).json()["knowledge_base_id"]
    member = client.post(
        "/api/members",
        json={
            "username": "team-member",
            "password": MEMBER_PASSWORD,
            "display_name": "团队成员",
            "role": "member",
        },
    )
    assert member.status_code == 201
    user_id = member.json()["user_id"]
    assert client.put(f"/api/knowledge-bases/{allowed_id}/members/{user_id}").status_code == 204

    login = client.post(
        "/api/auth/login",
        json={"username": "team-member", "password": MEMBER_PASSWORD},
    )
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    listed = client.get("/api/knowledge-bases", headers=member_headers)
    allowed = client.get(f"/api/knowledge-bases/{allowed_id}", headers=member_headers)
    denied = client.get(f"/api/knowledge-bases/{denied_id}", headers=member_headers)
    legacy_denied = client.get("/api/documents", headers=member_headers)
    create_denied = client.post(
        "/api/knowledge-bases",
        headers=member_headers,
        json={"name": "越权创建", "description": ""},
    )
    evaluation_readonly = client.get("/api/evaluations", headers=member_headers)

    assert [item["knowledge_base_id"] for item in listed.json()] == [allowed_id]
    assert allowed.status_code == 200
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "KNOWLEDGE_BASE_NOT_FOUND"
    assert legacy_denied.status_code == 404
    assert create_denied.status_code == 403
    assert create_denied.json()["error"]["code"] == "ADMIN_REQUIRED"
    assert evaluation_readonly.status_code == 200


def test_logout_and_member_deactivation_invalidate_sessions(client) -> None:
    member = client.post(
        "/api/members",
        json={
            "username": "revoked-member",
            "password": MEMBER_PASSWORD,
            "display_name": "待停用成员",
            "role": "member",
        },
    ).json()
    first = client.post(
        "/api/auth/login",
        json={"username": "revoked-member", "password": MEMBER_PASSWORD},
    ).json()["access_token"]
    first_headers = {"Authorization": f"Bearer {first}"}

    assert client.get("/api/auth/me", headers=first_headers).status_code == 200
    assert client.post("/api/auth/logout", headers=first_headers).status_code == 204
    expired = client.get("/api/auth/me", headers=first_headers)
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "SESSION_INVALID"

    second = client.post(
        "/api/auth/login",
        json={"username": "revoked-member", "password": MEMBER_PASSWORD},
    ).json()["access_token"]
    assert client.put(f"/api/members/{member['user_id']}", json={"active": False}).status_code == 200
    disabled = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {second}"},
    )
    assert disabled.status_code == 401
    assert disabled.json()["error"]["code"] == "SESSION_INVALID"


def test_invalid_credentials_and_unknown_member_are_stable_errors(client) -> None:
    login = client.post(
        "/api/auth/login",
        json={"username": "test-admin", "password": "incorrect-password"},
    )
    missing = client.put(
        "/api/members/usr_0000000000000000",
        json={"display_name": "不存在"},
    )

    assert login.status_code == 401
    assert login.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "MEMBER_NOT_FOUND"


def test_every_business_route_declares_bearer_security(client) -> None:
    public_operations = {
        ("/api/health", "get"),
        ("/api/health/live", "get"),
        ("/api/health/ready", "get"),
        ("/api/auth/bootstrap", "get"),
        ("/api/auth/bootstrap", "post"),
        ("/api/auth/login", "post"),
    }
    schema = client.app.openapi()

    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if not path.startswith("/api/") or (path, method) in public_operations:
                continue
            assert operation.get("security") == [{"HTTPBearer": []}], f"{method.upper()} {path} 未声明认证"
