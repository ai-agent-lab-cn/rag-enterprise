# `fact_lookup` 完整闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先清除 RAG Policy 中“页面可配置但运行语义不成立”的能力，再完成 `fact_lookup_v1` 从路由、KB 检索、条件 Web 补检、双层 Evidence Gate 到回答或受控拒答的闭环。

**Architecture:** 保留现有 Query API、PostgreSQL 表和模块执行轨迹，在 `service.py` 外新增独立 Evidence Gate 与策略更新边界。KB 仍是回答基础，Web 最多补检一次且不能形成 Web-only 答案；页面只展示真实可控的 Web 开关、可信域名与实际执行状态。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、PostgreSQL/psycopg、React 18、TypeScript、Vitest、pytest。

**Spec:** `docs/superpowers/specs/2026-09-20-fact-lookup-closed-loop-design.md`

## Global Constraints

- 单次请求最多一轮 KB 检索和一轮 Web 检索；查询扩展属于同一轮 KB 检索。
- Web 只能补充 KB，最终回答至少需要一条通过门禁的 KB evidence。
- 普通问题 KB 足够时不访问 Web；时效问题始终尝试一次 Web。
- 显式文档、分类、标签或时间范围过滤时禁止 Web 突破范围。
- `rollout_stage`、内部阈值及 Profile 版本保留在数据库中，但不再作为管理员可编辑能力展示。
- 不新增数据库迁移，不删除已有字段，不改变 Query API 路径。
- 保留工作区全部已有修改；不清理、不重置、不覆盖无关文件。
- 本轮不 Commit、不 Push。
- 当前没有“轻量验证”或“完整验证”授权：编写测试代码，但不要执行 pytest、Vitest、ESLint、类型检查、构建或页面验收。计划中的验证命令仅在用户明确授权后运行。

## File Map

- Create `backend/app/evidence_gate.py`：Reranker 分数语义、证据资格检查、Preliminary/Final Gate。
- Create `backend/app/rag_policy.py`：公开策略更新与内部只读字段保护。
- Modify `backend/app/modular_rag.py`：`greeting`、`social`、`requires_freshness`、`fact_lookup_v1` 模块顺序。
- Modify `backend/app/service.py`：一轮 KB 检索、双层门禁、一次 Web 补检与真实模块轨迹。
- Modify `backend/app/prompts.py`：`answered_stale` 生成约束与状态处理。
- Modify `backend/app/schemas.py`：兼容请求、响应枚举和门禁事件字段。
- Modify `backend/app/main.py`：策略写契约、Provider 配置检查、公开 Profile 收口。
- Modify `backend/app/postgres_history.py`：保留内部策略字段，只持久化合并后的完整策略。
- Modify `backend/app/history.py`、`backend/app/postgres_evaluation.py`：新回答状态的历史兼容与评测聚合。
- Modify `frontend/src/types.ts`、`frontend/src/api.ts`：新状态和最小策略写请求。
- Modify `frontend/src/components/KnowledgeBaseDetailPage.tsx`：RAG Policy 只显示 Web 与域名。
- Modify `frontend/src/components/AnswerPanel.tsx`、`SourceCard.tsx`、`TechnicalDrawer.tsx`、`ChatPage.tsx`：时效状态、问候回复、来源分组和真实 Web 状态。
- Modify `backend/tests/test_modular_rag.py`、`test_web_retrieval.py`、`test_retrieval_access.py`：路由、门禁与服务链测试。
- Create `backend/tests/test_rag_policy.py`：策略更新与兼容字段测试。
- Modify `frontend/src/App.test.tsx`：策略弹框、回答状态和技术抽屉测试。
- Modify `backend/evaluation/datasets/intent_routing_v1.json`：加入纯问候与问候加业务问题样本。
- Modify `backend/evaluation/intent_routing.py`：将 `greeting` 作为独立评测类别。

---

### Task 1: 收口 RAG Policy 后端写契约

