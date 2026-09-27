import time
from typing import get_args

import pytest

from backend.app.config import Settings
from backend.app.modular_rag import (
    DEFAULT_MODULE_REGISTRY,
    DEFAULT_PIPELINE_PROFILES,
    SOCIAL_DIRECT_REPLY,
    ExecutionTrace,
    ModuleDefinition,
    ModuleRegistry,
    QueryIntentRouter,
    RAGPolicy,
)
from backend.app.prompts import PROMPT_VERSION, AnswerStatus, build_prompt
from backend.app.schemas import AnswerRecordResponse, QueryMetadataFilter, QueryResponse
from backend.app.service import RAGService
from backend.evaluation.intent_routing import evaluate_intent_router

# 闭环链的替身全部复用 test_retrieval_access 那一套，不在这里再造一份——两份 Fake 迟早
# 分叉，而分叉的那一份会安静地测一条生产里不存在的链路。仓库里已有同样的跨测试导入：
# test_document_snapshots 与 test_index_evaluation_runs 都从 test_index_versions 取。
from backend.tests.test_retrieval_access import (
    CHAIN_FRESHNESS_QUESTION,
    CHAIN_MODULE_SEQUENCE,
    CHAIN_QUESTION,
    _ChainPolicies,
    _ChainReranker,
    _ChainStore,
    _ChainWebProvider,
    _FakeEmbedder,
    _kb_chunk,
    _module,
    _web_enabled_policy,
    _web_result,
)


class _Classifier:
    model_name = "classifier-test"

    def __init__(self, payload: str):
        self.payload = payload
        self.calls = 0

    def generate(self, prompt: str):
        self.calls += 1
        assert "只输出 JSON" in prompt
        return self.payload, {"provider": "test"}


# 设计稿 3.2 的问候全表。参数化必须照抄整张表：只挑 brief 里那六个会漏掉
# 哈喽 / Hello / 早上好 / 下午好 / 晚上好 / 有人吗 / 能听到吗。
GREETINGS = (
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "Hello",
    "Hi",
    "早上好",
    "下午好",
    "晚上好",
    "在吗",
    "有人吗",
    "能听到吗",
    "方便吗",
)


def test_router_uses_deterministic_high_confidence_rules() -> None:
    router = QueryIntentRouter()

    assert router.route("请总结这份制度").intent == "summarize"
    assert router.route("比较方案 A 和方案 B 的区别").intent == "compare"
    assert router.route("如何完成索引回滚，步骤是什么").intent == "procedure"
    assert router.route("合同编号是什么？").intent == "fact_lookup"
    assert router.route("配置指纹包含哪些字段？").intent == "fact_lookup"
    assert router.route("当前生效的索引版本是哪一个？").requires_freshness is False


@pytest.mark.parametrize("question", GREETINGS)
def test_router_sends_pure_greetings_to_social(question: str) -> None:
    decision = QueryIntentRouter().route(question)

    assert decision.intent == "greeting"
    assert decision.control_outcome == "social"
    assert decision.pipeline_profile is None
    assert decision.profile_version is None
    assert decision.requires_freshness is False


@pytest.mark.parametrize("question", ["你好！", "  在吗？  ", "嗨~", "你好，你好"])
def test_router_tolerates_punctuation_and_whitespace_around_greetings(question: str) -> None:
    assert QueryIntentRouter().route(question).control_outcome == "social"


def test_greeting_is_decided_before_the_short_question_guard() -> None:
    # 「嗨」1 字、「你好」2 字、「方便吗」3 字，全部短于 route() 的 len<4 闸。
    # 这条用例是 R7 的回归保护：判定一旦挪到闸后面，问候会重新变成 clarify。
    router = QueryIntentRouter()

    assert router.route("嗨").control_outcome == "social"
    assert router.route("你好").control_outcome == "social"
    assert router.route("方便吗").control_outcome == "social"
    # 同样短、但不是问候的句子保持原行为。
    assert router.route("嗯").control_outcome == "clarify"
    assert router.route("为什么").control_outcome == "clarify"


