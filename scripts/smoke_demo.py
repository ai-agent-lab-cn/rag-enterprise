#!/usr/bin/env python3
"""验证已部署 Demo 的健康、演示资料和基础问答链路。"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any


def request_json(
    base_url: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = 30,
) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers={
            **({"Content-Type": "application/json"} if data is not None else {}),
            **({"Authorization": f"Bearer {token}"} if token is not None else {}),
        },
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"{path} 返回 HTTP {response.status}")
        return json.load(response)


def wait_for_health(base_url: str, deadline_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + deadline_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            health = request_json(base_url, "/api/health", timeout=20)
            if health.get("status") == "ok":
                return health
            last_error = RuntimeError(f"健康状态异常：{health!r}")
        except (OSError, TimeoutError, urllib.error.HTTPError, ValueError) as exc:
            last_error = exc
        time.sleep(5)
    raise RuntimeError(f"等待 Demo 健康检查超时：{last_error}")


def authenticate(base_url: str, username: str, password: str) -> str:
    status = request_json(base_url, "/api/auth/bootstrap")
    path = "/api/auth/bootstrap" if status.get("required") else "/api/auth/login"
    payload = {"username": username, "password": password}
    if status.get("required"):
        payload["display_name"] = "CI 冒烟管理员"
    response = request_json(base_url, path, payload=payload)
    token = response.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("认证接口未返回会话令牌")
    return token


def run_smoke(
    base_url: str,
    deadline_seconds: int,
    allow_retrieval_only: bool,
    username: str,
    password: str,
) -> dict[str, Any]:
    health = wait_for_health(base_url, deadline_seconds)
    token = authenticate(base_url, username, password)
    knowledge_bases = request_json(base_url, "/api/knowledge-bases", token=token, timeout=30)
    default = next(
        (item for item in knowledge_bases if item.get("knowledge_base_id") == "kb_default"),
        None,
    )
    if default is None or default.get("document_count", 0) < 1:
        raise RuntimeError("默认知识库没有完成演示资料初始化")

    result = request_json(
        base_url,
        "/api/knowledge-bases/kb_default/query",
        payload={
            "question": "RongRAG Studio 如何保证回答可追溯？",
            "retrieve_k": 5,
            "rerank_k": 3,
        },
        token=token,
        timeout=deadline_seconds,
    )
    allowed_statuses = {"answered"}
    if allow_retrieval_only:
        allowed_statuses.add("retrieval_only")
    if result.get("answer_status") not in allowed_statuses:
        raise RuntimeError(f"问答状态未通过：{result.get('answer_status')}")

    filenames = {source.get("filename") for source in result.get("sources", [])}
    if "project-profile.md" not in filenames:
        raise RuntimeError("问答来源未包含 project-profile.md")

    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "base_url": base_url.rstrip("/"),
        "health": health.get("status"),
        "generation_ready": health.get("generation_ready"),
        "default_document_count": default["document_count"],
        "answer_status": result["answer_status"],
        "source": "project-profile.md",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="Demo 根地址，例如 https://example.onrender.com")
    parser.add_argument("--deadline", type=int, default=300, help="冷启动最长等待秒数")
    parser.add_argument(
        "--allow-retrieval-only",
        action="store_true",
        help="仅用于未配置 Gemini 的本地/CI 容器验证",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("SMOKE_ADMIN_USERNAME"),
        help="冒烟管理员用户名，也可通过 SMOKE_ADMIN_USERNAME 提供",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("SMOKE_ADMIN_PASSWORD"),
        help="冒烟管理员密码，也可通过 SMOKE_ADMIN_PASSWORD 提供",
    )
    args = parser.parse_args()
    if not args.username or not args.password:
        parser.error("必须提供冒烟管理员用户名和密码")
    evidence = run_smoke(
        args.base_url,
        args.deadline,
        args.allow_retrieval_only,
        args.username,
        args.password,
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