**Files:**
- Create: `backend/app/rag_policy.py`
- Modify: `backend/app/schemas.py`（`RAGPolicyUpdate`）
- Modify: `backend/app/main.py`（`update_knowledge_base_rag_policy`、`list_rag_pipeline_profiles`）
- Modify: `backend/app/postgres_history.py`（`PostgresRAGPolicyRepository.update` 调用边界）
- Create: `backend/tests/test_rag_policy.py`

**Interfaces:**
- Consumes: `modular_rag.RAGPolicy`、`schemas.RAGPolicyUpdate`、`Settings.searxng_base_url`。
- Produces: `merge_public_rag_policy_update(current, payload, searxng_base_url) -> RAGPolicy`；稳定错误 `RAG_POLICY_FIELD_READ_ONLY`、`WEB_SEARCH_PROVIDER_NOT_CONFIGURED`。

- [ ] **Step 1: 编写公开策略更新的行为测试**

在 `backend/tests/test_rag_policy.py` 覆盖以下明确断言：

```python
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
```

同时增加：Provider 未配置时启用 Web 被拒绝；旧客户端提交相同内部值被接受；旧客户端修改内部值抛出只读错误；关闭 Web 时允许预先保存域名。

- [ ] **Step 2: 定义兼容请求模型**

将 `RAGPolicyUpdate` 改为只要求两个公开字段，内部字段全部可选：

```python
class RAGPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    web_search_enabled: bool
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    rollout_stage: Literal["shadow", "canary", "full"] | None = None
    intent_confidence_threshold: float | None = Field(default=None, ge=0.5, le=1)
    minimum_evidence_count: int | None = Field(default=None, ge=1, le=10)
    max_web_results: int | None = Field(default=None, ge=1, le=5)
```

保留现有域名规范化和“启用 Web 必须有域名”校验。

- [ ] **Step 3: 实现策略合并器**

在 `backend/app/rag_policy.py` 定义专用异常和合并函数：

```python
class RAGPolicyFieldReadOnly(ValueError):
    pass


class WebSearchProviderNotConfigured(ValueError):
    pass


def merge_public_rag_policy_update(
    current: RAGPolicy,
    payload: RAGPolicyUpdate,
    searxng_base_url: str,
) -> RAGPolicy:
    # 校验 legacy 字段：None 或等于 current 才接受。
    # 启用 Web 时要求 base URL 的 scheme/host 合法。
    # 使用 dataclasses.replace 只替换两个公开字段。
```

只接受 `http/https` SearXNG 地址；这里只检查配置格式，不发起网络请求。

- [ ] **Step 4: 接入 API 并保护事务语义**

在 `update_knowledge_base_rag_policy` 中先 `policies.get()`，再调用合并器，最后调用现有 Repository `update()`。分别映射：

```python
RAGPolicyFieldReadOnly -> AppError("RAG_POLICY_FIELD_READ_ONLY", ..., 409)
WebSearchProviderNotConfigured -> AppError("WEB_SEARCH_PROVIDER_NOT_CONFIGURED", ..., 409)
```

审计 metadata 只记录 `web_search_enabled`、域名数量，以及两者是否变化。不要再记录一次并未由用户发布的 `rollout_stage`。

- [ ] **Step 5: 收口公开 Profile**

`GET /api/rag/pipeline-profiles` 只返回：

```python
DEFAULT_PIPELINE_PROFILES["fact_lookup_v1"]
```

不要删除其他内部定义，避免破坏后续意图开发与历史策略快照。

- [ ] **Step 6: 记录待授权验证命令**

仅在用户明确授权“轻量验证”后运行：

```bash
uv run pytest backend/tests/test_rag_policy.py backend/tests/test_modular_rag.py -q
```

预期：策略合并、只读保护、Provider 配置校验和公开 Profile 测试全部通过。

---

### Task 2: 精简 RAG Policy 页面

