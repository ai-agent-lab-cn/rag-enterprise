from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx


class WebSecurityError(ValueError):
    pass


@dataclass(frozen=True)
class WebSearchResult:
    url: str
    title: str
    snippet: str
    content: str
    retrieved_at: str
    content_sha256: str
    rank: int


# HTML 文本提取器
class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._ignored = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored and data.strip():
            self.parts.append(data.strip())


# 验证网页 URL 的安全性，确保其符合 HTTPS、域名白名单等要求
def validate_web_url(url: str, allowed_domains: tuple[str, ...]) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise WebSecurityError("Web 来源只允许 HTTPS。")
    if parsed.username or parsed.password:
        raise WebSecurityError("Web 来源 URL 不能包含凭据。")
    hostname = parsed.hostname.rstrip(".").casefold()
    domains = tuple(item.strip().rstrip(".").casefold() for item in allowed_domains if item.strip())
    if not domains or not any(hostname == item or hostname.endswith(f".{item}") for item in domains):
        raise WebSecurityError("Web 来源不在知识库可信域名白名单中。")
    if hostname in {"localhost", "localhost.localdomain"}:
        raise WebSecurityError("Web 来源不能指向本机。")
    try:
        addresses = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebSecurityError("Web 来源域名无法解析。") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise WebSecurityError("Web 来源不能指向内网或保留地址。")
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    return urlunsplit(("https", f"{hostname}{port}", parsed.path or "/", parsed.query, ""))


# 安全的网页内容获取器
class SafeWebContentFetcher:
    def __init__(self, timeout_seconds: float = 8, max_bytes: int = 2 * 1024 * 1024):
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes

    def fetch(self, url: str, allowed_domains: tuple[str, ...]) -> tuple[str, str]:
        current = validate_web_url(url, allowed_domains)
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
            for _redirect in range(4):
                with client.stream(
                    "GET", current, headers={"User-Agent": "RongRAG-WebEvidence/1.0"}
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise WebSecurityError("Web 来源重定向缺少目标地址。")
                        current = validate_web_url(urljoin(current, location), allowed_domains)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                    if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                        raise WebSecurityError("Web 来源内容类型不受支持。")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise WebSecurityError("Web 来源正文超过大小限制。")
                        chunks.append(chunk)
                    raw = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                    return current, _extract_text(raw, content_type)
        raise WebSecurityError("Web 来源重定向次数过多。")


# 网页搜索提供器，使用 SearXNG 作为搜索引擎
class SearXNGWebSearchProvider:
    def __init__(self, base_url: str, fetcher: SafeWebContentFetcher | None = None):
        self.base_url = base_url.rstrip("/")
        self.fetcher = fetcher or SafeWebContentFetcher()

    def search(
        self,
        query: str,
        allowed_domains: tuple[str, ...],
        limit: int = 5,
    ) -> list[WebSearchResult]:
        if not self.base_url or not allowed_domains:
            return []
        site_filter = " OR ".join(f"site:{domain}" for domain in allowed_domains)
        search_query = f"({query}) ({site_filter})"
        with httpx.Client(timeout=8) as client:
            response = client.get(
                f"{self.base_url}/search",
                params={"q": search_query, "format": "json", "language": "zh-CN", "safesearch": 1},
            )
            response.raise_for_status()
            payload = response.json()
        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            return []
        results: list[WebSearchResult] = []
        seen: set[str] = set()
        for item in raw_results:
            if len(results) >= min(max(limit, 1), 5) or not isinstance(item, dict):
                break
            try:
                url = validate_web_url(str(item.get("url") or ""), allowed_domains)
            except WebSecurityError:
                continue
            if url in seen:
                continue
            seen.add(url)
            snippet = _normalize_text(str(item.get("content") or ""))[:1000]
            try:
                final_url, content = self.fetcher.fetch(url, allowed_domains)
            except (httpx.HTTPError, WebSecurityError):
                final_url, content = url, snippet
            if not content:
                continue
            title = _normalize_text(str(item.get("title") or url))[:300]
            stored_content = content[:20_000]
            results.append(
                WebSearchResult(
                    url=final_url,
                    title=title,
                    snippet=snippet,
                    content=stored_content,
                    retrieved_at=datetime.now(UTC).isoformat(),
                    content_sha256=hashlib.sha256(stored_content.encode()).hexdigest(),
                    rank=len(results) + 1,
                )
            )
        return results


def _extract_text(raw: str, content_type: str) -> str:
    if content_type == "text/plain":
        return _normalize_text(raw)
    parser = _TextExtractor()
    parser.feed(raw)
    return _normalize_text(" ".join(parser.parts))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()
