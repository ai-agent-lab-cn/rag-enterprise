import pytest
from pydantic import ValidationError

from backend.app.schemas import DocumentMetadata, QueryRequest


def test_query_metadata_filter_normalizes_and_deduplicates_terms() -> None:
    payload = QueryRequest.model_validate(
        {
            "question": "部署规范是什么？",
            "filters": {
                "category_ids": ["cat_0123456789abcdef", "cat_0123456789abcdef"],
                "categories": [" 运维 ", "运维"],
                "tags": ["生产", " 生产 ", "RAG"],
                "source_types": ["file", "web"],
            },
        }
    )

    assert payload.filters is not None
    assert payload.filters.categories == ["运维"]
    assert payload.filters.category_ids == ["cat_0123456789abcdef"]
    assert payload.filters.tags == ["生产", "RAG"]
    assert payload.filters.source_types == ["file", "web"]


def test_query_metadata_filter_rejects_unknown_expression() -> None:
    with pytest.raises(ValidationError, match="sql"):
        QueryRequest.model_validate(
            {
                "question": "部署规范是什么？",
                "filters": {"sql": "1 = 1"},
            }
        )


def test_query_metadata_filter_rejects_invalid_category_id() -> None:
    with pytest.raises(ValidationError, match="分类 ID"):
        QueryRequest.model_validate(
            {"question": "部署规范是什么？", "filters": {"category_ids": ["invalid"]}}
        )


def test_query_metadata_filter_rejects_reverse_time_range() -> None:
    with pytest.raises(ValidationError, match="created_from"):
        QueryRequest.model_validate(
            {
                "question": "部署规范是什么？",
                "filters": {
                    "created_from": "2026-08-27T00:00:00Z",
                    "created_to": "2026-08-26T00:00:00Z",
                },
            }
        )


@pytest.mark.parametrize("field", ["categories", "tags"])
def test_query_metadata_filter_rejects_blank_terms(field: str) -> None:
    with pytest.raises(ValidationError, match="过滤值不能为空"):
        QueryRequest.model_validate(
            {"question": "部署规范是什么？", "filters": {field: ["  "]}}
        )


def test_document_governance_metadata_normalizes_acl_and_versions() -> None:
    metadata = DocumentMetadata.model_validate(
        {
            "category": " 安全 ",
            "tags": ["ACL", "ACL"],
            "source_system": " Confluence ",
            "external_resource_id": " page-42 ",
            "owner_user_id": "usr_0123456789abcdef",
            "department": " 研发部 ",
            "sensitivity": "confidential",
            "valid_from": "2026-08-01T00:00:00Z",
            "valid_to": "2026-09-01T00:00:00Z",
            "acl_version": 3,
            "allow_user_ids": ["usr_0123456789abcdef", "usr_0123456789abcdef"],
        }
    )

    assert metadata.category == "安全"
    assert metadata.tags == ["ACL"]
    assert metadata.source_system == "Confluence"
    assert metadata.department == "研发部"
    assert metadata.allow_user_ids == ["usr_0123456789abcdef"]
    assert metadata.acl_version == 3


def test_document_governance_metadata_rejects_conflicting_acl() -> None:
    with pytest.raises(ValidationError, match="Allow 与 Deny"):
        DocumentMetadata.model_validate(
            {
                "allow_user_ids": ["usr_0123456789abcdef"],
                "deny_user_ids": ["usr_0123456789abcdef"],
            }
        )


def test_document_governance_metadata_rejects_reverse_validity() -> None:
    with pytest.raises(ValidationError, match="valid_from"):
        DocumentMetadata.model_validate(
            {
                "valid_from": "2026-09-01T00:00:00Z",
                "valid_to": "2026-08-01T00:00:00Z",
            }
        )