**Files:**
- Modify: `frontend/src/types.ts`（`RAGPolicyUpdatePayload`）
- Modify: `frontend/src/api.ts`（`updateRagPolicy`）
- Modify: `frontend/src/components/KnowledgeBaseDetailPage.tsx`（策略状态、保存函数、弹框）
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: Task 1 的最小 PUT 契约。
- Produces: `RAGPolicyUpdatePayload = Pick<RAGPolicy, "web_search_enabled" | "allowed_domains">`。

- [ ] **Step 1: 添加策略弹框的前端测试**

在现有知识库详情测试场景增加：

```tsx
expect(screen.queryByText("发布阶段")).not.toBeInTheDocument();
expect(screen.queryByText("意图置信度")).not.toBeInTheDocument();
expect(screen.queryByText("最少证据")).not.toBeInTheDocument();
expect(screen.queryByText("Web 结果上限")).not.toBeInTheDocument();
expect(screen.getByText("启用受控 Web 检索")).toBeInTheDocument();
expect(screen.getByText("可信域名白名单")).toBeInTheDocument();
```

保存断言必须验证请求 body 只有 `web_search_enabled` 和 `allowed_domains`。

- [ ] **Step 2: 缩小前端写请求类型**

在 `frontend/src/types.ts` 新增：

```ts
export type RAGPolicyUpdatePayload = Pick<RAGPolicy, "web_search_enabled" | "allowed_domains">;
```

在 `api.ts` 将 `updateRagPolicy` 参数改为该类型。

- [ ] **Step 3: 简化弹框状态与提交内容**

保留 GET 返回的完整 `RAGPolicy` 用于兼容读取，但弹框草稿只保存：

```ts
type RAGPolicyDraft = {
  web_search_enabled: boolean;
  allowed_domains: string[];
};
```

删除发布阶段、意图阈值、证据数、Web 数量控件。保存时只提交这两个字段，域名继续 Trim、去空、由后端去重和验证。

- [ ] **Step 4: 修正文案和交互**

弹框 description 使用：

```text
控制当前知识库是否允许受控 Web 补检，并限定可访问的可信域名。
```

补充固定说明：Web 只补充知识库证据，不会写入知识库或绕过检索范围。开启 Web 但域名为空时，在前端阻止提交并显示“至少填写一个可信域名”。

- [ ] **Step 5: 记录待授权验证命令**

仅在用户明确授权“轻量验证”后运行：

```bash
cd frontend && npm test -- src/App.test.tsx
```

预期：策略弹框只显示两个公开字段，PUT body 不包含隐藏字段。

---

### Task 3: 建立问候旁路和准确的时效路由契约

**Files:**
- Modify: `backend/app/modular_rag.py`
- Modify: `backend/app/schemas.py`（`RoutingMetadata`、`QueryResponse`）
- Modify: `backend/tests/test_modular_rag.py`
- Modify: `backend/evaluation/datasets/intent_routing_v1.json`
- Modify: `backend/evaluation/intent_routing.py`

**Interfaces:**
- Consumes: 现有 `QueryIntentRouter.route(question, history)`。
- Produces: `QueryIntent += greeting`、`ControlOutcome += social`、`RoutingDecision.requires_freshness`；`requires_web` 只作为过渡序列化别名。

- [ ] **Step 1: 写路由行为测试和评测样本**

增加以下测试：

```python
@pytest.mark.parametrize("question", ["嗨", "你好", "您好", "Hi", "在吗", "方便吗"])
def test_router_sends_pure_greetings_to_social(question: str) -> None:
    decision = QueryIntentRouter().route(question)
    assert decision.intent == "greeting"
    assert decision.control_outcome == "social"
    assert decision.pipeline_profile is None


def test_business_question_wins_over_greeting_prefix() -> None:
    decision = QueryIntentRouter().route("你好，索引版本是什么？")
    assert decision.intent == "fact_lookup"
    assert decision.control_outcome == "route"
```

