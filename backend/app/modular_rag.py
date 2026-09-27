from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol
from uuid import uuid4

QueryIntent = Literal["greeting", "fact_lookup", "summarize", "compare", "procedure"]
ControlOutcome = Literal["route", "social", "clarify", "out_of_scope"]
ModuleStatus = Literal["succeeded", "failed", "skipped", "degraded"]
# greeting 不在表内：它没有 Pipeline Profile，也不该出现在每个知识库的策略快照里。
DEFAULT_PROFILE_VERSIONS = {
    "fact_lookup": "1",
    "summarize": "1",
    "compare": "1",
    "procedure": "1",
}
# 问候旁路的固定回复。Router 只产出 greeting + social 决策，这条文案由 service 层在
# social 分支直接返回，不经过 Embedding、检索、门禁或生成模型。
SOCIAL_DIRECT_REPLY = "你好，我在。你可以询问当前知识库中的事实、配置或资料内容。"


class StructuredClassifier(Protocol):
    model_name: str

    def generate(self, prompt: str) -> tuple[str, dict[str, object]]: ...


@dataclass(frozen=True)
class ModuleDefinition:
    module_key: str
    version: str
    capability: str
    input_schema_ref: str = "rag://schemas/module-input/v1"
    output_schema_ref: str = "rag://schemas/module-output/v1"
    timeout_ms: int = 8_000
    max_attempts: int = 1
    fallback_module: str | None = None


@dataclass(frozen=True)
class ExecutionContext:
    user_id: str
    knowledge_base_id: str
    conversation_id: str
    active_index_version_id: str | None
    policy_snapshot: dict[str, object]
    deadline_monotonic: float


@dataclass(frozen=True)
class ModuleResult:
    status: ModuleStatus
    output_reference: str | None
    metrics: dict[str, object]
    input_hash: str
    output_hash: str | None
    error_code: str | None = None
    error_message: str | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True)
