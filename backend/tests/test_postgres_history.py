import pytest

from backend.app.postgres_history import normalize_legacy_payload


def test_legacy_history_normalization_preserves_ids_and_computes_hash() -> None:
    normalized = normalize_legacy_payload(
        {
            "version": 1,
            "conversations": [
                {
                    "conversation_id": "conv_0123456789abcdef",
                    "knowledge_base_id": "kb_default",
                    "owner_id": "usr_0123456789abcdef",
                    "title": "问题",
                    "created_at": "2026-09-18T00:00:00+00:00",
                    "updated_at": "2026-09-18T00:00:00+00:00",
                }
            ],
            "answers": [
                {
                    "record_id": "answer_0123456789abcdef",
                    "conversation_id": "conv_0123456789abcdef",
                    "knowledge_base_id": "kb_default",
                    "question": "问题",
                    "status": "success",
                    "answer": "答案",
                    "sources": [],
                    "latency_ms": {"total": 1},
                    "models": {},
                    "model_metadata": {},
                    "created_at": "2026-09-18T00:00:00+00:00",
                }
            ],
        }
    )

    assert normalized.conversations[0]["conversation_id"] == "conv_0123456789abcdef"
    assert normalized.answers[0]["record_id"] == "answer_0123456789abcdef"
    assert len(normalized.sha256) == 64


def test_legacy_history_normalization_rejects_ownerless_conversations() -> None:
    with pytest.raises(ValueError, match="has no owner"):
        normalize_legacy_payload(
            {
                "version": 1,
                "conversations": [
                    {
                        "conversation_id": "conv_0123456789abcdef",
                        "knowledge_base_id": "kb_default",
                        "title": "无法确认归属的旧会话",
                    }
                ],
                "answers": [],
            }
        )