def test_greeting_does_not_reach_the_structured_classifier() -> None:
    classifier = _Classifier(
        '{"intent":"fact_lookup","confidence":0.99,"reason":"x","requires_web":false}'
    )

    decision = QueryIntentRouter(classifier).route("你好")

    assert decision.intent == "greeting"
    assert decision.classifier_model is None
    assert classifier.calls == 0


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("你好，索引版本是什么？", "fact_lookup"),
        ("你好，怎么回滚索引", "procedure"),
        ("在吗，帮我总结这份制度", "summarize"),
        ("嗨，比较 A 和 B 的区别", "compare"),
    ],
)
def test_business_question_wins_over_greeting_prefix(question: str, intent: str) -> None:
    decision = QueryIntentRouter().route(question)

    assert decision.intent == intent
    assert decision.control_outcome == "route"
    assert decision.pipeline_profile == f"{intent}_v1"


@pytest.mark.parametrize(
    "question",
    [
        "最新的索引版本是哪一个？",
        "今天的同步任务跑了几次？",
        "实时索引状态在哪里查看？",
        "截至目前一共导入了多少文档？",
    ],
)
def test_router_marks_time_sensitive_questions_as_requiring_freshness(question: str) -> None:
    decision = QueryIntentRouter().route(question)

    assert decision.requires_freshness is True
    # 过渡别名必须跟着真值走，否则旧前端与 query_executions.requires_web 列会读到 false。
    assert decision.as_dict()["requires_web"] is True


def test_routing_payload_keeps_requires_web_as_a_transitional_alias() -> None:
    payload = QueryIntentRouter().route("你好").as_dict()

    assert payload["requires_freshness"] is False
    assert payload["requires_web"] is False
    assert payload["intent"] == "greeting"
    assert payload["control_outcome"] == "social"
    assert set(payload) == {
        "intent",
        "confidence",
        "reason",
        "control_outcome",
        "pipeline_profile",
        "profile_version",
        "original_question",
        "effective_question",
        "follow_up_rewritten",
        "requires_freshness",
        "classifier_model",
        "fallback_used",
        "requires_web",
    }


def test_router_uses_structured_classifier_when_rules_do_not_match() -> None:
    router = QueryIntentRouter(
        _Classifier(
            '{"intent":"fact_lookup","confidence":0.91,'
            '"reason":"询问一个明确事实","requires_web":false}'
        )
    )

    decision = router.route("RRF 常数的当前值")

    assert decision.intent == "fact_lookup"
    assert decision.confidence == 0.91
    assert decision.classifier_model == "classifier-test"


def test_router_returns_clarification_below_confidence_threshold() -> None:
    router = QueryIntentRouter(
        _Classifier(
            '{"intent":"compare","confidence":0.42,'
            '"reason":"缺少比较对象","requires_web":false}'
        )
    )

    decision = router.route("请分析该方案")

    assert decision.control_outcome == "clarify"
    assert decision.pipeline_profile is None


def test_router_marks_out_of_scope_without_selecting_pipeline() -> None:
    router = QueryIntentRouter(
        _Classifier(
            '{"intent":"out_of_scope","confidence":0.97,'
            '"reason":"超出受控知识范围","requires_web":false}'
        )
    )

    decision = router.route("帮我预言明天的彩票号码")

    assert decision.control_outcome == "out_of_scope"
    assert decision.pipeline_profile is None


def test_registry_rejects_unregistered_or_incompatible_profile_modules() -> None:
    registry = ModuleRegistry()
    registry.register(ModuleDefinition("query.normalize", "1", "query_transformation"))

    errors = registry.validate_profile(DEFAULT_PIPELINE_PROFILES["fact_lookup_v1"])

    assert any("未注册" in item for item in errors)


def test_fact_lookup_profile_declares_the_closed_loop_sequence() -> None:
    profile = DEFAULT_PIPELINE_PROFILES["fact_lookup_v1"]

    assert profile.modules == (
        "query.normalize",
        "query.expand",
        "retrieval.knowledge_base",
        "rerank.knowledge_base",
        "evidence.preliminary_gate",
        "retrieval.web_policy",
        "evidence.fuse",
        "rerank.unified",
        "evidence.final_gate",
        "generation.fact",
        "generation.verify",
    )
    # 版本号不能跟着改链一起涨：RAG Policy 的 PUT 改成合并写之后，库里的 profile_versions
    # 不再被重算，而 canary/full 阶段版本不一致会直接抛 RAG_PROFILE_INCOMPATIBLE，
    # 已晋级的知识库会当场全部查询失败，且没有任何接口能刷新旧值。
    assert profile.version == "1"


