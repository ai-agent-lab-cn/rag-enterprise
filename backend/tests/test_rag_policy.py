"""RAG 策略公开写契约。

PUT 只允许改「Web 开关 + 可信域名」；`rollout_stage` 与三个内部阈值由服务端保留。
这里全是纯内存用例，不加 `skipif`——策略合并不碰数据库，靠外部服务跳过等于没测。
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.audit import AuditRepository
from backend.app.modular_rag import DEFAULT_PIPELINE_PROFILES, RAGPolicy
from backend.app.rag_policy import (
    RAGPolicyFieldReadOnly,
    WebSearchProviderNotConfigured,
    merge_public_rag_policy_update,
)
from backend.app.schemas import RAGPolicyResponse, RAGPolicyUpdate

SEARXNG_URL = "http://127.0.0.1:8081"


def _current(**overrides) -> RAGPolicy:
    fields: dict[str, object] = {
        "rollout_stage": "canary",
        "web_search_enabled": False,
        "allowed_domains": (),
        "intent_confidence_threshold": 0.85,
        "minimum_evidence_count": 2,
        "max_web_results": 3,
    }
    fields.update(overrides)
    return RAGPolicy(**fields)


def test_public_update_only_changes_web_fields() -> None:
    current = RAGPolicy(
        rollout_stage="canary",
        web_search_enabled=False,
        allowed_domains=(),
        intent_confidence_threshold=0.85,
        minimum_evidence_count=2,
        max_web_results=3,
    )
    updated = merge_public_rag_policy_update(
        current,
        RAGPolicyUpdate(web_search_enabled=True, allowed_domains=["example.com"]),
        "http://127.0.0.1:8081",
    )
    assert updated.web_search_enabled is True
    assert updated.allowed_domains == ("example.com",)
    assert updated.rollout_stage == "canary"
    assert updated.intent_confidence_threshold == 0.85
    assert updated.minimum_evidence_count == 2
    assert updated.max_web_results == 3


def test_legacy_client_may_resubmit_identical_internal_values() -> None:
    """旧客户端「GET 回来原样 PUT 回去」不能被拒——值没变就不是修改。"""

    current = _current()

    updated = merge_public_rag_policy_update(
        current,
        RAGPolicyUpdate(
            web_search_enabled=True,
            allowed_domains=["example.com"],
            rollout_stage="canary",
            intent_confidence_threshold=0.85,
            minimum_evidence_count=2,
            max_web_results=3,
        ),
        SEARXNG_URL,
    )

    assert updated.web_search_enabled is True
    assert updated.rollout_stage == "canary"
    assert updated.intent_confidence_threshold == 0.85


@pytest.mark.parametrize(
    ("field", "value", "label"),
    [
        ("rollout_stage", "full", "发布阶段"),
        ("intent_confidence_threshold", 0.6, "意图置信度阈值"),
        ("minimum_evidence_count", 5, "最少证据数"),
        ("max_web_results", 1, "Web 结果上限"),
    ],
)
def test_changing_an_internal_field_is_rejected_as_read_only(
    field: str, value: object, label: str
) -> None:
    payload = RAGPolicyUpdate(
        web_search_enabled=True, allowed_domains=["example.com"], **{field: value}
    )

    with pytest.raises(RAGPolicyFieldReadOnly) as excinfo:
        merge_public_rag_policy_update(_current(), payload, SEARXNG_URL)

    # 禁用/拒绝都必须说得出是哪个字段，不能只回一句「有字段只读」。
    assert label in str(excinfo.value)


@pytest.mark.parametrize("base_url", ["", "   ", "searxng.internal:8081", "ftp://searxng.internal"])
def test_enabling_web_requires_a_configured_search_provider(base_url: str) -> None:
    payload = RAGPolicyUpdate(web_search_enabled=True, allowed_domains=["example.com"])

    with pytest.raises(WebSearchProviderNotConfigured, match="SEARXNG_BASE_URL"):
        merge_public_rag_policy_update(_current(), payload, base_url)


def test_domains_can_be_saved_before_web_is_enabled() -> None:
    """Provider 还没配也要能先把域名存下来——关着 Web 时不该拦。"""

    updated = merge_public_rag_policy_update(
        _current(),
        RAGPolicyUpdate(web_search_enabled=False, allowed_domains=["example.com"]),
        "",
    )

    assert updated.web_search_enabled is False
    assert updated.allowed_domains == ("example.com",)


def test_merge_keeps_profile_versions_stored_in_the_database() -> None:
    """合并式更新不再用代码里的 Profile 版本覆盖库里的值。"""

    current = _current(profile_versions={"fact_lookup": "7"})

    updated = merge_public_rag_policy_update(
        current,
        RAGPolicyUpdate(web_search_enabled=False, allowed_domains=[]),
        "",
    )

    assert updated.profile_versions == {"fact_lookup": "7"}


def test_request_model_normalizes_domains_and_requires_one_when_web_is_on() -> None:
    payload = RAGPolicyUpdate(web_search_enabled=True, allowed_domains=["Example.COM.", "example.com"])
    assert payload.allowed_domains == ["example.com"]

    with pytest.raises(ValidationError, match="至少配置一个可信域名"):
        RAGPolicyUpdate(web_search_enabled=True, allowed_domains=[])


def test_profile_versions_is_not_a_put_field() -> None:
    """`profile_versions` 从未是 PUT 字段。

    `extra="forbid"` 把它挡成 422，而不是 `RAG_POLICY_FIELD_READ_ONLY`——这是刻意接受的
    差异，钉在这里以免被后来人当成回归「修好」。
    """

    with pytest.raises(ValidationError, match="extra_forbidden"):
        RAGPolicyUpdate(
            web_search_enabled=False,
            allowed_domains=[],
            profile_versions={"fact_lookup": "1"},
        )


def test_response_keeps_internal_fields_non_null() -> None:
    """写契约收窄不能外溢到读契约：GET 响应的内部四字段仍是非空。"""

    snapshot = _current().snapshot()

    response = RAGPolicyResponse(knowledge_base_id="kb_default", **snapshot)
    assert response.rollout_stage == "canary"
    assert response.intent_confidence_threshold == 0.85
    assert response.minimum_evidence_count == 2
    assert response.max_web_results == 3

    with pytest.raises(ValidationError):
        RAGPolicyResponse(knowledge_base_id="kb_default", **{**snapshot, "rollout_stage": None})


def test_response_does_not_require_domains_for_a_legacy_web_enabled_record() -> None:
    """「启用 Web 必须有域名」是写侧准入；挂在读侧会让历史脏记录一读就 500。"""

    response = RAGPolicyResponse(
        knowledge_base_id="kb_default",
        **_current(web_search_enabled=True, allowed_domains=()).snapshot(),
    )

    assert response.web_search_enabled is True
    assert response.allowed_domains == []


def test_policy_audit_metadata_survives_the_audit_whitelist(tmp_path) -> None:
    """写了不等于读得到：白名单外的键在 record() 里被静默丢弃，不报错。"""

    metadata = {
        "web_search_enabled": True,
        "allowed_domain_count": 2,
        "web_search_enabled_changed": True,
        "allowed_domains_changed": False,
    }
    repository = AuditRepository(tmp_path / "events.json")

    repository.record(
        "knowledge_base.rag_policy.update",
        actor_id="usr_admin",
        actor_role="admin",
        resource_type="knowledge_base",
        resource_id="kb_default",
        result="success",
        metadata=metadata,
    )

    [event] = repository.list(offset=0, limit=10)
    assert event["metadata"] == metadata


def test_public_pipeline_profiles_only_expose_fact_lookup(client: TestClient) -> None:
    response = client.get("/api/rag/pipeline-profiles")

    assert response.status_code == 200
    assert [item["profile_id"] for item in response.json()] == ["fact_lookup_v1"]
    # 收口的只是公开响应：另外三个 Profile 仍要留在代码里供 Router 执行。
    assert set(DEFAULT_PIPELINE_PROFILES) == {
        "fact_lookup_v1",
        "summarize_v1",
        "compare_v1",
        "procedure_v1",
    }
