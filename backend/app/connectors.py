"""数据源连接器协议与本地目录实现。

协议只有「列举」和「取内容」两个能力，没有「增量拉取」。这是拿 S3 与 GitHub 两种未来
实现压测后的结论：GitHub 有 tree diff 能直接返回增量，S3 与本地目录都没有变更流；若协议
提供 ``list_changes(cursor)``，后两者的实现只能退化成「全量列举后自己算差异」，那这个方法
就是在骗调用方。增量因此是框架层的能力（``data_source_sync``），对所有连接器一致。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, Protocol

from .errors import AppError


class SourceObject(NamedTuple):
    """数据源里的一个对象。

    ``version`` 的契约是「内容变了才变，内容没变就不变」。不同连接器用不同东西满足它：
    本地目录用内容 SHA-256，S3 用服务端给的 ETag。``modified_at`` 只进展示层，
    不参与任何判定——同内容重新落盘会刷新时间戳，内容改动也可能不改变 size。
    """

    key: str
    version: str
    size: int
    modified_at: datetime | None


class Connector(Protocol):
    def list_objects(self) -> Iterator[SourceObject]: ...
    def fetch(self, key: str) -> bytes: ...


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

    def __init__(self, root: Path, include_suffixes: tuple[str, ...]):
        self.root = Path(root)
        self.include_suffixes = tuple(suffix.lower() for suffix in include_suffixes)

    def list_objects(self) -> Iterator[SourceObject]:
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
            content = path.read_bytes()
            yield SourceObject(
                key=validate_object_key(path.relative_to(self.root).as_posix()),
                version=hashlib.sha256(content).hexdigest(),
                size=len(content),
                modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
            )

    def fetch(self, key: str) -> bytes:
        resolved = (self.root / validate_object_key(key)).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise AppError("SOURCE_OBJECT_KEY_INVALID", "对象键越出数据源根目录。", 400)
        if not resolved.is_file():
            # 列举与拉取之间对象可能已被删除，这是可预期状态而非崩溃。
            raise AppError("SOURCE_OBJECT_MISSING", f"对象已不存在：{key}", 409)
        return resolved.read_bytes()
