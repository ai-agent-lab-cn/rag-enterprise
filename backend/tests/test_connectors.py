"""数据源连接器的契约覆盖。

这些用例不需要数据库：连接器只回答「现在有什么」与「这个对象的内容是什么」，
增量判定是框架层的事（data_source_sync.py）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.connectors import (
    LocalDirectoryConnector,
    SourceObject,
    validate_object_key,
)
from backend.app.errors import AppError


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
