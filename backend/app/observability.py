from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from contextvars import ContextVar
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_actor_hash: ContextVar[str | None] = ContextVar("actor_hash", default=None)
_logger = logging.getLogger("uvicorn.error.rongrag.observability")
_logger.setLevel(logging.INFO)


def current_request_id() -> str:
    return _request_id.get() or "request_unavailable"


def bind_actor(user_id: str) -> str:
    actor_hash = hash_identifier(user_id)
    _actor_hash.set(actor_hash)
    return actor_hash


def current_actor_hash() -> str | None:
    return _actor_hash.get()


def hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def structured_log(event: str, *, level: int = logging.INFO, **fields: object) -> None:
    payload: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": logging.getLevelName(level),
        "event": event,
        "request_id": current_request_id(),
    }
    actor_hash = current_actor_hash()
    if actor_hash is not None:
        payload["actor_hash"] = actor_hash
    payload.update({key: value for key, value in fields.items() if _is_safe_log_value(value)})
    _logger.log(level, json.dumps(payload, ensure_ascii=False, sort_keys=True))


class MetricsRegistry:
    """进程内指标聚合；只记录计数与耗时，不记录业务正文。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._request_total = 0
        self._request_errors = 0
        self._routes: dict[str, dict[str, int | float]] = defaultdict(
            lambda: {"requests": 0, "errors": 0, "duration_ms_total": 0.0, "duration_ms_max": 0.0}
        )
        self._rag: dict[str, int | float] = {
            "queries": 0,
            "failures": 0,
            "retrieval_ms_total": 0.0,
            "rerank_ms_total": 0.0,
            "generation_ms_total": 0.0,
            "total_ms_total": 0.0,
        }
        self._indexing: dict[str, int | float] = {
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "duration_ms_total": 0.0,
        }

    def record_request(self, method: str, route: str, status_code: int, duration_ms: float) -> None:
        key = f"{method.upper()} {route}"
        with self._lock:
            self._request_total += 1
            if status_code >= 400:
                self._request_errors += 1
            item = self._routes[key]
            item["requests"] = int(item["requests"]) + 1
            item["errors"] = int(item["errors"]) + (1 if status_code >= 400 else 0)
            item["duration_ms_total"] = round(float(item["duration_ms_total"]) + duration_ms, 2)
            item["duration_ms_max"] = max(float(item["duration_ms_max"]), duration_ms)

    def record_rag(self, latency_ms: dict[str, float], *, failed: bool) -> None:
        with self._lock:
            self._rag["queries"] = int(self._rag["queries"]) + 1
            self._rag["failures"] = int(self._rag["failures"]) + (1 if failed else 0)
            for stage in ("retrieval", "rerank", "generation", "total"):
                key = f"{stage}_ms_total"
                self._rag[key] = round(float(self._rag[key]) + float(latency_ms.get(stage, 0.0)), 2)

    def record_index(self, duration_ms: float, *, failed: bool) -> None:
        with self._lock:
            self._indexing["attempts"] = int(self._indexing["attempts"]) + 1
            outcome = "failures" if failed else "successes"
            self._indexing[outcome] = int(self._indexing[outcome]) + 1
            self._indexing["duration_ms_total"] = round(
                float(self._indexing["duration_ms_total"]) + duration_ms,
                2,
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "generated_at": datetime.now(UTC).isoformat(),
                "requests": {
                    "total": self._request_total,
                    "errors": self._request_errors,
                    "routes": {key: dict(value) for key, value in sorted(self._routes.items())},
                },
                "rag": dict(self._rag),
                "indexing": dict(self._indexing),
            }


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp, metrics: MetricsRegistry):
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id_from_scope(scope)
        request_token = _request_id.set(request_id)
        actor_token = _actor_hash.set(None)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            route = getattr(scope.get("route"), "path", "unmatched")
            method = str(scope.get("method", "UNKNOWN"))
            self.metrics.record_request(method, route, status_code, duration_ms)
            structured_log(
                "request.completed",
                method=method,
                route=route,
                status_code=status_code,
                duration_ms=duration_ms,
                result="error" if status_code >= 400 else "success",
            )
            _actor_hash.reset(actor_token)
            _request_id.reset(request_token)


def _request_id_from_scope(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() != b"x-request-id":
            continue
        candidate = value.decode("ascii", errors="ignore")
        if _REQUEST_ID_PATTERN.fullmatch(candidate):
            return candidate
    return f"req_{uuid4().hex}"


def _is_safe_log_value(value: object) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    return isinstance(value, str) and len(value) <= 256 and "\n" not in value and "\r" not in value
