import json
from pathlib import Path

import pytest

from backend.app.knowledge_bases import (
    DEFAULT_KNOWLEDGE_BASE_ID,
    KnowledgeBaseRepository,
)


def test_registry_bootstraps_default_and_persists_crud(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    repository = KnowledgeBaseRepository(path)

    assert [item.knowledge_base_id for item in repository.list()] == [DEFAULT_KNOWLEDGE_BASE_ID]
    created = repository.create("研发资料", "团队文档")
    updated = repository.update(created.knowledge_base_id, "研发手册", "更新后的说明")

    assert updated is not None
    assert updated.created_at == created.created_at
    assert updated.updated_at >= created.updated_at
    assert KnowledgeBaseRepository(path).get(created.knowledge_base_id).name == "研发手册"
    assert repository.delete(created.knowledge_base_id) is True
    assert repository.get(created.knowledge_base_id) is None


def test_registry_rejects_duplicate_names_and_default_deletion(tmp_path: Path) -> None:
    repository = KnowledgeBaseRepository(tmp_path / "registry.json")
    repository.create("研发资料", "")

    with pytest.raises(ValueError, match="already exists"):
        repository.create("研发资料", "重复")
    with pytest.raises(ValueError, match="cannot be deleted"):
        repository.delete(DEFAULT_KNOWLEDGE_BASE_ID)


def test_registry_fails_closed_when_default_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"version": 1, "knowledge_bases": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="default knowledge base"):
        KnowledgeBaseRepository(path)