def test_default_registry_registers_the_closed_loop_modules_and_keeps_evidence_gate() -> None:
    # 注册是增量的：新增四个模块，但 evidence.gate 仍被另外三个 Profile 使用，不能删。
    for profile in DEFAULT_PIPELINE_PROFILES.values():
        assert DEFAULT_MODULE_REGISTRY.validate_profile(profile) == []

    for module_key in (
        "rerank.knowledge_base",
        "rerank.unified",
        "evidence.preliminary_gate",
        "evidence.final_gate",
        "evidence.gate",
    ):
        assert DEFAULT_MODULE_REGISTRY.get(module_key).module_key == module_key


def test_four_profiles_are_versioned_and_have_generation_verification() -> None:
    assert set(DEFAULT_PIPELINE_PROFILES) == {
        "fact_lookup_v1",
        "summarize_v1",
        "compare_v1",
        "procedure_v1",
    }
    assert all(
        profile.modules[-1] == "generation.verify"
        for profile in DEFAULT_PIPELINE_PROFILES.values()
    )


def test_module_trace_uses_epoch_timestamps_and_stable_hashes() -> None:
    trace = ExecutionTrace("qex_0123456789abcdef0123")
    started = time.perf_counter()
    trace.record("query.normalize", "1", started, {"q": "问题"}, {"q": "问题"})
    item = trace.as_list()[0]

    assert item["started_at"] > 1_000_000_000
    assert item["finished_at"] >= item["started_at"]
    assert len(item["input_hash"]) == 64
    assert len(item["output_hash"]) == 64


def test_intent_evaluation_reports_per_intent_metrics() -> None:
    result = evaluate_intent_router(
        QueryIntentRouter(),
        [
            {"sample_id": "1", "question": "RRF 是什么？", "expected": "fact_lookup"},
            {"sample_id": "2", "question": "总结这些资料", "expected": "summarize"},
            {"sample_id": "3", "question": "比较 A 和 B", "expected": "compare"},
            {"sample_id": "4", "question": "如何执行回滚？", "expected": "procedure"},
            {"sample_id": "5", "question": "你好", "expected": "greeting"},
        ],
    )

    assert result.macro_f1 == 1
    assert set(result.per_intent_f1) == {
        "greeting",
        "fact_lookup",
        "summarize",
        "compare",
        "procedure",
    }


# --------------------------------------------------------------------------------------
# 设计稿 7.2 结果矩阵：Final Gate 的 pass / stale / reject 最终落成哪个 answer_status。
#
# 这组用例不连数据库，全在内存里跑。替身复用 test_retrieval_access 的闭环链 Fake，只补一个
# 真会产出合规答案的 Generator——那边的 _ChainGenerator 是 ready=False，永远回
# retrieval_only，区分不了 answered 与 answered_stale。
# --------------------------------------------------------------------------------------

# 一句话一个引用，刚好满足 parse_answer 的"每个 >= 4 字的句子都要带来源"。
GROUNDED_ANSWER = "[STATUS: ANSWERED]\n知识库记载的默认索引版本是 v1[来源 1]。"
GROUNDED_ANSWER_BODY = "知识库记载的默认索引版本是 v1[来源 1]。"
# 时效声明独立成句的写法。与 test_prompts.py 的同名常量一致，两边测的是同一种模型输出：
# 那边测 parse_answer 的豁免本身，这里测它有没有被服务层真正接上。
STALE_NOTICE_AS_ITS_OWN_SENTENCE = (
    "[STATUS: ANSWERED]\n时效未验证。知识库记载的默认索引版本是 v1[来源 1]。"
)
# 回答 Prompt 的开头。Router 把同一个 generator 当结构化分类器用，两类调用必须分开数，
# 否则"拒答时生成模型零调用"这条断言会被分类器那一次调用弄成假阳性。
ANSWER_PROMPT_MARKER = "RongRAG Studio 的知识助手"


class _MatrixGenerator:
    model_name = "matrix/generator"
    provider_name = "fake"
    ready = True

    def __init__(self, raw_answer: str = GROUNDED_ANSWER):
        self.raw_answer = raw_answer
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> tuple[str, dict[str, object]]:
        self.prompts.append(prompt)
        return self.raw_answer, {"configured_model": self.model_name}

    @property
    def answer_prompts(self) -> list[str]:
        return [item for item in self.prompts if ANSWER_PROMPT_MARKER in item]


