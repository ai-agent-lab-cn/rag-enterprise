"""数据源连接器的契约覆盖。

这些用例不需要数据库：连接器只回答「现在有什么」与「这个对象的内容是什么」，
增量判定是框架层的事（data_source_sync.py）。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.app.connectors import (
    LocalDirectoryConnector,
    S3Connector,
    SourceObject,
    validate_object_key,
)
from backend.app.errors import AppError

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "probe")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "probe12345")
requires_minio = pytest.mark.skipif(not MINIO_ENDPOINT, reason="需要 MinIO")


@pytest.fixture
def minio_bucket(request: pytest.FixtureRequest) -> Iterator[str]:
    """每个测试独立的桶，用完删掉。

    桶名用 sha256 而不是内置 ``hash()``：后者对 str 带 PYTHONHASHSEED 随机化，每次
    运行 pytest 都会换一批桶名，于是桶只增不减——实测在开发机上攒了九十多个。
    """

    from minio import Minio

    client = Minio(
        MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY, secure=False,
    )
    name = f"t-{hashlib.sha256(request.node.name.encode()).hexdigest()[:12]}"
    if not client.bucket_exists(name):
        client.make_bucket(name)
    for item in client.list_objects(name, recursive=True):
        client.remove_object(name, item.object_name)
    yield name
    for item in client.list_objects(name, recursive=True):
        client.remove_object(name, item.object_name)
    client.remove_bucket(name)


def _put(bucket: str, key: str, content: bytes, part_size: int = 0) -> None:
    import io

    from minio import Minio

    client = Minio(
        MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY, secure=False,
    )
    if part_size:
        client.put_object(bucket, key, io.BytesIO(content), len(content), part_size=part_size)
    else:
        client.put_object(bucket, key, io.BytesIO(content), len(content))


def _s3(bucket: str, prefix: str = "", **kwargs: object) -> S3Connector:
    return S3Connector(
        MINIO_ENDPOINT, bucket, prefix, MINIO_ACCESS_KEY, MINIO_SECRET_KEY,
        secure=False, **kwargs,
    )


def test_version_ignores_mtime_and_tracks_content(tmp_path: Path) -> None:
    """version 的契约是"内容变了才变"。

    mtime 会在同内容重新落盘时改变（rsync、网盘客户端重传、cp 都会），用它做判定
    会触发无谓的重新解析与重新 embedding。
    """

    target = tmp_path / "a.md"
    target.write_text("原始内容", encoding="utf-8")
    connector = LocalDirectoryConnector(tmp_path, (".md",))

    before = {item.key: item.version for item in connector.list_objects()}
    os.utime(target, (0, 0))
    after = {item.key: item.version for item in connector.list_objects()}
    assert after == before, "mtime 变化不得改变 version"

    target.write_text("改过的内容", encoding="utf-8")
    changed = {item.key: item.version for item in connector.list_objects()}
    assert changed["a.md"] != before["a.md"]


def test_same_size_different_content_still_changes_version(tmp_path: Path) -> None:
    """大小不变的编辑也必须被识别，这是不能用 (size, mtime) 做判定的另一半理由。"""

    target = tmp_path / "a.md"
    target.write_text("AAAA", encoding="utf-8")
    connector = LocalDirectoryConnector(tmp_path, (".md",))
    before = next(iter(connector.list_objects())).version

    target.write_text("BBBB", encoding="utf-8")

    assert next(iter(connector.list_objects())).version != before


def test_keys_are_relative_and_keep_subdirectories(tmp_path: Path) -> None:
    """键保留子目录，否则不同目录下的同名文件会被算成同一个文档。"""

    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.md").write_text("子目录", encoding="utf-8")
    (tmp_path / "a.md").write_text("根目录", encoding="utf-8")

    keys = sorted(item.key for item in LocalDirectoryConnector(tmp_path, (".md",)).list_objects())

    assert keys == ["a.md", "sub/a.md"]


def test_symlinks_hidden_files_and_other_suffixes_are_skipped(tmp_path: Path) -> None:
    """跟随符号链接会引入目录环，也会让读取越出根目录。"""

    (tmp_path / "keep.md").write_text("保留", encoding="utf-8")
    (tmp_path / "skip.log").write_text("后缀不匹配", encoding="utf-8")
    (tmp_path / ".hidden.md").write_text("隐藏文件", encoding="utf-8")
    outside = tmp_path.parent / "outside.md"
    outside.write_text("根目录之外", encoding="utf-8")
    (tmp_path / "link.md").symlink_to(outside)

    keys = [item.key for item in LocalDirectoryConnector(tmp_path, (".md",)).list_objects()]

    assert keys == ["keep.md"]


def test_suffix_matching_is_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "A.MD").write_text("大写后缀", encoding="utf-8")

    keys = [item.key for item in LocalDirectoryConnector(tmp_path, (".md",)).list_objects()]

    assert keys == ["A.MD"]


def test_missing_root_fails_loudly_instead_of_yielding_nothing(tmp_path: Path) -> None:
    """空清单会被差异计算判定为"全部删除"，那是把配置错误伪装成数据变更。"""

    connector = LocalDirectoryConnector(tmp_path / "not-there", (".md",))

    with pytest.raises(AppError) as error:
        list(connector.list_objects())

    assert error.value.code == "SOURCE_ROOT_UNAVAILABLE"


@pytest.mark.parametrize("key", ["../escape.md", "/etc/passwd", "sub/../../escape.md", ""])
def test_object_keys_reject_traversal_and_absolute_paths(key: str) -> None:
    with pytest.raises(AppError) as error:
        validate_object_key(key)

    assert error.value.code == "SOURCE_OBJECT_KEY_INVALID"


def test_fetch_returns_content_and_refuses_to_escape_root(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("正文", encoding="utf-8")
    connector = LocalDirectoryConnector(tmp_path, (".md",))

    assert connector.fetch("a.md") == "正文".encode()

    with pytest.raises(AppError) as error:
        connector.fetch("../escape.md")
    assert error.value.code == "SOURCE_OBJECT_KEY_INVALID"


def test_fetch_reports_missing_object(tmp_path: Path) -> None:
    """列举与拉取之间对象可能已被删除，这不是崩溃而是可预期的状态。"""

    connector = LocalDirectoryConnector(tmp_path, (".md",))

    with pytest.raises(AppError) as error:
        connector.fetch("gone.md")

    assert error.value.code == "SOURCE_OBJECT_MISSING"


def test_source_object_exposes_size_and_modified_at(tmp_path: Path) -> None:
    """modified_at 只进展示层，但必须有值可展示。"""

    (tmp_path / "a.md").write_text("12345", encoding="utf-8")

    item = next(iter(LocalDirectoryConnector(tmp_path, (".md",)).list_objects()))

    assert isinstance(item, SourceObject)
    assert item.size == 5
    assert item.modified_at is not None


def test_oversized_files_are_skipped_without_reading(tmp_path: Path) -> None:
    """超限文件必须在读取之前就被跳过。

    list_objects 要读全文件算哈希、fetch 把整个文件读进 bytes，一个大文件足以打死
    Worker。过滤必须发生在 read_bytes 之前，用 stat 的大小判断。
    """

    (tmp_path / "small.md").write_text("小文件", encoding="utf-8")
    (tmp_path / "huge.md").write_bytes(b"x" * 5000)
    connector = LocalDirectoryConnector(tmp_path, (".md",), max_bytes=1000)

    keys = [item.key for item in connector.list_objects()]

    assert keys == ["small.md"]
    assert connector.skipped == [("huge.md", 5000)]


def test_no_size_limit_when_max_bytes_is_none(tmp_path: Path) -> None:
    """不给上限时行为与改造前一致。"""

    (tmp_path / "huge.md").write_bytes(b"x" * 5000)

    connector = LocalDirectoryConnector(tmp_path, (".md",))

    assert [item.key for item in connector.list_objects()] == ["huge.md"]
    assert connector.skipped == []


def test_skipped_list_resets_between_listings(tmp_path: Path) -> None:
    """同一个连接器实例可能被多次列举，跳过清单不能累积。"""

    (tmp_path / "huge.md").write_bytes(b"x" * 5000)
    connector = LocalDirectoryConnector(tmp_path, (".md",), max_bytes=1000)

    list(connector.list_objects())
    list(connector.list_objects())

    assert connector.skipped == [("huge.md", 5000)]


def test_file_exactly_at_limit_is_kept(tmp_path: Path) -> None:
    """恰好等于上限不算超限——上限的语义是「超过」才拦。"""

    (tmp_path / "edge.md").write_bytes(b"x" * 1000)

    connector = LocalDirectoryConnector(tmp_path, (".md",), max_bytes=1000)

    assert [item.key for item in connector.list_objects()] == ["edge.md"]


@requires_minio
def test_s3_uses_etag_as_version_and_strips_prefix(minio_bucket: str) -> None:
    """ETag 直接用作 version；key 去掉 prefix 后保留子目录。"""

    _put(minio_bucket, "handbook/policy.md", b"policy content")
    _put(minio_bucket, "handbook/sub/onboarding.md", b"onboarding content")
    _put(minio_bucket, "other/ignored.md", b"not in prefix")

    objects = {item.key: item for item in _s3(minio_bucket, "handbook/").list_objects()}

    assert set(objects) == {"policy.md", "sub/onboarding.md"}
    assert objects["policy.md"].version
    assert objects["policy.md"].size == len(b"policy content")


@requires_minio
def test_s3_multipart_upload_changes_etag_for_identical_content(minio_bucket: str) -> None:
    """同一内容换 part_size 重传，ETag 变化。

    这是接受 ETag 作为 version 的已知代价，固化它是为了防止后人把这个行为当成 bug
    「修」掉。误判方向安全：多做一次索引，结果仍正确。
    """

    content = b"x" * (12 * 1024 * 1024)
    _put(minio_bucket, "big-a.md", content, part_size=5 * 1024 * 1024)
    _put(minio_bucket, "big-b.md", content, part_size=10 * 1024 * 1024)

    versions = {item.key: item.version for item in _s3(minio_bucket).list_objects()}

    assert versions["big-a.md"] != versions["big-b.md"], "分段大小不同必然导致 ETag 不同"
    assert versions["big-a.md"].endswith("-3"), "12MB 按 5MB 分段应产生 3 段"
    assert versions["big-b.md"].endswith("-2"), "12MB 按 10MB 分段应产生 2 段"
    # 「version 变化会触发重新索引」由同步层的改内容场景覆盖，两段合起来即完整链路；
    # 端到端再组合一次要上传十几 MB 并真索引几千个分块，成本不抵价值。


@requires_minio
def test_s3_skips_oversized_objects_without_downloading(minio_bucket: str) -> None:
    """超限对象在列举阶段被跳过，根本不下载——列举响应本来就带回 size。"""

    _put(minio_bucket, "small.md", b"small")
    _put(minio_bucket, "huge.md", b"x" * 5000)
    connector = _s3(minio_bucket, max_bytes=1000)

    keys = [item.key for item in connector.list_objects()]

    assert keys == ["small.md"]
    assert connector.skipped == [("huge.md", 5000)]


@requires_minio
def test_s3_fetch_returns_content_and_reports_missing(minio_bucket: str) -> None:
    _put(minio_bucket, "handbook/a.md", "正文".encode())
    connector = _s3(minio_bucket, "handbook/")

    assert connector.fetch("a.md") == "正文".encode()

    with pytest.raises(AppError) as error:
        connector.fetch("gone.md")
    assert error.value.code == "SOURCE_OBJECT_MISSING"


@requires_minio
def test_s3_missing_bucket_maps_to_root_unavailable() -> None:
    """桶不存在与本地目录的根目录不存在同义，绝不能退化成空清单。"""

    connector = _s3("bucket-that-does-not-exist")

    with pytest.raises(AppError) as error:
        list(connector.list_objects())

    assert error.value.code == "SOURCE_ROOT_UNAVAILABLE"


@requires_minio
def test_s3_bad_credentials_map_to_credentials_invalid(minio_bucket: str) -> None:
    """给错凭据与没给凭据要区分：一个是 INVALID，一个是 MISSING。"""

    connector = S3Connector(
        MINIO_ENDPOINT, minio_bucket, "", "wrong-key", "wrong-secret-value", secure=False
    )

    with pytest.raises(AppError) as error:
        list(connector.list_objects())

    assert error.value.code == "SOURCE_CREDENTIALS_INVALID"
