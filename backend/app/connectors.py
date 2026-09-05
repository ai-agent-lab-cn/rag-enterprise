"""数据源连接器协议与本地目录实现。

协议只有「列举」和「取内容」两个能力，没有「增量拉取」。这是拿 S3 与 GitHub 两种未来
实现压测后的结论：GitHub 有 tree diff 能直接返回增量，S3 与本地目录都没有变更流；若协议
提供 ``list_changes(cursor)``，后两者的实现只能退化成「全量列举后自己算差异」，那这个方法
就是在骗调用方。增量因此是框架层的能力（``data_source_sync``），对所有连接器一致。
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, Protocol
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from minio import Minio
from minio.error import S3Error

from .errors import AppError


class SourceObject(NamedTuple):
    """数据源里的一个对象。

    ``version`` 的契约是「**内容变了，version 一定变**」。反向不保证——version 变了
    内容未必变，取决于连接器：本地目录用内容 SHA-256，满足双向；S3 用服务端 ETag，
    而 ETag 在分段上传时是分段配置的函数，同一份内容换个 part_size 重传就会变（实测）。

    接受这个方向的误判是因为它安全：多做一次索引只浪费算力，结果正确；而致命的那一半
    ——内容变了却没被发现，导致检索到过期内容——不会发生。

    ``modified_at`` 只进展示层，不参与任何判定：同内容重新落盘会刷新时间戳，
    内容改动也可能不改变 size。
    """

    key: str
    version: str
    size: int
    modified_at: datetime | None


class Connector(Protocol):
    def list_objects(self) -> Iterator[SourceObject]: ...
    def fetch(self, key: str) -> bytes: ...
    def metadata(self, key: str) -> dict[str, object]: ...


class WebConnector:
    """受控 URL 列表连接器；不跟随任意站内链接，避免抓取范围失控。"""

    def __init__(
        self, urls: list[str], max_bytes: int | None = None,
        sitemap_url: str | None = None, max_objects: int = 1000,
    ):
        if not urls and not sitemap_url:
            raise AppError("SOURCE_CONFIGURATION_INVALID", "Web 数据源至少配置一个 URL。", 400)
        self.urls = [self._validate_url(value) for value in urls]
        self.sitemap_url = self._validate_url(sitemap_url) if sitemap_url else None
        self.max_bytes = max_bytes
        if max_objects < 1 or max_objects > 10_000:
            raise AppError("SOURCE_CONFIGURATION_INVALID", "Web 最大资源数必须在 1 到 10000 之间。", 400)
        self.max_objects = max_objects
        self.skipped: list[tuple[str, int]] = []
        self._cache: dict[str, bytes] = {}
        self._url_by_key: dict[str, str] = {}

    @staticmethod
    def _validate_url(value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AppError("SOURCE_CONFIGURATION_INVALID", "Web URL 必须使用 HTTP 或 HTTPS。", 400)
        hostname = parsed.hostname or ""
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or 443)}
        except socket.gaierror as exc:
            raise AppError("SOURCE_UNAVAILABLE", "Web 数据源域名无法解析。", 409) from exc
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise AppError("SOURCE_URL_FORBIDDEN", "Web 数据源不允许访问本机或内网地址。", 409)
        return value.strip()

    def _discovery_urls(self, client: httpx.Client) -> list[str]:
        if not self.sitemap_url:
            return self.urls[: self.max_objects]
        response = client.get(self.sitemap_url)
        response.raise_for_status()
        self._validate_url(str(response.url))
        if self.max_bytes is not None and len(response.content) > self.max_bytes:
            raise AppError("SOURCE_OBJECT_TOO_LARGE", "Sitemap 超过允许大小。", 409)
        root = ElementTree.fromstring(response.content)
        urls = [self._validate_url((node.text or "").strip()) for node in root.findall(".//{*}loc")]
        return list(dict.fromkeys([*self.urls, *urls]))[: self.max_objects]

    def list_objects(self) -> Iterator[SourceObject]:
        self.skipped = []
        self._cache = {}
        self._url_by_key = {}
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            for url in self._discovery_urls(client):
                response = client.get(url)
                response.raise_for_status()
                self._validate_url(str(response.url))
                content_type = response.headers.get("content-type", "").lower()
                if not any(value in content_type for value in ("text/", "application/xhtml+xml")):
                    self.skipped.append((url, len(response.content)))
                    continue
                content = response.content
                if self.max_bytes is not None and len(content) > self.max_bytes:
                    self.skipped.append((url, len(content)))
                    continue
                parsed = urlparse(url)
                basename = Path(parsed.path).name or "index.html"
                key = validate_object_key(
                    f"{parsed.netloc}/{hashlib.sha256(url.encode()).hexdigest()[:12]}-{basename}"
                )
                self._cache[key] = content
                self._url_by_key[key] = url
                yield SourceObject(key, hashlib.sha256(content).hexdigest(), len(content), None)

    def fetch(self, key: str) -> bytes:
        if key in self._cache:
            return self._cache[key]
        url = self._url_by_key.get(key)
        if not url:
            raise AppError("SOURCE_OBJECT_MISSING", "Web 资源不在本次发现结果中。", 409)
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.content

    def metadata(self, key: str) -> dict[str, object]:
        return {"source_url": self._url_by_key.get(key)}


class ReadOnlyDatabaseConnector:
    """固定 View 只读连接器；配置只允许标识符，不接受任意 SQL。"""

    def __init__(
        self, database_url_env: str, view: str, id_column: str, content_column: str,
        updated_column: str | None = None,
        metadata_mapping: dict[str, str] | None = None,
        acl_mapping: dict[str, str] | None = None,
    ):
        self.database_url = os.getenv(database_url_env, "")
        if not self.database_url:
            raise AppError("SOURCE_CREDENTIALS_MISSING", f"缺少环境变量 {database_url_env}。", 409)
        identifiers = [view, id_column, content_column, *( [updated_column] if updated_column else [])]
        if any(not value or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) for value in identifiers):
            raise AppError("SOURCE_CONFIGURATION_INVALID", "数据库 View 或列名格式无效。", 400)
        self.view, self.id_column, self.content_column = view, id_column, content_column
        self.updated_column = updated_column
        allowed_metadata = {"department", "sensitivity", "tags", "valid_from", "valid_to", "owner_user_id"}
        allowed_acl = {"allow_user_ids", "deny_user_ids"}
        self.metadata_mapping = metadata_mapping or {}
        self.acl_mapping = acl_mapping or {}
        if set(self.metadata_mapping) - allowed_metadata or set(self.acl_mapping) - allowed_acl:
            raise AppError(
                "SOURCE_MAPPING_FORBIDDEN",
                "字段映射包含不允许覆盖的系统治理字段。",
                400,
            )
        mapped_columns = [*self.metadata_mapping.values(), *self.acl_mapping.values()]
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) for value in mapped_columns):
            raise AppError("SOURCE_CONFIGURATION_INVALID", "Metadata 或 ACL 映射列名格式无效。", 400)
        self._cache: dict[str, bytes] = {}
        self._metadata: dict[str, dict[str, object]] = {}

    def list_objects(self) -> Iterator[SourceObject]:
        columns = [sql.Identifier(self.id_column), sql.Identifier(self.content_column)]
        if self.updated_column:
            columns.append(sql.Identifier(self.updated_column))
        for column in dict.fromkeys([*self.metadata_mapping.values(), *self.acl_mapping.values()]):
            if column not in {self.id_column, self.content_column, self.updated_column}:
                columns.append(sql.Identifier(column))
        query = sql.SQL("SELECT {columns} FROM {view} ORDER BY {id}").format(
            columns=sql.SQL(",").join(columns), view=sql.Identifier(self.view),
            id=sql.Identifier(self.id_column),
        )
        self._cache = {}
        self._metadata = {}
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '30s'")
            for row in connection.execute(query):
                key = validate_object_key(f"{row[self.id_column]}.txt")
                content = str(row[self.content_column] or "").encode("utf-8")
                self._cache[key] = content
                modified = row.get(self.updated_column) if self.updated_column else None
                metadata = {
                    target: row.get(column)
                    for target, column in self.metadata_mapping.items()
                    if row.get(column) is not None
                }
                if "tags" in metadata and not isinstance(metadata["tags"], list):
                    metadata["tags"] = [
                        value.strip() for value in str(metadata["tags"]).split(",") if value.strip()
                    ]
                for time_key in ("valid_from", "valid_to"):
                    value = metadata.get(time_key)
                    if hasattr(value, "isoformat"):
                        metadata[time_key] = value.isoformat()
                acl = {}
                for target, column in self.acl_mapping.items():
                    raw = row.get(column)
                    if raw is None:
                        continue
                    if isinstance(raw, (list, tuple)):
                        acl[target] = [str(value).strip() for value in raw if str(value).strip()]
                    else:
                        acl[target] = [value.strip() for value in str(raw).split(",") if value.strip()]
                self._metadata[key] = {
                    **metadata,
                    "external_updated_at": modified.isoformat() if hasattr(modified, "isoformat") else None,
                    **acl,
                }
                fingerprint = hashlib.sha256(
                    content + json.dumps({"metadata": metadata, "acl": acl}, sort_keys=True, default=str).encode()
                ).hexdigest()
                yield SourceObject(key, fingerprint, len(content), modified)

    def fetch(self, key: str) -> bytes:
        if key not in self._cache:
            raise AppError("SOURCE_OBJECT_MISSING", "数据库资源不在本次发现结果中。", 409)
        return self._cache[key]

    def metadata(self, key: str) -> dict[str, object]:
        return self._metadata.get(key, {})


def validate_object_key(key: str) -> str:
    """对象键必须是相对路径且不含上跳。

    键会被用作 ``documents.filename`` 并参与 ``document_id`` 计算，放过 ``..``
    或绝对路径会让不同对象互相覆盖。
    """

    if not key or key.startswith("/") or Path(key).is_absolute():
        raise AppError("SOURCE_OBJECT_KEY_INVALID", "对象键必须是相对路径。", 400)
    if ".." in Path(key).parts:
        raise AppError("SOURCE_OBJECT_KEY_INVALID", "对象键不得包含上跳路径。", 400)
    return key


class LocalDirectoryConnector:
    """把一个本地目录当作数据源。

    覆盖的真实场景：挂载的 NFS 共享、企业网盘的本地同步目录、定期落盘的导出文件。

    已知代价：``list_objects`` 要读完每个文件才能算出内容哈希，成本远高于 S3 那种
    「服务端在列举响应里直接给 ETag」。协议不为此增加「便宜的预检」方法——那会把 S3 的
    特性泄进抽象。同步框架每次同步只调用它一次，这个成本可以接受。
    """

    def __init__(
        self,
        root: Path,
        include_suffixes: tuple[str, ...],
        max_bytes: int | None = None,
    ):
        self.root = Path(root)
        self.include_suffixes = tuple(suffix.lower() for suffix in include_suffixes)
        self.max_bytes = max_bytes
        # 本轮列举中因超限被跳过的对象，供同步框架汇报。跳过不是失败：它们不入队、
        # 不软删、不影响熔断分母。
        self.skipped: list[tuple[str, int]] = []

    def list_objects(self) -> Iterator[SourceObject]:
        # 同一实例可能被多次列举，跳过清单不能累积。
        self.skipped = []
        if not self.root.is_dir():
            # 不能静默返回空清单：空清单会被差异计算判定为「全部删除」，
            # 那是把配置错误伪装成数据变更。
            raise AppError(
                "SOURCE_ROOT_UNAVAILABLE", f"数据源根目录不可用：{self.root}", 409
            )
        for path in sorted(self.root.rglob("*")):
            if path.is_symlink():
                # 跟随符号链接会引入目录环，也会让读取越出 root。
                continue
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in self.include_suffixes:
                continue
            stat = path.stat()
            key = path.relative_to(self.root).as_posix()
            if self.max_bytes is not None and stat.st_size > self.max_bytes:
                # 必须在 read_bytes 之前判定：否则大文件已经进内存了，再跳过也来不及。
                self.skipped.append((key, stat.st_size))
                continue
            content = path.read_bytes()
            yield SourceObject(
                key=validate_object_key(key),
                version=hashlib.sha256(content).hexdigest(),
                size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            )

    def fetch(self, key: str) -> bytes:
        resolved = (self.root / validate_object_key(key)).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise AppError("SOURCE_OBJECT_KEY_INVALID", "对象键越出数据源根目录。", 400)
        if not resolved.is_file():
            # 列举与拉取之间对象可能已被删除，这是可预期状态而非崩溃。
            raise AppError("SOURCE_OBJECT_MISSING", f"对象已不存在：{key}", 409)
        return resolved.read_bytes()

    def metadata(self, key: str) -> dict[str, object]:
        return {}


_S3_ERROR_CODES: dict[str, tuple[str, str, int]] = {
    "NoSuchBucket": ("SOURCE_ROOT_UNAVAILABLE", "数据源存储桶不存在或不可访问。", 409),
    "InvalidAccessKeyId": ("SOURCE_CREDENTIALS_INVALID", "数据源访问凭据无效。", 409),
    "SignatureDoesNotMatch": ("SOURCE_CREDENTIALS_INVALID", "数据源访问凭据无效。", 409),
    "AccessDenied": ("SOURCE_CREDENTIALS_INVALID", "数据源访问被拒绝。", 409),
    "NoSuchKey": ("SOURCE_OBJECT_MISSING", "对象已不存在。", 409),
}


def _map_s3_error(error: S3Error) -> AppError:
    """把 S3 错误映射为项目的稳定错误码。

    ``NoSuchBucket`` 复用 ``SOURCE_ROOT_UNAVAILABLE`` 是有意的：它与本地目录的根目录
    不存在同义，同步框架已经据此拒绝「把不可达当成全部删除」，S3 侧不该另造一套语义。
    未知错误统一为 ``SOURCE_UNAVAILABLE`` 并保留原始 code，便于运行手册按码处置。
    """

    code, message, status = _S3_ERROR_CODES.get(
        str(error.code), ("SOURCE_UNAVAILABLE", f"数据源不可访问：{error.code}", 502)
    )
    return AppError(code, message, status)


def _strip_prefix(object_name: str, prefix: str) -> str:
    return object_name[len(prefix):] if prefix and object_name.startswith(prefix) else object_name


class S3Connector:
    """把一个 S3 兼容存储桶的某个前缀当作数据源。

    ``version`` 用服务端返回的 ETag。minio SDK 已经剥离了引号（实测返回
    ``40820467919c684a8c89388304bcd584-3``），不需要自己处理。带 ``-N`` 后缀的是分段
    上传的复合校验值而非内容 MD5——``SourceObject`` 的契约收紧为单向保证正因为它。

    与本地目录的成本差异很大：这里一次列举 API 调用就带回全部 ETag 与 size，不需要读
    对象内容；本地目录要读完每个文件才能算出哈希。协议不为此增加「便宜的预检」方法，
    因为调用方每轮同步只列举一次。
    """

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        prefix: str,
        access_key: str,
        secret_key: str,
        *,
        region: str | None = None,
        secure: bool = True,
        max_bytes: int | None = None,
    ):
        self.bucket = bucket
        self.prefix = prefix or ""
        self.max_bytes = max_bytes
        self.skipped: list[tuple[str, int]] = []
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            secure=secure,
        )

    def list_objects(self) -> Iterator[SourceObject]:
        self.skipped = []
        try:
            for item in self._client.list_objects(
                self.bucket, prefix=self.prefix or None, recursive=True
            ):
                if item.is_dir:
                    continue
                key = validate_object_key(_strip_prefix(str(item.object_name), self.prefix))
                size = int(item.size or 0)
                if self.max_bytes is not None and size > self.max_bytes:
                    # 列举响应已带回 size，超限对象根本不需要下载。
                    self.skipped.append((key, size))
                    continue
                yield SourceObject(
                    key=key,
                    version=str(item.etag),
                    size=size,
                    modified_at=item.last_modified,
                )
        except S3Error as error:
            raise _map_s3_error(error) from error

    def fetch(self, key: str) -> bytes:
        object_name = f"{self.prefix}{validate_object_key(key)}"
        try:
            response = self._client.get_object(self.bucket, object_name)
        except S3Error as error:
            raise _map_s3_error(error) from error
        try:
            return response.read()
        finally:
            # get_object 返回的是 HTTP 响应而不是字节，不释放会泄漏连接池。
            response.close()
            response.release_conn()

    def metadata(self, key: str) -> dict[str, object]:
        return {"external_resource_id": key}