def _matrix_service(
    store: _ChainStore,
    *,
    policy: RAGPolicy,
    generator: _MatrixGenerator,
    reranker: _ChainReranker | None = None,
    web_provider: _ChainWebProvider | None = None,
) -> RAGService:
    return RAGService(
        Settings(),
        store,
        _FakeEmbedder(),
        reranker or _ChainReranker(),
        generator,
        policy_repository=_ChainPolicies(policy),
        web_provider=web_provider,
    )


def test_normal_question_with_sufficient_knowledge_base_is_answered() -> None:
    """矩阵第 1 行：普通问题 + KB 通过 + Web 未触发 = answered。"""

    store = _ChainStore([_kb_chunk("a"), _kb_chunk("b")])
    provider = _ChainWebProvider(results=(_web_result(),))
    generator = _MatrixGenerator()
    service = _matrix_service(
        store, policy=_web_enabled_policy(), generator=generator, web_provider=provider
    )

    response = service.query(CHAIN_QUESTION, retrieve_k=5, rerank_k=5)

    assert response.answer_status == "answered"
    assert response.answer == GROUNDED_ANSWER_BODY
    assert provider.calls == 0
    assert len(generator.answer_prompts) == 1
    # 正常路径的提示词必须逐字不变，否则 prompt_hash 变了、历史答案不可复现。
    assert "时效约束" not in generator.answer_prompts[0]
    assert response.prompt_version == PROMPT_VERSION


def test_normal_question_is_answered_after_one_web_supplement() -> None:
    """矩阵第 2 行：普通问题 + KB 不足 + 补检后满足且有 KB anchor = answered。"""

    store = _ChainStore([_kb_chunk("a")])
    provider = _ChainWebProvider(results=(_web_result(),))
    generator = _MatrixGenerator()
    service = _matrix_service(
        store,
        policy=_web_enabled_policy(minimum_evidence_count=2),
        generator=generator,
        web_provider=provider,
    )

    response = service.query(CHAIN_QUESTION, retrieve_k=5, rerank_k=5)

    assert provider.calls == 1
    assert response.answer_status == "answered", "补检补满了就是普通 answered，不是时效降级"
    assert {item.evidence_source_type for item in response.sources} == {"knowledge_base", "web"}
    assert "时效约束" not in generator.answer_prompts[0]


def test_normal_question_without_web_is_insufficient_and_never_calls_the_generator() -> None:
    """矩阵第 3 行：普通问题 + KB 不足 + Web 关闭 = insufficient_evidence，且不生成。"""

    store = _ChainStore([_kb_chunk("a")])
    generator = _MatrixGenerator()
    service = _matrix_service(
        store, policy=RAGPolicy(minimum_evidence_count=2), generator=generator
    )

    response = service.query(CHAIN_QUESTION, retrieve_k=5, rerank_k=5)

    assert response.answer_status == "insufficient_evidence"
    assert response.sources == [], "拒答不展示没通过门禁的候选"
    assert generator.answer_prompts == [], "reject 是固定拒答，不得调用生成模型"
    final = _module(response, "evidence.final_gate")
    assert final.metrics["outcome"] == "reject"
    assert "evidence_below_minimum" in final.metrics["reason_codes"]
    assert response.generation_governance is not None
    assert response.generation_governance.outcome_reason == "INSUFFICIENT_EVIDENCE"


def test_freshness_question_with_qualified_web_evidence_is_answered() -> None:
    """矩阵第 4 行：时效问题 + KB 历史证据 + 合格 Web 证据 = answered（不降级）。"""

    store = _ChainStore([_kb_chunk("a"), _kb_chunk("b")])
    provider = _ChainWebProvider(results=(_web_result(),))
    generator = _MatrixGenerator()
    service = _matrix_service(
        store, policy=_web_enabled_policy(), generator=generator, web_provider=provider
    )

    response = service.query(CHAIN_FRESHNESS_QUESTION, retrieve_k=5, rerank_k=5)

    assert provider.calls == 1
    assert response.answer_status == "answered"
    final = _module(response, "evidence.final_gate")
    assert final.metrics["outcome"] == "pass"
    assert final.metrics["freshness_verified"] is True
    assert "时效约束" not in generator.answer_prompts[0], "验证过时效的回答不加降级约束"


