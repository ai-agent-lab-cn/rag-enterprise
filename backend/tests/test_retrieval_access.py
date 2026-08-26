from datetime import UTC, datetime

from backend.app.chunking import Chunk
from backend.app.retrieval_access import RetrievalAccessContext, can_retrieve_metadata
from backend.app.store import ChromaStore

USER = "usr_0123456789abcdef"
OTHER = "usr_fedcba9876543210"
NOW = datetime(2026, 8, 26, tzinfo=UTC)


def test_document_deny_has_priority_over_allow() -> None:
    metadata = {"allow_user_ids": [USER], "deny_user_ids": [USER]}
    assert can_retrieve_metadata(metadata, RetrievalAccessContext(USER), NOW) is False


def test_non_empty_allow_list_requires_current_user() -> None:
    metadata = {"allow_user_ids": [OTHER], "deny_user_ids": []}
    assert can_retrieve_metadata(metadata, RetrievalAccessContext(USER), NOW) is False
    assert can_retrieve_metadata(metadata, RetrievalAccessContext(OTHER), NOW) is True


def test_data_source_acl_is_enforced_after_document_acl() -> None:
    metadata = {
        "allow_user_ids": [],
        "deny_user_ids": [],
        "data_source_acl": {"allow_user_ids": [OTHER], "deny_user_ids": []},
    }
    assert can_retrieve_metadata(metadata, RetrievalAccessContext(USER), NOW) is False


def test_expired_or_deleted_document_is_never_retrievable() -> None:
    assert can_retrieve_metadata(
        {"retrieval_status": "deleted"}, RetrievalAccessContext(USER), NOW
    ) is False
    assert can_retrieve_metadata(
        {"retrieval_status": "searchable", "valid_to": "2026-08-25T00:00:00Z"},
        RetrievalAccessContext(USER),
        NOW,
    ) is False


def test_empty_acl_and_active_validity_inherit_knowledge_base_access() -> None:
    metadata = {
        "retrieval_status": "searchable",
        "valid_from": "2026-08-01T00:00:00Z",
        "valid_to": "2026-09-01T00:00:00Z",
        "allow_user_ids": [],
        "deny_user_ids": [],
    }
    assert can_retrieve_metadata(metadata, RetrievalAccessContext(USER), NOW) is True


def test_chroma_acl_update_takes_effect_on_next_query(tmp_path) -> None:
    store = ChromaStore(tmp_path / "chroma", "acl_boundary", "test-embedding")
    chunk = Chunk(
        chunk_id="doc_acl:chunk:00000",
        knowledge_base_id="kb_default",
        document_id="doc_acl",
        filename="acl.md",
        text="restricted content",
        page=None,
        paragraph=0,
        chunk_index=0,
        char_count=18,
        summary="restricted content",
        governance_metadata={"retrieval_status": "searchable", "acl_version": 1},
    )
    store.upsert([chunk], [[1.0, 0.0]])

    assert store.query([1.0, 0.0], 5, access=RetrievalAccessContext(USER))
    assert store.update_document_acl("doc_acl", [], [USER]) == 2
    assert store.query([1.0, 0.0], 5, access=RetrievalAccessContext(USER)) == []
