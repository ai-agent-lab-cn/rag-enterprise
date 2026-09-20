import time

from backend.app.modular_rag import (
    DEFAULT_PIPELINE_PROFILES,
    ExecutionTrace,
    ModuleDefinition,
    ModuleRegistry,
    QueryIntentRouter,
)
from backend.evaluation.intent_routing import evaluate_intent_router


class _Classifier:
    model_name = "classifier-test"

    def __init__(self, payload: str):
        self.payload = payload

    def generate(self, prompt: str):
        assert "只输出 JSON" in prompt
        return self.payload, {"provider": "test"}


def test_router_uses_deterministic_high_confidence_rules() -> None:
    router = QueryIntentRouter()

    assert router.route("请总结这份制度").intent == "summarize"
    assert router.route("比较方案 A 和方案 B 的区别").intent == "compare"
    assert router.route("如何完成索引回滚，步骤是什么").intent == "procedure"
    assert router.route("合同编号是什么？").intent == "fact_lookup"
    assert router.route("配置指纹包含哪些字段？").intent == "fact_lookup"
    assert router.route("当前生效的索引版本是哪一个？").requires_web is False


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
        ],
    )

    assert result.macro_f1 == 1
    assert set(result.per_intent_f1) == {"fact_lookup", "summarize", "compare", "procedure"}