为“最新、今天、实时、截至目前”等问题断言 `requires_freshness=True`。

- [ ] **Step 2: 修改类型与序列化兼容层**

更新：

```python
QueryIntent = Literal["greeting", "fact_lookup", "summarize", "compare", "procedure"]
ControlOutcome = Literal["route", "social", "clarify", "out_of_scope"]
```

`RoutingDecision` 使用 `requires_freshness` 存储真实含义；`as_dict()` 额外输出：

```python
payload["requires_web"] = self.requires_freshness
```

保证旧前端在过渡期仍能读取。

- [ ] **Step 3: 实现问候判定优先级**

先检测“是否包含业务问题特征”，再检测整句纯问候。问候正则必须整句匹配，不能让“你好，怎么回滚”进入 social。

固定 social 回复：

```text
你好，我在。你可以询问当前知识库中的事实、配置或资料内容。
```

`RAGService.query()` 在 social 分支直接构造 `answer_status=direct_response`，只记录 Router trace，不调用 active index、Embedding、Reranker、Web 或 Generator。

- [ ] **Step 4: 更新 Schema 枚举**

`RoutingMetadata.intent` 增加 `greeting`，`control_outcome` 增加 `social`，新增 `requires_freshness: bool` 并暂时保留 `requires_web: bool`。`QueryResponse` 与 `AnswerRecordResponse` 的 `answer_status` 增加 `direct_response`。将 `QueryRequest.question` 的 Schema 最小长度从 2 调整为 1；空白仍由现有 validator 拒绝，使单字问候“嗨”能进入 Router，而单字非问候由 Router 返回 clarify。

- [ ] **Step 5: 更新意图评测样本**

在 JSON 数据集中加入至少：单字中文问候、普通中文问候、英文问候、确认在场、问候加事实问题五类样本；同步扩展 `intent_routing.py` 的类别集合，对 `greeting` 计算独立 Precision/Recall/F1，不把它合并到 `fact_lookup`。

- [ ] **Step 6: 记录待授权验证命令**

```bash
uv run pytest backend/tests/test_modular_rag.py -q
```

预期：问候旁路、业务优先、时效标记和旧字段别名全部通过。

---

### Task 4: 提取确定性的 Evidence Gate

**Files:**
- Create: `backend/app/evidence_gate.py`
- Create: `backend/tests/test_evidence_gate.py`
- Modify: `backend/app/ranking.py`（只在需要时暴露原始 Reranker 分数，不改变排序公式）

**Interfaces:**
- Consumes: `list[RetrievedChunk]`、Reranker model name、最低证据数、`requires_freshness`、Web 执行结果。
- Produces: `EvidenceGateResult`、`evaluate_preliminary_evidence(...)`、`evaluate_final_evidence(...)`。

- [ ] **Step 1: 编写门禁数据驱动测试**

明确覆盖：

```python
def test_single_relevant_candidate_does_not_fail_due_to_minmax() -> None: ...
def test_low_relevance_candidate_is_rejected() -> None: ...
def test_kb_source_missing_document_version_is_not_citation_ready() -> None: ...
def test_web_source_missing_url_or_hash_is_not_citation_ready() -> None: ...
def test_final_gate_requires_one_qualified_kb_anchor() -> None: ...
def test_freshness_without_web_returns_stale_only_with_kb_anchor() -> None: ...
def test_unknown_reranker_score_semantics_is_configuration_error() -> None: ...
```

- [ ] **Step 2: 定义稳定结果对象**

```python
GateOutcome = Literal["pass", "needs_web", "stale", "reject"]

@dataclass(frozen=True)
class EvidenceGateResult:
    outcome: GateOutcome
    selected: tuple[RetrievedChunk, ...]
    kb_count: int
    web_count: int
    freshness_verified: bool
    reason_codes: tuple[str, ...]

    @property
    def sufficient(self) -> bool:
        return self.outcome in {"pass", "stale"}
```

