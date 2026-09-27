import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.main import _SUCCESSFUL_ANSWER_STATUSES
from backend.app.postgres_history import (
    PostgresConversationRepository,
    _column_control_outcome,
    _column_intent,
    normalize_legacy_payload,
)
from backend.app.schemas import AnswerRecordResponse, QueryExecutionDetailResponse

# 门禁原因码的读取路径要对着**真的写进去的那份轨迹**验，不能对着手写夹具验：手写夹具只能
# 证明模型读得懂自己，证明不了服务层还在往 metrics 里写这些键。替身沿用闭环链那一套。
from backend.tests.test_retrieval_access import (
    CHAIN_FRESHNESS_QUESTION,
    _chain_service,
    _ChainReranker,
    _ChainStore,
    _ChainWebProvider,
    _kb_chunk,
    _web_enabled_policy,
)

MIGRATION_0041 = Path("backend/migrations/0041_modular_rag_runtime.sql")


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


def test_execution_intent_column_drops_greeting_to_null() -> None:
    # greeting 不在 0041 的 CHECK 里，又不许新增迁移，只能落 NULL
    # （CHECK 对 NULL 返回 UNKNOWN，视为通过）。真实意图留在 answer_records.routing。
    assert _column_intent("greeting") is None


def test_execution_control_outcome_column_drops_social_to_route() -> None:
    # control_outcome 是 NOT NULL，没有 NULL 逃逸，只能写允许集合里的占位值。
    assert _column_control_outcome("social") == "route"


@pytest.mark.parametrize("intent", ["fact_lookup", "summarize", "compare", "procedure"])
def test_execution_intent_column_passes_business_intents_through(intent: str) -> None:
    assert _column_intent(intent) == intent


@pytest.mark.parametrize("outcome", ["route", "clarify", "out_of_scope"])
def test_execution_control_outcome_column_passes_existing_outcomes_through(
    outcome: str,
) -> None:
    assert _column_control_outcome(outcome) == outcome


def test_execution_columns_keep_treating_missing_routing_as_before() -> None:
    # clarify / out_of_scope 今天就写 intent=NULL；routing 整个缺失时
    # control_outcome 仍要退回 'route'，与改造前的 `route.get(...) or "route"` 一致。
    assert _column_intent(None) is None
    assert _column_control_outcome(None) == "route"
    assert _column_control_outcome("") == "route"


def test_execution_column_mapping_stays_inside_the_migration_check_constraints() -> None:
    """映射失效不会报错，只会让历史执行详情显示错的 intent。

    这两条 CHECK 是映射函数存在的全部理由，而本轮不新增迁移放宽它们。把允许集合直接
    从迁移文件里读出来对照：谁改了映射函数、或者哪天有人动了 CHECK，这条立刻红。
    """
    sql = MIGRATION_0041.read_text(encoding="utf-8")

    allowed_intents = _check_values(sql, "intent")
    allowed_outcomes = _check_values(sql, "control_outcome")
    assert allowed_intents == {"fact_lookup", "summarize", "compare", "procedure"}
    assert allowed_outcomes == {"route", "clarify", "out_of_scope"}

    for value in [*allowed_intents, *allowed_outcomes, "greeting", "social", "", None]:
        mapped_intent = _column_intent(value)
        assert mapped_intent is None or mapped_intent in allowed_intents
        assert _column_control_outcome(value) in allowed_outcomes


def _check_values(sql: str, column: str) -> set[str]:
    matches = re.findall(rf"CHECK \({column} IN \(([^)]*)\)", sql, re.S)
    assert len(matches) == 1, f"迁移里 {column} 的 CHECK 不唯一"
    return {item.strip().strip("'") for item in matches[0].split(",") if item.strip()}


def test_answer_status_column_takes_new_values_without_a_migration() -> None:
    """answer_status 是裸 text，没有 CHECK——所以新状态不需要迁移就能落库。

    这也意味着写错的状态同样落得进去，写入侧不会报错；唯一的守门人是读取侧的 Literal
    （下面两条）。哪天有人给这一列加上 CHECK，answered_stale / direct_response 会在写入
    时抛 CheckViolation 并回滚整笔事务，这条会先红。
    """

    sql = MIGRATION_0041.read_text(encoding="utf-8")
    column_line = next(
        line.strip() for line in sql.splitlines() if line.strip().startswith("answer_status ")
    )

    assert column_line == "answer_status text,"


