from pathlib import Path

import pytest

from backend.app.history import ConversationRepository


def test_history_persists_source_and_execution_metadata(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    repository = ConversationRepository(path)
    conversation = repository.resolve_conversation("kb_default", "第一个问题", None)
    record = repository.record(
        conversation_id=conversation["conversation_id"],
        knowledge_base_id="kb_default",
        question="第一个问题",
        status="success",
        answer="有来源的答案",
        sources=[{"chunk_id": "chunk_1", "text": "来源快照"}],
        latency_ms={"total": 12.5},
        models={"generation": "test-model"},
        model_metadata={"configured_model": "test-model"},
        prompt_version="v1",
        prompt_hash="abc123",
    )

    restored = ConversationRepository(path)
    detail = restored.get_conversation("kb_default", conversation["conversation_id"])

    assert detail["records"][0]["record_id"] == record["record_id"]
    assert detail["records"][0]["sources"][0]["text"] == "来源快照"
    assert detail["records"][0]["prompt_hash"] == "abc123"
    assert restored.get_answer("kb_default", record["record_id"])["answer"] == "有来源的答案"


def test_history_rejects_cross_knowledge_base_conversation(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "records.json")
    conversation = repository.resolve_conversation("kb_default", "默认库问题", None)

    with pytest.raises(PermissionError, match="another knowledge base"):
        repository.resolve_conversation("kb_team", "团队库问题", conversation["conversation_id"])


def test_failed_answer_is_included_in_summary(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "records.json")
    conversation = repository.resolve_conversation("kb_default", "失败问题", None)
    record = repository.record(
        conversation_id=conversation["conversation_id"],
        knowledge_base_id="kb_default",
        question="失败问题",
        status="failed",
        answer=None,
        sources=[],
        latency_ms={"total": 3.0},
        models={"generation": "test-model"},
        model_metadata={"configured_model": "test-model"},
        prompt_version=None,
        prompt_hash=None,
        error_code="MODEL_UNAVAILABLE",
        error_message="模型不可用",
    )

    summary = repository.list_conversations("kb_default")[0]
    assert summary["turn_count"] == 1
    assert summary["last_status"] == "failed"
    assert repository.delete_conversation("kb_default", conversation["conversation_id"]) is True
    assert repository.get_answer("kb_default", record["record_id"]) is None
