import json
from pathlib import Path

import pytest

from backend.app.history import ConversationRepository

OWNER = "usr_0123456789abcdef"
OTHER = "usr_fedcba9876543210"


def test_history_persists_source_and_execution_metadata(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    repository = ConversationRepository(path)
    conversation = repository.resolve_conversation("kb_default", "第一个问题", None, OWNER)
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
        query_metadata={
            "strategy": "controlled_expansion",
            "query_count": 2,
            "expansion_count": 1,
            "fallback_used": False,
        },
    )

    restored = ConversationRepository(path)
    detail = restored.get_conversation("kb_default", conversation["conversation_id"], OWNER)

    assert detail["records"][0]["record_id"] == record["record_id"]
    assert detail["records"][0]["sources"][0]["text"] == "来源快照"
    assert detail["records"][0]["prompt_hash"] == "abc123"
    assert detail["records"][0]["query_metadata"]["query_count"] == 2
    assert restored.get_answer("kb_default", record["record_id"], OWNER)["answer"] == "有来源的答案"


def test_history_rejects_cross_knowledge_base_conversation(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "records.json")
    conversation = repository.resolve_conversation("kb_default", "默认库问题", None, OWNER)

    with pytest.raises(PermissionError, match="another knowledge base"):
        repository.resolve_conversation(
            "kb_team", "团队库问题", conversation["conversation_id"], OWNER
        )


def _seed(repository: ConversationRepository, owner: str, question: str) -> dict:
    conversation = repository.resolve_conversation("kb_default", question, None, owner)
    record = repository.record(
        conversation_id=conversation["conversation_id"],
        knowledge_base_id="kb_default",
        question=question,
        status="success",
        answer="答案正文",
        sources=[],
        latency_ms={"total": 1.0},
        models={"generation": "test-model"},
        model_metadata={"configured_model": "test-model"},
        prompt_version="v1",
        prompt_hash="abc123",
    )
    return {"conversation": conversation, "record": record}


def test_conversations_are_isolated_between_users_in_the_same_knowledge_base(
    tmp_path: Path,
) -> None:
    """知识库授权决定能否提问，不代表能读别人的问答正文。"""

    repository = ConversationRepository(tmp_path / "records.json")
    mine = _seed(repository, OWNER, "我的问题")
    theirs = _seed(repository, OTHER, "别人的问题")

    assert [item["conversation_id"] for item in repository.list_conversations("kb_default", OWNER)] == [
        mine["conversation"]["conversation_id"]
    ]
    assert (
        repository.get_conversation(
            "kb_default", theirs["conversation"]["conversation_id"], OWNER
        )
        is None
    )
    # 回答记录不带归属，必须回到会话判断，否则可凭 record_id 绕过隔离。
    assert repository.get_answer("kb_default", theirs["record"]["record_id"], OWNER) is None
    assert (
        repository.delete_conversation(
            "kb_default", theirs["conversation"]["conversation_id"], OWNER
        )
        is False
    )
    # 越权删除不得影响对方数据。
    assert repository.get_conversation(
        "kb_default", theirs["conversation"]["conversation_id"], OTHER
    ) is not None


def test_resolving_another_users_conversation_is_rejected(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "records.json")
    theirs = _seed(repository, OTHER, "别人的问题")

    with pytest.raises(PermissionError, match="another user"):
        repository.resolve_conversation(
            "kb_default", "追问", theirs["conversation"]["conversation_id"], OWNER
        )


def test_legacy_conversations_without_owner_are_hidden_from_everyone(tmp_path: Path) -> None:
    """V5 之前的记录没有归属，无法判断属于谁，因此不对任何人展示。"""

    path = tmp_path / "records.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "conversations": [
                    {
                        "conversation_id": "conv_00112233445566aa",
                        "knowledge_base_id": "kb_default",
                        "title": "历史会话",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
                "answers": [
                    {
                        "record_id": "answer_00112233445566bb",
                        "conversation_id": "conv_00112233445566aa",
                        "knowledge_base_id": "kb_default",
                        "question": "旧问题",
                        "status": "success",
                        "answer": "旧答案",
                        "sources": [],
                        "latency_ms": {"total": 1.0},
                        "models": {},
                        "model_metadata": {},
                        "prompt_version": None,
                        "prompt_hash": None,
                        "error_code": None,
                        "error_message": None,
                        "created_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    repository = ConversationRepository(path)

    assert repository.list_conversations("kb_default", OWNER) == []
    assert repository.get_conversation("kb_default", "conv_00112233445566aa", OWNER) is None
    assert repository.get_answer("kb_default", "answer_00112233445566bb", OWNER) is None
    # 但知识库是否为空的判定必须仍然看得见它，否则会留下孤儿记录。
    assert repository.count_conversations("kb_default") == 1


def test_failed_answer_is_included_in_summary(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "records.json")
    conversation = repository.resolve_conversation("kb_default", "失败问题", None, OWNER)
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
        bad_case_category="answer_generation_failed",
    )

    bad_cases = repository.list_bad_cases(
        "kb_default", OWNER, category="answer_generation_failed", error_code="MODEL_UNAVAILABLE"
    )
    assert [item["record_id"] for item in bad_cases] == [record["record_id"]]
    assert repository.list_bad_cases("kb_default", OTHER) == []
    summary = repository.list_conversations("kb_default", OWNER)[0]
    assert summary["turn_count"] == 1
    assert summary["last_status"] == "failed"
    assert (
        repository.delete_conversation("kb_default", conversation["conversation_id"], OWNER) is True
    )
    assert repository.get_answer("kb_default", record["record_id"], OWNER) is None