@pytest.mark.parametrize("web_state", ["disabled", "failed", "no_result"])
def test_freshness_question_without_qualified_web_evidence_is_answered_stale(
    web_state: str,
) -> None:
    """矩阵第 5 行：时效问题 + KB 历史证据 + Web 关闭/失败/无结果 = answered_stale。

    三种 Web 状态都必须落到同一个终态：用户看到的是"答了但时效未验证"，而不是拒答，
    也不是一句看不出风险的普通 answered。
    """

    store = _ChainStore([_kb_chunk("a"), _kb_chunk("b")])
    generator = _MatrixGenerator()
    policy = RAGPolicy()
    provider: _ChainWebProvider | None = None
    if web_state == "failed":
        policy = _web_enabled_policy()
        provider = _ChainWebProvider(error=RuntimeError("searxng unreachable"))
    elif web_state == "no_result":
        policy = _web_enabled_policy()
        provider = _ChainWebProvider(results=())

    service = _matrix_service(
        store, policy=policy, generator=generator, web_provider=provider
    )

    response = service.query(CHAIN_FRESHNESS_QUESTION, retrieve_k=5, rerank_k=5)

    assert response.answer_status == "answered_stale"
    assert response.answer == GROUNDED_ANSWER_BODY, "时效降级仍然要给出答案"
    assert [item.chunk_id for item in response.sources] == ["a", "b"]
    final = _module(response, "evidence.final_gate")
    assert final.metrics["outcome"] == "stale"
    assert final.metrics["freshness_verified"] is False
    assert "freshness_unverified" in final.metrics["reason_codes"]
    # 生成约束只加在这条分支上，而且 PROMPT_VERSION 不跟着涨。
    assert "时效约束" in generator.answer_prompts[0]
    assert "时效未验证" in generator.answer_prompts[0]
    assert response.prompt_version == PROMPT_VERSION
    assert response.generation_governance is not None
    assert response.generation_governance.outcome_reason == "answered_stale"


def test_stale_answer_survives_a_freshness_notice_written_as_its_own_sentence() -> None:
    """端到端确认引用校验的时效豁免真的接上了服务层。

    `_MatrixGenerator` 默认那句是提示词推荐的"同句"写法，天然绕开了这个风险；真实模型
    完全可能把「时效未验证。」写成独立一句，而那 6 个字在豁免之前会被判成没有来源的事实
    声明，整条时效降级回答变成 generation_failed——矩阵第 5 行就成了概率事件。
    """

    store = _ChainStore([_kb_chunk("a"), _kb_chunk("b")])
    generator = _MatrixGenerator(STALE_NOTICE_AS_ITS_OWN_SENTENCE)
    service = _matrix_service(store, policy=RAGPolicy(), generator=generator)

    response = service.query(CHAIN_FRESHNESS_QUESTION, retrieve_k=5, rerank_k=5)

    assert response.answer_status == "answered_stale"
    assert response.answer.startswith("时效未验证。")
    assert response.generation_governance is not None
    assert response.generation_governance.claim_citation_coverage is True


def test_the_same_notice_still_fails_outside_the_stale_branch() -> None:
    """豁免按请求算，不是常开：普通问题写成同样的两句话仍然是引用缺失。"""

    store = _ChainStore([_kb_chunk("a"), _kb_chunk("b")])
    generator = _MatrixGenerator(STALE_NOTICE_AS_ITS_OWN_SENTENCE)
    service = _matrix_service(store, policy=RAGPolicy(), generator=generator)

    response = service.query(CHAIN_QUESTION, retrieve_k=5, rerank_k=5)

    assert response.answer_status == "generation_failed"
    assert response.error_code == "CLAIM_CITATION_MISSING"


def test_freshness_question_without_knowledge_base_anchor_is_rejected() -> None:
    """矩阵第 6 行：时效问题 + 无合格 KB 证据 = 拒答，Web 搜到了也不算数。"""

    store = _ChainStore([_kb_chunk("a")])
    reranker = _ChainReranker(scores={"知识库证据 a": -1.0})
    provider = _ChainWebProvider(results=(_web_result(),))
    generator = _MatrixGenerator()
    service = _matrix_service(
        store,
        policy=_web_enabled_policy(),
        generator=generator,
        reranker=reranker,
        web_provider=provider,
    )

    response = service.query(CHAIN_FRESHNESS_QUESTION, retrieve_k=5, rerank_k=5)

    assert provider.calls == 1, "时效问题仍然试一次 Web"
    assert response.answer_status == "insufficient_evidence"
    assert response.sources == []
    assert generator.answer_prompts == []
    final = _module(response, "evidence.final_gate")
    assert final.metrics["outcome"] == "reject"
    assert "no_kb_anchor" in final.metrics["reason_codes"]