def _stored_answer_row(**overrides: object) -> dict[str, object]:
    """一行 answer_records（含 get_answer 里 LEFT JOIN 带出的两列）。"""

    row: dict[str, object] = {
        "record_id": "answer_0123456789abcdef",
        "conversation_id": "conv_0123456789abcdef",
        "knowledge_base_id": "kb_default",
        "execution_id": "qex_0123456789abcdef0123",
        "question": "索引版本的默认取值是多少",
        "status": "success",
        "answer": "知识库记载的默认索引版本是 v1[来源 1]。",
        "sources": [],
        "latency_ms": {"total": 1.0},
        "models": {},
        "model_metadata": {},
        "prompt_version": "v5-stream-grounded-governance-2",
        "prompt_hash": "0" * 64,
        "answer_status": "answered",
        "generation_governance": None,
        "query_metadata": None,
        "routing": None,
        "pipeline_profile": "fact_lookup_v1",
        "profile_version": "1",
        "module_summary": [],
        "bad_case_category": None,
        "error_code": None,
        "error_message": None,
        "created_at": datetime(2026, 9, 21, tzinfo=UTC),
        "policy_snapshot": {},
        "active_index_version_id": "iv_chain",
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("answer_status", ["answered_stale", "direct_response"])
def test_new_answer_statuses_survive_the_history_read_path(answer_status: str) -> None:
    """写得进去还不够，得读得回来。

    `AnswerRecordResponse.answer_status` 是 Literal 白名单，漏一个值不会有任何静态报错，
    只会在某个用户翻历史会话时 500——而那条记录本身是好的。
    """

    row = PostgresConversationRepository._answer(_stored_answer_row(answer_status=answer_status))

    record = AnswerRecordResponse(**row)

    assert record.answer_status == answer_status


def test_old_answer_records_read_back_with_compatible_defaults() -> None:
    """本轮之前落库的记录没有新字段，读取时用兼容默认，不回写历史数据。"""

    row = PostgresConversationRepository._answer(
        _stored_answer_row(
            answer_status=None,
            routing=None,
            generation_governance=None,
            query_metadata=None,
            sources=None,
            latency_ms=None,
            models=None,
            model_metadata=None,
            module_summary=None,
        )
    )

    record = AnswerRecordResponse(**row)

    assert record.answer_status is None
    assert record.routing is None
    assert record.sources == []
    assert record.module_summary == []
    assert record.latency_ms == {}


def _stored_execution_row(**overrides: object) -> dict[str, object]:
    """一行 query_executions，外加 get_execution 现拼的 routing 与 modules 两个键。"""

    row: dict[str, object] = {
        "execution_id": "qex_0123456789abcdef0123",
        "conversation_id": "conv_0123456789abcdef",
        "knowledge_base_id": "kb_default",
        "status": "succeeded",
        "routing": None,
        "pipeline_profile": "fact_lookup_v1",
        "profile_version": "1",
        "active_index_version_id": "iv_chain",
        "policy_snapshot": {},
        "total_latency_ms": 12.0,
        "modules": [],
        "created_at": datetime(2026, 9, 21, tzinfo=UTC),
    }
    row.update(overrides)
    return row


def test_gate_reason_codes_are_readable_in_the_query_execution_detail() -> None:
    """两段门禁与 Web 决策的原因码必须原样穿过持久化，落到执行详情与历史记录里。

    写入侧把整条轨迹 `model_dump(mode="json")` 之后塞进 `answer_records.module_summary`
    与 `module_executions.metrics`（main.py:3681、postgres_history.py:224-242）；读取侧
    两条路都要重新过 `ModuleExecutionResponse`。`metrics` 一旦被收窄成有限键的模型，
    reason_codes 会**静默消失**——写入测试照样全绿，只有技术抽屉那三行原因变成空白。
    这正是项目规则第三条说的「写入路径和读取路径必须成对验证」。
    """

    service = _chain_service(
        _ChainStore([_kb_chunk("a"), _kb_chunk("b")]),
        _ChainReranker(),
        policy=_web_enabled_policy(),
        web_provider=_ChainWebProvider(error=RuntimeError("searxng unreachable")),
    )

    response = service.query(CHAIN_FRESHNESS_QUESTION, retrieve_k=5, rerank_k=5)
    # 写入侧的原样载荷：main.py 就是这么把轨迹交给仓储的。
    persisted = [item.model_dump(mode="json") for item in response.module_executions]

    record = AnswerRecordResponse(
        **_stored_answer_row(
            answer_status=response.answer_status,
            execution_id=response.execution_id,
            module_summary=persisted,
        )
    )
    detail = QueryExecutionDetailResponse(
        **_stored_execution_row(execution_id=response.execution_id, modules=persisted)
    )

    for modules in (record.module_summary, detail.modules):
        metrics = {item.module_key: item.metrics for item in modules}
        assert metrics["evidence.preliminary_gate"]["reason_codes"] == ["freshness_required"]
        assert metrics["retrieval.web_policy"]["decision"] == "failed"
        assert metrics["retrieval.web_policy"]["reason_code"] == "web_retrieval_failed"
        assert metrics["evidence.final_gate"]["reason_codes"] == [
            "freshness_required",
            "freshness_unverified",
            "web_no_qualified_result",
        ]
        # 被跳过的模块也要读得回来：轨迹里少一条，抽屉就说不清"为什么没有 Web 证据"。
        assert metrics["evidence.fuse"]["reason_code"] == "web_retrieval_failed"


def test_successful_answer_statuses_cover_every_controlled_terminal_state() -> None:
    """这个集合决定会话记录、执行状态、bad_case_category 与在线 Bad Case 抓取四件事。

    answered_stale 与 direct_response 漏进去，每一条时效降级回答和每一次问候都会被记成
    failed 并自动建一条 Bad Case——测试全绿，只有运营看着坏例列表发懵。
    """

    assert "answered_stale" in _SUCCESSFUL_ANSWER_STATUSES
    assert "direct_response" in _SUCCESSFUL_ANSWER_STATUSES
    # 受控降级仍然是失败：它们没有给出可用答案，也说不清原因。
    assert _SUCCESSFUL_ANSWER_STATUSES.isdisjoint({"retrieval_only", "generation_failed"})