- [ ] **Step 3: 定义 Reranker 分数语义**

使用精确映射，不使用查询内 Min-Max：

```python
RERANKER_THRESHOLDS = {
    "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1": 0.0,
    "demo/lexical-overlap-v1": 0.0,
}
```

CrossEncoder 使用 `>= 0.0`，demo overlap 使用 `> 0.0`。未知模型抛出 `UnsupportedRerankerScoreSemantics`，由服务层映射为 `RAG_PROFILE_INCOMPATIBLE`。

- [ ] **Step 4: 实现来源资格检查**

KB 必须具备：`document_id`、`document_version_id`、`content_sha256`、`filename`、Chunk 定位字段。Web 必须具备：`source_url`、`filename/title`、`retrieved_at`、`content_sha256`。门禁只选择同时通过相关性和引用完整性的候选。

- [ ] **Step 5: 实现 Preliminary / Final Gate**

Preliminary：普通问题 KB 合格返回 `pass`；不足且允许 Web 返回 `needs_web`；时效问题只要有 KB anchor 就返回 `needs_web`；没有 KB anchor 仍可执行一次 Web 以记录补检，但 Final Gate 必须拒绝 Web-only。

Final：普通问题满足数量和 KB anchor 返回 `pass`；时效问题有 Web 返回 `pass`，无 Web 但有 KB anchor 返回 `stale`；无 KB anchor 返回 `reject`。

- [ ] **Step 6: 记录待授权验证命令**

```bash
uv run pytest backend/tests/test_evidence_gate.py -q
```

预期：所有门禁矩阵和单候选回归用例通过。

---

### Task 5: 重构 `fact_lookup_v1` 执行链

**Files:**
- Modify: `backend/app/modular_rag.py`（Profile 模块顺序与 Registry）
- Modify: `backend/app/service.py`（`RAGService.query`）
- Modify: `backend/tests/test_retrieval_access.py`
- Modify: `backend/tests/test_modular_rag.py`

**Interfaces:**
- Consumes: Task 3 路由、Task 4 Evidence Gate、现有 `SearXNGWebSearchProvider.search()`。
- Produces: 一轮 KB 检索、Preliminary Gate、最多一次 Web、统一精排、Final Gate；`retrieval.web_policy` 稳定决策指标。

- [ ] **Step 1: 添加服务链调用次数测试**

使用 Fake Embedder/Reranker/Web Provider 记录调用次数，覆盖：

```python
assert kb_retrieval_calls == 1
assert web_provider.calls == 0  # KB 足够的普通问题
assert web_provider.calls == 1  # KB 不足或 freshness
```

显式过滤条件存在时断言 Web 为 0；`shadow` 策略且 Web 开启时断言 Web 仍按门禁执行。

- [ ] **Step 2: 更新 `fact_lookup_v1` 模块顺序**

Profile 固定为：

```python
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
)
```

Registry 注册新增模块。其他未来 Profile 不得通过公开 API 返回。

- [ ] **Step 3: 删除第二轮 KB 补检**

移除 `supplemental_candidate_count`、`_supplemental_query()` 和 `_merge_candidates()` 的 fact_lookup 使用路径。保留 `build_query_plan()` 产生的原始查询与最多 3 个受控扩展，并将它们视为同一轮检索计划。

- [ ] **Step 4: 将 KB 精排提前到 Web 决策之前**

先对 KB candidates 调用 Reranker 和 `rank_candidates()`，再把原始 `rerank_score` 交给 Preliminary Gate。不要用 `rank_candidates` 内部 Min-Max 结果做门禁。

- [ ] **Step 5: 实现稳定 Web 决策轨迹**

`retrieval.web_policy` 的 trace metrics 必须包含：

```python
{
    "decision": "disabled|not_needed|scope_limited|provider_unavailable|executed|no_result|failed",
    "result_count": int,
    "reason_code": str | None,
}
```