def test_greeting_returns_a_direct_response_without_running_any_pipeline_module() -> None:
    """矩阵之外的第七种终态：问候旁路。零检索、零门禁、零生成。"""

    store = _ChainStore([_kb_chunk("a")])
    reranker = _ChainReranker()
    provider = _ChainWebProvider(results=(_web_result(),))
    generator = _MatrixGenerator()
    service = _matrix_service(
        store,
        policy=_web_enabled_policy(),
        generator=generator,
        reranker=reranker,
        web_provider=provider,
    )

    response = service.query("你好", retrieve_k=5, rerank_k=5)

    assert response.answer_status == "direct_response"
    assert response.answer == SOCIAL_DIRECT_REPLY
    assert response.sources == []
    assert response.pipeline_profile is None
    assert response.routing is not None
    assert response.routing.intent == "greeting"
    assert response.routing.control_outcome == "social"
    # 只留 Router 轨迹。改造前这里还会记一条 evidence.gate 的 skipped——那是一次从未
    # 发生过的门禁，技术抽屉会把它显示成"门禁跳过了"。
    assert [item.module_key for item in response.module_executions] == ["intent.router"]
    assert store.queries == []
    assert reranker.calls == 0
    assert provider.calls == 0
    assert generator.prompts == [], "问候既不生成答案，也不进结构化分类器"
    # 没有 retrieval / rerank / generation 阶段，前端据此不展示检索性能。
    assert set(response.latency_ms) == {"routing", "total"}


def test_freshness_prompt_mode_only_changes_the_stale_branch() -> None:
    """新增的第四个参数不能动到正常路径：默认值必须产出逐字相同的文本与哈希。

    这里刻意不 bump PROMPT_VERSION：test_prompts.py:21 与
    test_pgvector_integration.py:206/:289 三处硬断言着它，而版本号表示的是提示词协议，
    追加一段条件约束没有改变协议。
    """

    chunk = _kb_chunk("a")
    default = build_prompt("索引版本是多少", [chunk])
    explicit = build_prompt("索引版本是多少", [chunk], "fact_lookup", freshness_unverified=False)
    stale = build_prompt("索引版本是多少", [chunk], "fact_lookup", freshness_unverified=True)

    assert default.text == explicit.text
    assert default.sha256 == explicit.sha256
    assert "时效约束" not in default.text
    assert "时效未验证" in stale.text
    assert stale.text != default.text
    assert stale.sha256 != default.sha256
    assert stale.version == default.version == PROMPT_VERSION


def test_backend_answer_status_unions_stay_in_sync() -> None:
    """三处 union 不同步不会被静态检查抓到，只会在 pydantic 构造响应时 500。

    本仓库没有 mypy / pyright，`answer_status=` 传一个某处没声明的值，
    唯一的失败点是运行时的 ValidationError——而那时候答案已经生成完了。
    """

    declared = set(get_args(AnswerStatus))

    assert declared == {
        "answered",
        "answered_stale",
        "insufficient_evidence",
        "source_conflict",
        "retrieval_only",
        "generation_failed",
        "direct_response",
    }
    assert _literal_values(QueryResponse.model_fields["answer_status"].annotation) == declared
    assert (
        _literal_values(AnswerRecordResponse.model_fields["answer_status"].annotation) == declared
    )


def _literal_values(annotation: object) -> set[str]:
    """把 Literal[...] 与 Literal[...] | None 都摊平成字符串集合。"""

    values: set[str] = set()
    for item in get_args(annotation):
        if isinstance(item, str):
            values.add(item)
        else:
            values.update(_literal_values(item))
    return values


# --------------------------------------------------------------------------------------
# 设计稿 13.3 的五个验收场景。
#
# 与上面按机制拆开的用例分工不同：那些回答"某个判据为什么这样判"，这五条回答"这个业务
# 场景交付了没有"。问题文本逐字取自设计稿，断言落在用户与技术抽屉真正看得到的东西上
# ——终态、引用构成、外部调用次数、整条执行轨迹与门禁原因码。
#
# 五条全部跑在闭环链上：默认 rollout_stage="shadow" 时 effective_profile 恒为
# fact_lookup_v1（service.py:452-454），所以不需要也不应该去构造 canary/full。
# --------------------------------------------------------------------------------------

