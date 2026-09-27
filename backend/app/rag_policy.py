"""RAG 策略的公开写契约。

PUT 只允许改「Web 开关 + 可信域名」两个公开字段，`rollout_stage` 与三个内部阈值由服务端
保留。旧客户端仍会「GET 回来原样 PUT 回去」，所以内部字段不是直接拒收，而是「传了就必须
与当前值相同」——值没变的回传照常接受，真要改才报 `RAG_POLICY_FIELD_READ_ONLY`。
"""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlsplit

from .modular_rag import RAGPolicy
from .schemas import RAGPolicyUpdate

# 字段名 → 面向管理员的中文名。判定与文案同一个来源，拒绝时说得出是哪个字段被拒，
# 而不是只回一句「有字段只读」。
_SERVER_MANAGED_FIELDS = {
    "rollout_stage": "发布阶段",
    "intent_confidence_threshold": "意图置信度阈值",
    "minimum_evidence_count": "最少证据数",
    "max_web_results": "Web 结果上限",
}


class RAGPolicyFieldReadOnly(ValueError):
    pass


class WebSearchProviderNotConfigured(ValueError):
    pass


def merge_public_rag_policy_update(
    current: RAGPolicy,
    payload: RAGPolicyUpdate,
    searxng_base_url: str,
) -> RAGPolicy:
    rejected = [
        label
        for field, label in _SERVER_MANAGED_FIELDS.items()
        if (requested := getattr(payload, field)) is not None
        and requested != getattr(current, field)
    ]
    if rejected:
        raise RAGPolicyFieldReadOnly(
            f"{'、'.join(rejected)}由服务端保留，不能通过 RAG 策略接口修改。"
        )
    if payload.web_search_enabled and not _is_configured_search_endpoint(searxng_base_url):
        raise WebSearchProviderNotConfigured(
            "启用受控 Web 检索前必须先配置 SEARXNG_BASE_URL 为 http 或 https 地址。"
        )
    return replace(
        current,
        web_search_enabled=payload.web_search_enabled,
        allowed_domains=tuple(payload.allowed_domains),
    )


def _is_configured_search_endpoint(searxng_base_url: str) -> bool:
    """只校验配置格式，不发起网络请求——保存策略不该依赖 SearXNG 此刻是否在线。"""

    parsed = urlsplit(searxng_base_url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