去掉 `differential_enabled` 对 fact_lookup Web 的限制。过滤范围、开关、Provider 与门禁结果按固定优先级产生唯一 decision。Provider 是否可用必须检查 `self.web_provider` 及其非空 `base_url`，不能只检查对象是否存在。

- [ ] **Step 6: Web 后统一精排并执行 Final Gate**

Web 触发时合并 KB/Web candidates，再统一调用 Reranker；不触发时可复用 KB 精排结果。Final Gate 的 `selected` 是唯一可进入 Prompt 和 Sources 的证据集合。`evidence.final_gate` trace metrics 记录 `selected_count/kb_count/web_count`；Web 模块只记录搜索结果数，不在统一精排前猜测采用数量。

- [ ] **Step 7: 发出两段门禁事件**

新增 `preliminary_gate_completed`；扩展 `evidence_gate_completed`：

```python
{
    "outcome": gate.outcome,
    "sufficient": gate.sufficient,
    "evidence_count": len(gate.selected),
    "kb_count": gate.kb_count,
    "web_count": gate.web_count,
    "requires_freshness": routing.requires_freshness,
    "freshness_verified": gate.freshness_verified,
    "reason_codes": list(gate.reason_codes),
}
```

- [ ] **Step 8: 记录待授权验证命令**

```bash
uv run pytest backend/tests/test_modular_rag.py backend/tests/test_retrieval_access.py -q
```

预期：调用次数、过滤边界、shadow 不阻断 Web 和门禁事件全部通过。

---

### Task 6: 完成回答状态、时效降级和持久化兼容

**Files:**
- Modify: `backend/app/prompts.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/service.py`
- Modify: `backend/app/main.py`（SSE 终态和历史保存）
- Modify: `backend/app/postgres_history.py`（状态读取/统计兼容）
- Modify: `backend/app/history.py`（本地历史记录兼容）
- Modify: `backend/app/postgres_evaluation.py`（质量聚合排除 direct response）
- Modify: `backend/tests/test_modular_rag.py`
- Modify: `backend/tests/test_postgres_history.py`

**Interfaces:**
- Consumes: Task 5 Final Gate outcome。
- Produces: `answered_stale`、`direct_response`、固定拒答和可审计的 Gate/Web 结果。

- [ ] **Step 1: 添加结果矩阵测试**

覆盖：普通 pass → `answered`；普通 reject → `insufficient_evidence` 且 Generator 0 次；时效 pass → `answered`；时效 stale → `answered_stale`；无 KB anchor → `insufficient_evidence`；social → `direct_response`。

- [ ] **Step 2: 扩展回答状态类型**

后端所有 Query/History/Prompt 状态 union 增加：

```python
"answered_stale"
"direct_response"
```

更新 `main.py` 的会话记录成功判定，使 `answered_stale` 与 `direct_response` 保存为成功终态。评测成功率统计把 `answered_stale` 作为“有答案但存在时效风险”单独计数，不并入普通 `answered`；`direct_response` 不进入 RAG 质量分母。没有新增聚合响应字段时，通过原始 answer status 和执行详情统计 stale，不伪装成普通 answered。

- [ ] **Step 3: 增加 stale Prompt 模式**

扩展 `build_prompt(..., freshness_unverified: bool = False)`。为 stale 模式加入：

```text
未取得可信的当前 Web 证据。只能陈述知识库中的历史事实；
答案开头必须明确写“时效未验证”；不得使用“当前、最新、截至今日”等确定性措辞。
```

解析后由服务层把通过引用校验的 `answered` 转为 `answered_stale`；模型不得自行决定是否 stale。

- [ ] **Step 4: 固定拒答与生成调用边界**

Final Gate `reject` 直接返回固定 `INSUFFICIENT_ANSWER`，不调用 Generator。`pass/stale` 才构建 Prompt。生成或引用校验失败继续使用现有 `retrieval_only/generation_failed`，不得伪装成成功。