ACCEPTANCE_FACT_QUESTION = "索引版本是什么？"
ACCEPTANCE_FRESHNESS_QUESTION = "截至目前最新的索引版本是什么？"
ACCEPTANCE_SCOPED_QUESTION = "查找指定文档中的索引配置"
ACCEPTANCE_GREETING = "你好，在吗"


def test_acceptance_1_index_version_question_is_answered_from_knowledge_base_without_web() -> None:
    """场景 1：「索引版本是什么？」KB 足够，直接回答并引用 KB，不联网。"""

    store = _ChainStore([_kb_chunk("a"), _kb_chunk("b")])
    provider = _ChainWebProvider(results=(_web_result(),))
    generator = _MatrixGenerator()
    service = _matrix_service(
        store, policy=_web_enabled_policy(), generator=generator, web_provider=provider
    )

    response = service.query(ACCEPTANCE_FACT_QUESTION, retrieve_k=5, rerank_k=5)

    assert response.answer_status == "answered"
    assert response.answer == GROUNDED_ANSWER_BODY
    assert [item.chunk_id for item in response.sources] == ["a", "b"]
    assert {item.evidence_source_type for item in response.sources} == {"knowledge_base"}
    # Web 明明可用（开关开着、域名在、Provider 就绪、结果也准备好了）仍然一次都不打。
    assert provider.calls == 0
    # 一轮 KB 检索。纯中文问题不产生受控扩展，所以这里就是 1；断条数而不是断文本，
    # 因为 normalize_query 会把全角「？」NFKC 成半角，写死原句反而是假断言。
    assert len(store.queries) == 1
    assert tuple(item.module_key for item in response.module_executions) == CHAIN_MODULE_SEQUENCE
    assert _module(response, "retrieval.web_policy").metrics["decision"] == "not_needed"
    assert _module(response, "evidence.preliminary_gate").metrics["reason_codes"] == [
        "kb_evidence_sufficient"
    ]
    assert _module(response, "evidence.final_gate").metrics["reason_codes"] == [
        "kb_evidence_sufficient"
    ]


def test_acceptance_2_freshness_question_answers_stale_when_web_fails() -> None:
    """场景 2：「截至目前最新的索引版本是什么？」必须试 Web；失败则标时效未验证。"""

    store = _ChainStore([_kb_chunk("a"), _kb_chunk("b")])
    provider = _ChainWebProvider(error=RuntimeError("searxng unreachable"))
    generator = _MatrixGenerator()
    service = _matrix_service(
        store, policy=_web_enabled_policy(), generator=generator, web_provider=provider
    )

    response = service.query(ACCEPTANCE_FRESHNESS_QUESTION, retrieve_k=5, rerank_k=5)

    assert response.routing is not None
    assert response.routing.requires_freshness is True
    assert provider.calls == 1, "时效问题必须尝试一次 Web"
    assert response.answer_status == "answered_stale"
    assert response.answer == GROUNDED_ANSWER_BODY
    # 时效降级的引用只可能来自 KB：Final Gate 只在 web_count == 0 时给 stale。
    assert {item.evidence_source_type for item in response.sources} == {"knowledge_base"}
    web_policy = _module(response, "retrieval.web_policy")
    assert web_policy.status == "degraded"
    assert web_policy.metrics["decision"] == "failed"
    final = _module(response, "evidence.final_gate")
    assert final.metrics["outcome"] == "stale"
    assert final.metrics["freshness_verified"] is False
    # 顺序固定，技术抽屉按这个顺序拼「原因：…」。web_no_qualified_result 表示"搜了没拿到
    # 合格结果"，与从未发起（web_not_executed）是两回事。
    assert list(final.metrics["reason_codes"]) == [
        "freshness_required",
        "freshness_unverified",
        "web_no_qualified_result",
    ]
    assert "时效未验证" in generator.answer_prompts[0]


