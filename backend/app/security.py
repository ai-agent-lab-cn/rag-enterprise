from __future__ import annotations

import math
import os
import unicodedata
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import AppError
from .parsers import SUPPORTED_EXTENSIONS

_ALLOWED_CONTENT_TYPES = {
    ".md": {"application/octet-stream", "text/markdown", "text/plain"},
    ".txt": {"application/octet-stream", "text/plain"},
    ".pdf": {"application/octet-stream", "application/pdf"},
}


class SecurityBoundaryMiddleware:
    """拒绝明显过大的请求，并为 API 响应设置最小安全头。"""

    def __init__(self, app: ASGIApp, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is not None and content_length > self.max_body_bytes:
            await _send_json_error(
                send,
                413,
                "REQUEST_TOO_LARGE",
                "请求体超过服务允许的大小。",
            )
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"cache-control", b"no-store"),
                        (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                    ]
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class SlidingWindowRateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: int,
        clock: Callable[[], float] = monotonic,
    ):
        self.limit = limit
        self.window_seconds = window_seconds
        self.clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = self.clock()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                retry_after = max(1, math.ceil(events[0] + self.window_seconds - now))
                raise AppError(
                    "RATE_LIMITED",
                    "请求过于频繁，请稍后重试。",
                    429,
                    {"retry_after_seconds": retry_after},
                    {"Retry-After": str(retry_after)},
                )
            events.append(now)


class ConcurrencyGate:
    def __init__(self, limit: int):
        self.limit = limit
        self._active = 0
        self._lock = Lock()

    @contextmanager
    def slot(self) -> Iterator[None]:
        with self._lock:
            if self._active >= self.limit:
                raise AppError(
                    "SERVICE_BUSY",
                    "当前处理任务较多，请稍后重试。",
                    429,
                    headers={"Retry-After": "1"},
                )
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1


class AbuseProtection:
    def __init__(
        self,
        *,
        login_limit: int,
        expensive_limit: int,
        window_seconds: int,
        concurrency_limit: int,
    ):
        self.login = SlidingWindowRateLimiter(login_limit, window_seconds)
        self.expensive = SlidingWindowRateLimiter(expensive_limit, window_seconds)
        self.concurrency = ConcurrencyGate(concurrency_limit)

    def check_login(self, client_key: str, username: str) -> None:
        normalized = username.strip().casefold()[:64] or "unknown"
        self.login.check(f"{client_key}:{normalized}")

    def check_expensive(self, user_id: str) -> None:
        self.expensive.check(user_id)


def validate_upload(
    filename: str | None,
    content_type: str | None,
    content: bytes,
    *,
    max_filename_chars: int,
) -> str:
    safe_name = validate_upload_filename(filename, max_filename_chars=max_filename_chars)
    extension = Path(safe_name).suffix.lower()
    actual_content_type = (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if actual_content_type not in _ALLOWED_CONTENT_TYPES[extension]:
        raise AppError("INVALID_CONTENT_TYPE", "文件类型与上传声明不一致。", 415)
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise AppError("INVALID_PDF", "PDF 无法解析或已损坏。")
    if extension in {".md", ".txt"} and b"\x00" in content:
        raise AppError("INVALID_TEXT_FILE", "文本文件包含不支持的二进制内容。")
    return safe_name


def validate_upload_filename(filename: str | None, *, max_filename_chars: int) -> str:
    normalized = unicodedata.normalize("NFC", (filename or "").strip())
    if not normalized:
        raise AppError("INVALID_FILENAME", "文件名不能为空。")
    if len(normalized) > max_filename_chars:
        raise AppError("INVALID_FILENAME", "文件名过长。")
    if "/" in normalized or "\\" in normalized or normalized in {".", ".."}:
        raise AppError("INVALID_FILENAME", "文件名不能包含路径。")
    if normalized.startswith(".") or normalized.endswith((".", " ")):
        raise AppError("INVALID_FILENAME", "文件名格式不受支持。")
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        raise AppError("INVALID_FILENAME", "文件名包含不支持的控制字符。")
    if Path(normalized).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise AppError(
            "UNSUPPORTED_FILE",
            "仅支持 Markdown、TXT、PDF、HTML、DOCX、XLSX 和 CSV 文件。",
            415,
        )
    return normalized


def write_private_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


async def _send_json_error(send: Send, status: int, code: str, message: str) -> None:
    import json

    body = json.dumps(
        {"error": {"code": code, "message": message, "details": None}},
        ensure_ascii=False,
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"cache-control", b"no-store"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                (b"content-type", b"application/json; charset=utf-8"),
                (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                (b"referrer-policy", b"no-referrer"),
                (b"x-content-type-options", b"nosniff"),
                (b"x-frame-options", b"DENY"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
