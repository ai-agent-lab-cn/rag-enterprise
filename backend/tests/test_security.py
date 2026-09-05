from __future__ import annotations

import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.config import Settings
from backend.app.errors import AppError, install_error_handlers
from backend.app.security import (
    ConcurrencyGate,
    SecurityBoundaryMiddleware,
    SlidingWindowRateLimiter,
    validate_upload,
    validate_upload_filename,
    write_private_file,
)


def test_api_responses_include_security_headers(client) -> None:
    response = client.get("/api/health")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_request_body_limit_rejects_before_endpoint() -> None:
    app = FastAPI()
    app.add_middleware(SecurityBoundaryMiddleware, max_body_bytes=3)

    @app.post("/payload")
    async def payload() -> dict[str, bool]:
        return {"accepted": True}

    response = TestClient(app).post("/payload", content=b"four")

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "filename",
    ["../secret.txt", "folder/secret.txt", "folder\\secret.txt", ".hidden.md", "bad\x00.txt"],
)
def test_upload_filename_rejects_paths_hidden_names_and_controls(filename: str) -> None:
    with pytest.raises(AppError, match="文件名"):
        validate_upload_filename(filename, max_filename_chars=160)


def test_upload_validation_rejects_mime_and_content_mismatch() -> None:
    with pytest.raises(AppError) as content_type_error:
        validate_upload("notes.txt", "application/pdf", b"plain text", max_filename_chars=160)
    with pytest.raises(AppError) as pdf_error:
        validate_upload("notes.pdf", "application/pdf", b"not a pdf", max_filename_chars=160)
    with pytest.raises(AppError) as binary_text_error:
        validate_upload(
            "notes.txt",
            "text/plain",
            b"plain\x00binary",
            max_filename_chars=160,
        )

    assert content_type_error.value.code == "INVALID_CONTENT_TYPE"
    assert pdf_error.value.code == "INVALID_PDF"
    assert binary_text_error.value.code == "INVALID_TEXT_FILE"


def test_upload_validation_accepts_structured_document_mime_types() -> None:
    assert validate_upload(
        "论文-动态测试.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        b"PK\x03\x04docx",
        max_filename_chars=160,
    ) == "论文-动态测试.docx"


def test_uploaded_original_uses_private_permissions(tmp_path) -> None:
    """上传的源文件与其目录必须只有当前服务用户可读写。

    这个约束原先由 API 层在 Chroma 路径上落盘时保证，覆盖也挂在那条端到端路径上。
    Chroma 移除后落盘归 PostgresAsyncRAGService.index_document，覆盖改挂在
    write_private_file 本身——权限约束不该随调用点迁移而失去覆盖。
    """

    target = tmp_path / "uploads" / "kb_default" / "private.md"

    write_private_file(target, "私密资料".encode())

    assert target.read_text(encoding="utf-8") == "私密资料"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_validation_errors_do_not_echo_password_or_question(client) -> None:
    secret = "sensitive-password-" * 20
    login = client.post(
        "/api/auth/login",
        json={"username": "test-admin", "password": secret},
    )
    question = client.post(
        "/api/query",
        json={"question": "secret\x00question", "retrieve_k": 5, "rerank_k": 3},
    )

    assert login.status_code == 422
    assert question.status_code == 422
    assert login.json()["error"]["code"] == "VALIDATION_ERROR"
    assert secret not in login.text
    assert "secret" not in question.text


def test_unexpected_errors_do_not_expose_internal_exception() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/failure")
    async def failure() -> None:
        raise RuntimeError("internal-token-must-not-leak")

    response = TestClient(app, raise_server_exceptions=False).get("/failure")

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "服务暂时不可用，请稍后重试。",
        "details": None,
    }
    assert "internal-token" not in response.text


def test_sliding_window_rate_limit_returns_retry_boundary() -> None:
    now = [100.0]
    limiter = SlidingWindowRateLimiter(2, 60, clock=lambda: now[0])

    limiter.check("user")
    limiter.check("user")
    with pytest.raises(AppError) as limited:
        limiter.check("user")

    assert limited.value.code == "RATE_LIMITED"
    assert limited.value.status_code == 429
    assert limited.value.headers == {"Retry-After": "60"}

    now[0] = 161.0
    limiter.check("user")


def test_concurrency_gate_rejects_saturated_work() -> None:
    gate = ConcurrencyGate(1)

    with gate.slot(), pytest.raises(AppError) as busy:
        with gate.slot():
            pass

    assert busy.value.code == "SERVICE_BUSY"
    with gate.slot():
        pass


def test_list_endpoints_apply_bounded_pagination(client) -> None:
    for name in ("知识库 A", "知识库 B"):
        assert client.post(
            "/api/knowledge-bases",
            json={"name": name, "description": ""},
        ).status_code == 201

    page = client.get("/api/knowledge-bases", params={"offset": 1, "limit": 1})
    invalid = client.get("/api/knowledge-bases", params={"limit": 101})

    assert page.status_code == 200
    assert len(page.json()) == 1
    assert page.json()[0]["name"] == "知识库 A"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"


def test_production_configuration_requires_explicit_https_origin() -> None:
    with pytest.raises(ValidationError, match="https"):
        Settings(
            _env_file=None,
            app_environment="production",
            frontend_origin="http://example.com",
        )

    settings = Settings(
        _env_file=None,
        app_environment="production",
        frontend_origin="https://app.example.com, https://admin.example.com/",
    )
    assert settings.frontend_origins == [
        "https://app.example.com",
        "https://admin.example.com",
    ]