- [ ] **Step 5: 保存完整执行事实**

确认 `answer_records.answer_status`、`query_executions.policy_snapshot`、`module_executions.metrics` 和 `query_evidence.source_type` 保存新状态与决策。读取旧记录时字段缺失使用兼容默认，不回写历史数据。

- [ ] **Step 6: 更新 SSE 完成条件**

`done` 事件允许 `answered_stale` 与 `direct_response`；social 不发送 retrieval/rerank/generation 阶段事件。stale 仍发送 `generation_verified`。

- [ ] **Step 7: 记录待授权验证命令**

```bash
uv run pytest backend/tests/test_modular_rag.py backend/tests/test_postgres_history.py -q
```

预期：状态矩阵、Generator 调用边界、SSE 顺序和历史兼容全部通过。

---

### Task 7: 完成问答页面和真实执行状态展示

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/components/AnswerPanel.tsx`
- Modify: `frontend/src/components/SourceCard.tsx`
- Modify: `frontend/src/components/TechnicalDrawer.tsx`
- Modify: `frontend/src/components/ChatPage.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: Task 5/6 的 answer status、routing、module trace 和流式事件。
- Produces: 用户可见的 `answered_stale/direct_response`、KB/Web 引用区分和准确 Web 状态。

- [ ] **Step 1: 添加回答状态测试**

增加断言：

```tsx
expect(screen.getByText("时效未验证")).toBeInTheDocument();
expect(screen.getByText(/未取得可信的当前 Web 证据/)).toBeInTheDocument();
```

`direct_response` 场景断言没有“引用证据”“查询性能”和技术抽屉。

- [ ] **Step 2: 更新前端类型和 SSE union**

`QueryResult.answer_status` 增加 `answered_stale | direct_response`；Routing 增加 `greeting/social/requires_freshness` 并保留可选 `requires_web`；SSE union 增加 `preliminary_gate_completed` 和扩展后的 gate payload。

- [ ] **Step 3: 更新 AnswerPanel**

状态映射：

```ts
answered_stale: "时效未验证"
direct_response: "对话回复"
```

stale 使用 warning 语义色；direct response 使用普通正文样式且不展示 Metrics、Sources、TechnicalDrawer。

- [ ] **Step 4: 区分 KB 与 Web 引用**

按 `evidence_source_type` 分组。KB 卡片保留文档/段落定位；Web 卡片显示域名、标题、抓取时间和外链。不得把 Web URL 显示为知识库文件名。

- [ ] **Step 5: 用模块轨迹计算 Web 状态**

删除 `rolloutStage`。从 `module_executions` 中找到 `retrieval.web_policy`，读取 `metrics.decision/result_count/reason_code`；从 `evidence.final_gate` 读取最终 `web_count`，映射为：

```text
disabled → Web 未启用
not_needed → KB 证据已满足，未触发 Web
scope_limited → 当前检索范围禁止 Web
provider_unavailable → Web Provider 未配置
failed → Web 搜索失败
no_result → Web 未找到合格结果
executed + web_count=0 → 已检索，结果未被采用
executed + web_count>0 → 已采用 N 条 Web 证据
```

旧记录无 trace 时显示“历史记录未保存 Web 执行状态”，不能再通过 Web source count 猜测。

- [ ] **Step 6: 更新流式阶段文案**

`routing_completed` 对 greeting 显示“已识别为问候”；Preliminary Gate 显示“正在判断是否需要补充 Web 证据”；Web 事件显示真实结果；social 直接落为最终消息。

- [ ] **Step 7: 记录待授权验证命令**

```bash
cd frontend && npm test -- src/App.test.tsx
```

预期：两种新状态、两类引用、真实 Web 决策和旧记录兼容测试通过。

---

### Task 8: 补齐闭环级回归用例与交付检查