class PipelineProfile:
    profile_id: str
    version: str
    intent: QueryIntent
    modules: tuple[str, ...]
    required_capabilities: tuple[str, ...] = (
        "vector",
        "keyword",
        "metadata",
        "acl",
        "citation",
        "parser",
    )
    parameters: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RAGPolicy:
    rollout_stage: Literal["shadow", "canary", "full"] = "shadow"
    web_search_enabled: bool = False
    allowed_domains: tuple[str, ...] = ()
    intent_confidence_threshold: float = 0.8
    minimum_evidence_count: int = 1
    max_web_results: int = 5
    profile_versions: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_PROFILE_VERSIONS)
    )

    def snapshot(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RoutingDecision:
    intent: QueryIntent | None
    confidence: float
    reason: str
    control_outcome: ControlOutcome
    pipeline_profile: str | None
    profile_version: str | None
    original_question: str
    effective_question: str
    follow_up_rewritten: bool = False
    # 用户需求（问题是否要求时效信息），不是执行策略。是否真的访问 Web 由门禁另行决定。
    requires_freshness: bool = False
    classifier_model: str | None = None
    fallback_used: bool = False

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        # 过渡别名：前端类型、answer_records.routing 的历史数据与
        # query_executions.requires_web 列都还读旧名，两个键必须同时出现。
        payload["requires_web"] = self.requires_freshness
        return payload


@dataclass
class ModuleExecutionTrace:
    module_execution_id: str
    sequence: int
    module_key: str
    module_version: str
    status: ModuleStatus
    attempt: int
    started_at: float
    finished_at: float
    duration_ms: float
    input_hash: str
    output_hash: str | None = None
    metrics: dict[str, object] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    fallback_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class ExecutionTrace:
    def __init__(self, execution_id: str | None = None):
        self.execution_id = execution_id or f"qex_{uuid4().hex[:20]}"
        self.modules: list[ModuleExecutionTrace] = []

    def record(
        self,
        module_key: str,
        module_version: str,
        started_at: float,
        input_value: object,
        output_value: object | None,
        *,
        status: ModuleStatus = "succeeded",
        attempt: int = 1,
        metrics: dict[str, object] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        fallback_reason: str | None = None,
    ) -> None:
        finished_monotonic = time.perf_counter()
        finished_at = time.time()
        duration_ms = round((finished_monotonic - started_at) * 1000, 3)
        self.modules.append(
            ModuleExecutionTrace(
                module_execution_id=f"mex_{uuid4().hex[:20]}",
                sequence=len(self.modules) + 1,
                module_key=module_key,
                module_version=module_version,
                status=status,
                attempt=attempt,
                started_at=finished_at - duration_ms / 1000,
                finished_at=finished_at,
                duration_ms=duration_ms,
                input_hash=_stable_hash(input_value),
                output_hash=_stable_hash(output_value) if output_value is not None else None,
                metrics=metrics or {},
                error_code=error_code,
                error_message=error_message,
                fallback_reason=fallback_reason,
            )
        )

    def as_list(self) -> list[dict[str, object]]:
        return [item.as_dict() for item in self.modules]


class ModuleRegistry:
    def __init__(self):
        self._definitions: dict[str, ModuleDefinition] = {}

    def register(self, definition: ModuleDefinition) -> None:
        existing = self._definitions.get(definition.module_key)
        if existing and existing != definition:
            raise ValueError(f"模块 {definition.module_key} 已使用不同定义注册")
        self._definitions[definition.module_key] = definition

    def get(self, module_key: str) -> ModuleDefinition:
        try:
            return self._definitions[module_key]
        except KeyError as exc:
            raise LookupError(f"模块 {module_key} 未注册") from exc

    def validate_profile(
        self,
        profile: PipelineProfile,
        capability_manifest: dict[str, object] | None = None,
    ) -> list[str]:
        errors = [f"模块 {key} 未注册" for key in profile.modules if key not in self._definitions]
        if capability_manifest is not None:
            missing = [
                capability
                for capability in profile.required_capabilities
                if not capability_manifest.get(capability)
            ]
            errors.extend(f"索引缺少能力：{item}" for item in missing)
        if len(profile.modules) != len(set(profile.modules)):
            errors.append("Profile 不允许重复模块")
        if not profile.modules or profile.modules[-1] != "generation.verify":
            errors.append("Profile 必须以 generation.verify 结束")
        return errors


DEFAULT_PIPELINE_PROFILES: dict[str, PipelineProfile] = {
    "fact_lookup_v1": PipelineProfile(
        "fact_lookup_v1",
        # 版本号必须保持 "1"。RAG Policy 的 PUT 改成合并写之后，profile_versions 不再随
        # DEFAULT_PIPELINE_PROFILES 重算，而 service.py 在 canary/full 阶段比对两者不一致
        # 就抛 RAG_PROFILE_INCOMPATIBLE——bump 它等于让所有已晋级的知识库当场全部查询失败，
        # 且管理员没有任何接口能把库里的旧版本号刷新过来。
        "1",
        "fact_lookup",
        (
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
        ),
        parameters={"query_expansion": True, "coverage_mode": "relevance"},
    ),
    "summarize_v1": PipelineProfile(
        "summarize_v1",
        "1",
        "summarize",
        (
            "query.scope",
            "retrieval.coverage",
            "retrieval.web_policy",
            "evidence.diversify",
            "context.compress",
            "evidence.gate",
            "generation.summary",
            "generation.verify",
        ),
        parameters={"query_expansion": False, "coverage_mode": "document_diversity"},
    ),
    "compare_v1": PipelineProfile(
        "compare_v1",
        "1",
        "compare",
        (
            "query.decompose",
            "retrieval.knowledge_base",
            "retrieval.web_policy",
            "evidence.balance",
            "evidence.conflict",
            "evidence.gate",
            "generation.compare",
            "generation.verify",
        ),
        parameters={"query_expansion": True, "coverage_mode": "balanced_entities"},
    ),
    "procedure_v1": PipelineProfile(
        "procedure_v1",
        "1",
        "procedure",
        (
            "query.constraints",
            "retrieval.heading_weighted",
            "retrieval.web_policy",
            "evidence.order",
            "evidence.gate",
            "generation.procedure",
            "generation.verify",
        ),
        parameters={"query_expansion": True, "coverage_mode": "ordered_steps"},
    ),
}


def build_default_registry() -> ModuleRegistry:
    registry = ModuleRegistry()
    capabilities = {
        "intent.router": "query_routing",
        "query.normalize": "query_transformation",
        "query.expand": "query_expansion",
        "query.scope": "query_transformation",
        "query.decompose": "query_expansion",
        "query.constraints": "query_construction",
        "retrieval.knowledge_base": "hybrid_retrieval",
        "retrieval.coverage": "hybrid_retrieval",
        "retrieval.heading_weighted": "hybrid_retrieval",
        "retrieval.web_policy": "web_retrieval",
        "rerank.knowledge_base": "reranking",
        "rerank.unified": "reranking",
        "evidence.fuse": "evidence_selection",
        "evidence.diversify": "evidence_selection",
        "evidence.balance": "evidence_selection",
        "evidence.conflict": "verification",
        "evidence.order": "evidence_selection",
        "context.compress": "context_compression",
        "evidence.preliminary_gate": "verification",
        "evidence.final_gate": "verification",
        # evidence.gate 仍被 summarize_v1 / compare_v1 / procedure_v1 使用，不能随
        # fact_lookup_v1 改链一起删：注册表是增量的。
        "evidence.gate": "verification",
        "generation.fact": "generation",
        "generation.summary": "generation",
        "generation.compare": "generation",
        "generation.procedure": "generation",
        "generation.verify": "verification",
    }
    for key, capability in capabilities.items():
        registry.register(ModuleDefinition(key, "1", capability))
    return registry


DEFAULT_MODULE_REGISTRY = build_default_registry()


class QueryIntentRouter:
    _RULES: tuple[tuple[QueryIntent, re.Pattern[str]], ...] = (
        ("summarize", re.compile(r"总结|概括|摘要|综述|归纳|要点")),
        ("compare", re.compile(r"对比|比较|区别|差异|异同|\bvs\.?\b", re.IGNORECASE)),
        ("procedure", re.compile(r"如何|怎么|怎样|步骤|流程|操作|办理")),
    )
    _FRESHNESS = re.compile(r"最新|今天|实时|外部|互联网|官网|截至(?:今天|目前)")
    _FOLLOW_UP = re.compile(r"^(这个|那个|它|他们|上述|前面|刚才|继续|那).{0,30}$")
    # 设计稿 3.2 的问候全表。
    _GREETING_WORDS: tuple[str, ...] = (
        "你好",
        "您好",
        "嗨",
        "哈喽",
        "hello",
        "hi",
        "早上好",
        "下午好",
        "晚上好",
        "在吗",
        "有人吗",
        "能听到吗",
        "方便吗",
    )
    _GREETING_PADDING = r"[\s~～!！?？.。,，、]*"
    # 整句 fullmatch 而不是 search：「你好，索引版本是什么？」里的「你好」只是前缀，
    # 业务问题必须优先。表里没有任何词是 _RULES 或 _FRESHNESS 的子串，所以把问候判定
    # 排在业务规则之前不会截走业务问题。
    _GREETING = re.compile(
        rf"{_GREETING_PADDING}(?:(?:{'|'.join(_GREETING_WORDS)}){_GREETING_PADDING})+",
        re.IGNORECASE,
    )

    def __init__(
        self,
        classifier: StructuredClassifier | None = None,
        confidence_threshold: float = 0.8,
    ):
        self.classifier = classifier
        self.confidence_threshold = confidence_threshold

    def route(
        self,
        question: str,
        history: list[dict[str, object]] | None = None,
    ) -> RoutingDecision:
        original = question.strip()
        effective, rewritten = self._rewrite_follow_up(original, history or [])
        # 问候判定必须排在长度闸之前：「嗨」「你好」「Hi」「在吗」「方便吗」全部短于 4，
        # 放到下面就会先被判成 clarify，整条旁路不生效。
        if self._GREETING.fullmatch(effective):
            return self._social(original, effective)
        if len(effective) < 4:
            return self._control(original, effective, "问题信息不足，请补充要查询的对象。")
        if self._FOLLOW_UP.search(original) and not rewritten:
            return self._control(original, effective, "追问缺少可用的会话上下文，请补充查询对象。")
        for intent, pattern in self._RULES:
            if pattern.search(effective):
                return self._decision(
                    intent,
                    0.98,
                    "命中确定性意图规则",
                    original,
                    effective,
                    rewritten,
                    requires_freshness=bool(self._FRESHNESS.search(effective)),
                )
        if self.classifier is None:
            return self._decision(
                "fact_lookup",
                0.8,
                "分类器未配置，完整问题安全降级到事实查找",
                original,
                effective,
                rewritten,
                requires_freshness=bool(self._FRESHNESS.search(effective)),
                fallback_used=True,
            )
        try:
            raw, _metadata = self.classifier.generate(_classification_prompt(effective))
            payload = _parse_json_object(raw)
            intent = payload.get("intent")
            confidence = float(payload.get("confidence", 0))
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("confidence is invalid")
            reason = str(payload.get("reason") or "结构化分类")
            # 分类器的 JSON 契约保持 requires_web 不变：改名会让已有提示词与样例失效。
            # 只在映射到 RoutingDecision 时换成 requires_freshness。
            if not isinstance(payload.get("requires_web"), bool):
                raise ValueError("requires_web is invalid")
            requires_freshness = payload["requires_web"]
            if intent == "out_of_scope":
                return RoutingDecision(
                    intent=None,
                    confidence=confidence,
                    reason=reason,
                    control_outcome="out_of_scope",
                    pipeline_profile=None,
                    profile_version=None,
                    original_question=original,
                    effective_question=effective,
                    follow_up_rewritten=rewritten,
                    requires_freshness=requires_freshness,
                    classifier_model=self.classifier.model_name,
                )
            if intent not in {"fact_lookup", "summarize", "compare", "procedure"}:
                raise ValueError("intent is invalid")
            if confidence < self.confidence_threshold:
                return self._control(
                    original,
                    effective,
                    reason or "意图置信度不足",
                    confidence=confidence,
                    classifier_model=self.classifier.model_name,
                    rewritten=rewritten,
                )
            return self._decision(
                intent,
                confidence,
                reason,
                original,
                effective,
                rewritten,
                requires_freshness=requires_freshness or bool(self._FRESHNESS.search(effective)),
                classifier_model=self.classifier.model_name,
            )
        except Exception:
            return self._decision(
                "fact_lookup",
                0.8,
                "分类器不可用，完整问题安全降级到事实查找",
                original,
                effective,
                rewritten,
                requires_freshness=bool(self._FRESHNESS.search(effective)),
                classifier_model=self.classifier.model_name,
                fallback_used=True,
            )

    def _rewrite_follow_up(
        self,
        question: str,
        history: list[dict[str, object]],
    ) -> tuple[str, bool]:
        if not history or not self._FOLLOW_UP.search(question) or self.classifier is None:
            return question, False
        recent = history[-6:]
        context = "\n".join(
            f"问：{item.get('question', '')}\n答：{str(item.get('answer') or '')[:800]}"
            for item in recent
        )
        prompt = (
            "把当前追问改写为可独立检索的问题。只能补全对话中已经出现的信息，"
            "不要回答问题，只输出改写后的单行问题。\n"
            f"历史：\n{context}\n当前追问：{question}"
        )
        try:
            raw, _metadata = self.classifier.generate(prompt)
            rewritten = raw.strip().splitlines()[0][:2000]
        except Exception:
            return question, False
        return (rewritten, True) if len(rewritten) >= 4 else (question, False)

    def _decision(
        self,
        intent: QueryIntent,
        confidence: float,
        reason: str,
        original: str,
        effective: str,
        rewritten: bool,
        *,
        requires_freshness: bool,
        classifier_model: str | None = None,
        fallback_used: bool = False,
    ) -> RoutingDecision:
        # greeting 没有同名 Profile，走到这里就是 KeyError —— 它必须在 route() 里被
        # _social() 提前截走，不要给它加一条 greeting_v1。
        profile = DEFAULT_PIPELINE_PROFILES[f"{intent}_v1"]
        return RoutingDecision(
            intent=intent,
            confidence=min(max(confidence, 0), 1),
            reason=reason,
            control_outcome="route",
            pipeline_profile=profile.profile_id,
            profile_version=profile.version,
            original_question=original,
            effective_question=effective,
            follow_up_rewritten=rewritten,
            requires_freshness=requires_freshness,
            classifier_model=classifier_model,
            fallback_used=fallback_used,
        )

    @staticmethod
    def _social(original: str, effective: str) -> RoutingDecision:
        return RoutingDecision(
            intent="greeting",
            confidence=1.0,
            reason="整句纯问候，直接回应引导语，不检索也不生成。",
            control_outcome="social",
            pipeline_profile=None,
            profile_version=None,
            original_question=original,
            effective_question=effective,
        )

    @staticmethod
    def _control(
        original: str,
        effective: str,
        reason: str,
        *,
        confidence: float = 0,
        classifier_model: str | None = None,
        rewritten: bool = False,
    ) -> RoutingDecision:
        return RoutingDecision(
            intent=None,
            confidence=confidence,
            reason=reason,
            control_outcome="clarify",
            pipeline_profile=None,
            profile_version=None,
            original_question=original,
            effective_question=effective,
            follow_up_rewritten=rewritten,
            requires_freshness=False,
            classifier_model=classifier_model,
        )


def capability_manifest(component_manifest: dict[str, object] | None) -> dict[str, object]:
    manifest = component_manifest or {}
    return {
        "vector": bool(manifest.get("vector_index_schema_version")),
        "keyword": bool(manifest.get("keyword_index_schema_version")),
        "metadata": bool(manifest.get("metadata_schema_version")),
        "acl": bool(manifest.get("acl_schema_version")),
        "citation": bool(manifest.get("citation_schema_version")),
        "parser": bool(manifest.get("parser_schema_version")),
        "components": manifest,
    }


def _classification_prompt(question: str) -> str:
    return (
        "你是受控 RAG 的意图分类器。只输出 JSON，不要解释。"
        "intent 只能是 fact_lookup、summarize、compare、procedure、out_of_scope；"
        "confidence 为 0 到 1；reason 为简短中文；requires_web 仅在问题明确要求最新、"
        "外部或互联网信息时为 true。\n"
        f"问题：{question}"
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("classifier output must be an object")
    return payload


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