def test_acceptance_3_document_scoped_question_never_breaks_out_to_web() -> None:
    """场景 3：「查找指定文档中的索引配置」保持 KB 范围，不联网。"""

    chunks = [_kb_chunk("a"), _kb_chunk("b")]
    for chunk in chunks:
        # Query API 没有 document_ids 过滤（schemas.QueryMetadataFilter 只有分类、分类 ID、
        # 标签、来源类型和时间范围），页面把范围锁进库内资料的写法就是来源类型过滤。
        # 候选必须自带 source_type，否则 _filter_candidates 先把它们清空。
        chunk.metadata["source_type"] = "file"
    store = _ChainStore(chunks)
    provider = _ChainWebProvider(results=(_web_result(),))
    generator = _MatrixGenerator()
    service = _matrix_service(
        store, policy=_web_enabled_policy(), generator=generator, web_provider=provider
    )

    response = service.query(
        ACCEPTANCE_SCOPED_QUESTION,
        retrieve_k=5,
        rerank_k=5,
        filters=QueryMetadataFilter(source_types=["file"]),
    )

    assert provider.calls == 0
    assert response.answer_status == "answered"
    assert {item.evidence_source_type for item in response.sources} == {"knowledge_base"}
    web_policy = _module(response, "retrieval.web_policy")
    assert web_policy.status == "skipped"
    assert web_policy.metrics["decision"] == "scope_limited"
    assert web_policy.metrics["reason_code"] == "knowledge_base_scope_locked"


@pytest.mark.parametrize(
    "question", [ACCEPTANCE_FACT_QUESTION, ACCEPTANCE_FRESHNESS_QUESTION]
)
def test_acceptance_4_irrelevant_knowledge_base_is_rejected_even_when_web_has_the_answer(
    question: str,
) -> None:
    """场景 4：KB 只有无关内容时，Web 搜到答案也拒答——普通问题与时效问题都一样。

    只断 answer_status 测不出"为什么拒"：用户在技术抽屉看到的正是 no_kb_anchor，
    而"Web 已检索 + 缺少可锚定证据"这句话（types.describeWebExecution）就靠它。
    """

    store = _ChainStore([_kb_chunk("a")])
    reranker = _ChainReranker(scores={"知识库证据 a": -1.0})
    provider = _ChainWebProvider(results=(_web_result(),))
    generator = _MatrixGenerator()
    service = _matrix_service(
        store,
        policy=_web_enabled_policy(),
        generator=generator,
        reranker=reranker,
        web_provider=provider,
    )

    response = service.query(question, retrieve_k=5, rerank_k=5)

    assert provider.calls == 1, "缺少 KB anchor 也仍然允许打一次 Web（设计稿 5.3）"
    assert response.answer_status == "insufficient_evidence"
    assert response.sources == [], "Web 有答案也不能单独支撑回答"
    assert generator.answer_prompts == []
    assert _module(response, "retrieval.web_policy").metrics["decision"] == "executed"
    final = _module(response, "evidence.final_gate")
    assert final.metrics["outcome"] == "reject"
    assert "no_kb_anchor" in final.metrics["reason_codes"]
    assert "relevance_below_threshold" in final.metrics["reason_codes"]


def test_acceptance_5_greeting_returns_a_fixed_reply_with_no_pipeline_execution() -> None:
    """场景 5：「你好，在吗」固定回复，检索、Reranker、Web、Generator 均 0 次。

    与 test_greeting_returns_a_direct_response_without_running_any_pipeline_module 的
    区别在问句形态：那条用单词「你好」，这条是"两个问候词 + 标点"的组合，走
    `_GREETING` 的 `(?:词 填充)+` 重复分支。该分支此前只有 Router 级用例
    （test_router_tolerates_punctuation_and_whitespace_around_greetings），没有整链用例。
    """

    store = _ChainStore([_kb_chunk("a")])
    reranker = _ChainReranker()
    provider = _ChainWebProvider(results=(_web_result(),))
    generator = _MatrixGenerator()
    service = _matrix_service(
        store,
        policy=_web_enabled_policy(),
        generator=generator,
        reranker=reranker,
        web_provider=provider,
    )

    response = service.query(ACCEPTANCE_GREETING, retrieve_k=5, rerank_k=5)

    assert response.answer_status == "direct_response"
    assert response.answer == SOCIAL_DIRECT_REPLY
    assert response.sources == []
    assert store.queries == []
    assert reranker.calls == 0
    assert provider.calls == 0
    assert generator.prompts == [], "问候既不生成答案，也不进结构化分类器"
    assert [item.module_key for item in response.module_executions] == ["intent.router"]
    assert set(response.latency_ms) == {"routing", "total"}