**Files:**
- Modify: `backend/tests/test_modular_rag.py`
- Modify: `backend/tests/test_retrieval_access.py`
- Modify: `backend/tests/test_postgres_history.py`
- Modify: `frontend/src/App.test.tsx`
- Modify: `README.md`（仅更新真实可用的 RAG Policy 与 fact_lookup 说明）

**Interfaces:**
- Consumes: Tasks 1–7 的最终接口。
- Produces: 五个验收场景的可追溯测试代码与本地运行说明。

- [ ] **Step 1: 编写五个业务验收场景**

覆盖并使用固定 Fake 依赖：

1. “索引版本是什么？”：KB 足够，Web 0 次，`answered`；
2. “截至目前最新的索引版本是什么？”：Web 失败、KB 有历史证据，`answered_stale`；
3. 指定文档过滤：Web 0 次；
4. KB 只有无关内容、Web 有答案：缺少 KB anchor，`insufficient_evidence`；
5. “你好，在吗”：检索、Reranker、Web、Generator 均 0 次，`direct_response`。

- [ ] **Step 2: 增加执行轨迹完整性断言**

对事实查询断言模块顺序严格匹配 `fact_lookup_v1` 的实际执行路径；被跳过模块保留 `skipped` trace，不能从轨迹中消失。断言 Preliminary Gate、Web decision、Final Gate 的 reason codes 可在查询执行详情读取。

- [ ] **Step 3: 更新 README 的真实能力说明**

只写已经实现的能力：

```text
RAG Policy 当前只允许配置受控 Web 补检开关和可信域名。
首个完整 Modular RAG Profile 为 fact_lookup_v1。
summarize、compare、procedure 尚未作为完整 Profile 对外发布。
```

删除或改写任何把 Shadow/Canary/Full 描述为当前可操作发布功能的文字。

- [ ] **Step 4: 执行静态交付检查**

此步骤不运行测试，仅检查改动范围：

```bash
git diff --check
git status --short
rg -n "发布阶段|意图置信度|最少证据|Web 结果上限" frontend/src/components/KnowledgeBaseDetailPage.tsx
```

预期：无空白错误；工作区只包含用户原有改动和本计划范围内文件；RAG Policy 弹框无四个误导字段。

- [ ] **Step 5: 用户授权后执行轻量验证**

仅在用户明确回复“轻量验证”后运行：

```bash
uv run pytest \
  backend/tests/test_rag_policy.py \
  backend/tests/test_evidence_gate.py \
  backend/tests/test_modular_rag.py \
  backend/tests/test_web_retrieval.py \
  backend/tests/test_postgres_history.py \
  backend/tests/test_retrieval_access.py -q
cd frontend && npm test -- src/App.test.tsx
cd frontend && npm run lint
cd frontend && npm run typecheck
```

预期：全部通过。若失败，按首个根因逐项修复，不扩大范围。

- [ ] **Step 6: 用户授权后执行完整验证**

只有用户明确回复“完整验证”时，再运行生产构建和真实页面桌面/移动端检查；否则在交付说明中明确标注“未执行”。

---

## Completion Criteria

- RAG Policy 页面只保留 Web 开关和可信域名。
- 未配置 SearXNG 时不能把策略保存成 Web 已开启。
- `rollout_stage` 不再阻断 `fact_lookup`，也不在页面和技术抽屉展示。
- 公开 Profile 接口只返回 `fact_lookup_v1`。
- 纯问候不执行 RAG；问候加业务问题仍进入业务路由。
- 普通事实问题遵循 KB 足够即回答、KB 不足才补 Web。
- 时效问题 Web 失败时，有 KB 历史证据返回 `answered_stale`，无 KB anchor 则拒答。
- Web-only 永不返回确定性答案。
- 技术抽屉准确区分 Web 的七种执行状态。
- 所有新状态、门禁结果和降级原因可从会话记录和执行详情复核。
- 不 Commit、不 Push；自动化验证是否执行以用户后续授权为准。
