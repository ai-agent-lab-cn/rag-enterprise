# Modular RAG：`fact_lookup` 完整闭环设计

## 1. 目标

本轮只交付 `fact_lookup（事实查找）` 的可运行闭环：

```text
用户问题
  → 问候旁路 / 事实查找路由
  → KB Hybrid Retrieval + Rerank
  → Preliminary Evidence Gate
  → 按需执行一次 Web Retrieval
  → KB + Web 统一 Rerank
  → Final Evidence Gate
  → 基于证据回答 / 时效未验证回答 / 证据不足拒答
```

目标是让系统能够明确回答四个问题：

- 当前请求是否应进入事实查找；
- KB 证据是否已经足够；
- 是否允许且有必要补充 Web 证据；
- 最终应回答、标记时效风险，还是拒答。

## 2. 本轮边界

### 2.1 包含

- `fact_lookup_v1` 固定管线；
- 常见问候语旁路，避免无意义检索；
- KB-first、一次 Web 补检；
- Preliminary / Final 两段确定性 Evidence Gate；
- 普通问题、时效问题和 Web 故障的稳定结果；
- KB / Web 引用区分、时效风险标记和执行轨迹；
- RAG Policy 页面只保留 Web 开关和可信域名；
- RAG Policy 写接口只允许修改当前真正生效的业务字段；
- Web Provider 未配置时禁止形成“页面已开启、运行时不可用”的假状态；
- 技术抽屉按实际模块轨迹区分 Web 未启用、未触发、范围受限、失败、无结果和已采用；
- 对应单元测试、服务测试和前端交互测试代码。

### 2.2 不包含

- `summarize`、`compare`、`procedure` 的差异化执行；
- Web-only 回答；
- 多次 Web 搜索、循环补检或 Agent 自主规划；
- 知识图谱、多模态、数据库检索和模型微调；
- 可视化拖拽编排；
- Shadow / Canary 流量控制、策略版本中心和管理员阈值调参；
- 新增数据库迁移或后端接口。

未实施的三类业务意图继续路由到现有兼容行为，本轮不宣称其已形成 Modular RAG 闭环。

## 3. 路由契约

### 3.1 类型

业务意图保留：

```text
fact_lookup / summarize / compare / procedure
```

交互意图新增：

```text
greeting
```

控制结果统一为：

```text
route / social / clarify / out_of_scope
```

路由修饰信息保留：

```text
requires_freshness / follow_up_rewritten
```

`requires_freshness` 替代含义不够准确的 `requires_web`：前者描述用户需求，后者是执行策略，二者不可混为一谈。

### 3.2 问候旁路

以下纯问候进入 `greeting + social`：

```text
你好 / 您好 / 嗨 / 哈喽 / Hello / Hi
早上好 / 下午好 / 晚上好
在吗 / 有人吗 / 能听到吗 / 方便吗
```

处理规则：

- 返回固定、简短的产品引导语；
- 不执行 Embedding、KB Retrieval、Web Retrieval、Evidence Gate 或生成模型；
- 保留会话与回答记录，便于多轮上下文完整展示；
- `pipeline_profile=null`，`answer_status=direct_response`。

问候与业务问题同时出现时，业务意图优先。例如“你好，索引版本是什么”进入 `fact_lookup`。

### 3.3 `fact_lookup`

规则命中或结构化分类结果为事实查找时：

```text
intent=fact_lookup
control_outcome=route
pipeline_profile=fact_lookup_v1
profile_version=1
```

完整问题在分类器不可用、超时或返回非法结构时安全降级到 `fact_lookup`；信息不足的问题进入 `clarify`，不盲目检索。

## 4. `fact_lookup_v1` 固定执行链

```text
query.normalize
  → query.expand
  → retrieval.knowledge_base
  → rerank.knowledge_base
  → evidence.preliminary_gate
  → retrieval.web_policy（条件执行，最多一次）
  → evidence.fuse
  → rerank.unified
  → evidence.final_gate
  → generation.fact
  → generation.verify
```

约束：

- KB 是事实源基础，Web 只能补充，不允许单独支撑回答；
- 单次请求最多执行一轮 KB 检索和一轮 Web 检索；
- 删除当前“KB 候选不足后再执行一次 KB 补检”的行为；
- 查询扩展仍属于同一轮 KB 检索计划，最多保留现有 3 个受控扩展，不视为循环补检；
- 所有 KB 证据必须来自当前 active 索引版本并重新执行 ACL 与检索状态校验；
- 显式文档、分类、标签或时间范围过滤时，Web 不得突破该范围。

## 5. Preliminary Evidence Gate

KB 候选完成精排后执行初步门禁。门禁是确定性代码，不调用 LLM。

### 5.1 检查项

| 检查 | 通过条件 |
|---|---|
| `kb_anchor` | 至少 1 条 KB 证据通过相关性与引用完整性检查 |
| `evidence_count` | 合格 KB 证据数达到系统内部 `minimum_evidence_count` |
| `relevance` | 至少 1 条证据达到当前 Reranker 对应的版本化阈值 |
| `citation_ready` | 文档、索引版本、Chunk 定位和展示名称齐全 |
| `access_valid` | active version、ACL、有效期和 retrieval status 均通过复核 |

### 5.2 相关性分数

门禁不得使用 `rank_candidates()` 当前的查询内 Min-Max 分数，因为单候选或同分候选会全部归零。采用代码内版本化的 Reranker 阈值表：

| Reranker | 分数语义 | 通过条件 |
|---|---|---|
| `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | CrossEncoder relevance logit | `score >= 0.0` |
| `demo/lexical-overlap-v1` | Query token overlap，范围 0–1 | `score > 0.0` |

阈值不进入管理员 UI。运行时遇到未登记分数语义的 Reranker，Profile 校验失败并返回稳定配置错误，不能跳过相关性门禁。

### 5.3 初步结论

| 场景 | 结论 |
|---|---|
| 普通问题，KB 通过 | 不搜 Web，进入 Final Gate |
| 普通问题，KB 未通过，Web 可用 | 执行一次 Web 补检 |
| 普通问题，KB 未通过，Web 不可用 | `insufficient_evidence` |
| 时效问题 | 无论 KB 是否通过，均尝试一次 Web 补检 |

## 6. Web Retrieval Policy

Web 只有在以下条件同时成立时才执行：

- `web_search_enabled=true`；
- 可信域名列表非空；
- Web Provider 可用；
- 当前请求没有限制在 KB 内部文档、分类、标签或时间范围；
- 普通问题的 KB 门禁未通过，或 `requires_freshness=true`。

`rollout_stage` 不再参与 `fact_lookup` 是否联网的运行判断。字段暂时保留兼容现有数据，但不在页面展示，也不阻止管理员已经启用的 Web 策略生效。

`canary` 与 `full` 当前没有请求级流量分配差异，只存在阶段记录和晋级校验，因此不能继续作为成熟发布能力展示。数据库字段、历史策略快照和旧响应字段暂时保留，运行时不得再用它阻断 `fact_lookup` 的 Web 补检。

Web 安全规则保持不变：仅 HTTPS、域名白名单、重定向后复核、阻止本机/内网/保留地址、单页大小与超时限制、内容按不可信数据处理。

Web 结果必须具备 URL、标题、抓取时间和内容哈希；缺少任一关键字段的结果不进入候选集。

管理员开启 Web Search 时，服务端必须确认 `SEARXNG_BASE_URL` 已配置且 URL 格式有效。未配置时返回 `WEB_SEARCH_PROVIDER_NOT_CONFIGURED`，策略保持原值。保存策略时不主动请求 SearXNG，避免外部服务瞬时故障阻止配置落库；实际不可用由查询执行轨迹记录。

## 7. Evidence Fusion 与 Final Gate

Web 返回后，将合格 KB 与 Web 候选统一去重、精排和编号。Final Gate 仍是确定性代码。

### 7.1 最终检查

| 检查 | 通过条件 |
|---|---|
| `kb_anchor` | 最终证据中至少 1 条合格 KB 证据 |
| `evidence_count` | 合格证据总数达到内部最低数量 |
| `relevance` | 被选证据全部达到相应 Reranker 阈值 |
| `citation_ready` | KB 与 Web 均具备各自完整引用字段 |
| `freshness` | 时效问题至少有 1 条合格 Web 证据，否则进入时效降级分支 |

### 7.2 结果矩阵

| 问题 | KB | Web | 最终结果 |
|---|---|---|---|
| 普通 | 通过 | 未触发 | `answered` |
| 普通 | 未通过 | 补检后满足且有 KB anchor | `answered` |
| 普通 | 未通过 | 关闭、失败或补检后仍不足 | `insufficient_evidence` |
| 时效 | 有历史证据 | 有合格 Web 证据 | `answered` |
| 时效 | 有历史证据 | 关闭、失败或无合格结果 | `answered_stale` |
| 时效 | 无合格 KB 证据 | 任意 | `insufficient_evidence` |

`answered_stale` 的答案必须：

- 在答案状态区显示“时效未验证”；
- 在正文开头明确说明未取得可信 Web 证据；
- 只陈述 KB 中可验证的历史事实；
- 禁止使用“当前、最新、截至今日”等确定性措辞；
- 保留 Web 未执行或失败原因到技术抽屉，不把技术错误直接暴露给普通用户。

## 8. 生成与引用校验

只有 Final Gate 输出 `answered` 或 `answered_stale` 才调用生成模型。

生成约束：

- 只允许使用 Final Gate 选中的证据；
- 每个可核验事实必须包含 `[来源 N]`；
- 引用编号必须存在且与证据一一对应；
- `answered_stale` 使用独立提示词约束时效措辞；
- 引用校验失败沿用现有受控降级，不把未校验答案标成成功。

`insufficient_evidence` 使用固定拒答文案，不调用生成模型。

## 9. API、流式事件与持久化

保持现有 Query API 路径兼容，扩展字段枚举：

```text
answer_status += answered_stale | direct_response
routing.intent += greeting
routing.control_outcome += social
routing.requires_web → routing.requires_freshness
```

为兼容旧客户端，一个过渡版本同时返回 `requires_web`，值等于 `requires_freshness`，并在后续版本删除旧字段。

流式事件顺序：

```text
routing_completed
module_started / module_completed
preliminary_gate_completed
web_retrieval_completed（仅触发时）
evidence_gate_completed
generation_verified（仅进入生成时）
done
```

`evidence_gate_completed` 增加：

```text
outcome / evidence_count / kb_count / web_count
requires_freshness / freshness_verified / reason_codes
```

现有 `query_executions`、`module_executions`、`query_evidence` 和 `answer_records` 足以保存本轮事实，不新增表。策略快照、active index version、Web 状态、两次门禁结论和降级原因必须可从执行详情复核。

### 9.1 RAG Policy 写契约

保持现有路径：

```text
GET /api/knowledge-bases/{id}/rag-policy
PUT /api/knowledge-bases/{id}/rag-policy
```

新的前端只提交：

```json
{
  "web_search_enabled": true,
  "allowed_domains": ["example.com"]
}
```

服务端处理规则：

- 先读取数据库当前策略；
- 只更新 `web_search_enabled` 和 `allowed_domains`；
- `rollout_stage`、`intent_confidence_threshold`、`minimum_evidence_count`、`max_web_results` 和 `profile_versions` 保持原值；
- 为兼容旧客户端，原 PUT 已支持的 `rollout_stage`、`intent_confidence_threshold`、`minimum_evidence_count` 和 `max_web_results` 仍可出现在请求中；值与当前值相同则接受，不同则返回 `RAG_POLICY_FIELD_READ_ONLY`，禁止静默忽略；`profile_versions` 从未是 PUT 字段，继续由服务端管理；
- GET 响应暂时保留完整旧结构，供历史页面与执行详情读取；
- 审计只记录真正可修改字段及变更前后摘要。

`GET /api/rag/pipeline-profiles` 首期只返回已经形成完整闭环的 `fact_lookup_v1`。其余 Profile 可以保留为代码内未来定义，但不能通过公开接口宣称为已可用能力。

### 9.2 Web 执行状态

`retrieval.web_policy` 的模块轨迹必须提供稳定 `decision`：

```text
disabled / not_needed / scope_limited / provider_unavailable /
executed / no_result / failed
```

Web 模块记录：

```text
result_count / reason_code
```

最终采用数量由 `evidence.final_gate` 记录 `selected_count / kb_count / web_count`，避免 Web 搜索模块在统一精排完成前猜测采用结果。

前端只依据该轨迹显示联网状态，不再通过“最终有没有 Web 来源”反推是否执行过 Web。

## 10. 页面闭环

### 10.1 问答区

- `answered`：显示“已基于证据回答”；
- `answered_stale`：显示橙色“时效未验证”及固定说明；
- `insufficient_evidence`：显示“证据不足”，不展示伪答案；
- `direct_response`：显示普通对话回复，不展示证据区和性能阶段；
- 引用按“知识库引用 / Web 引用”区分，Web 引用展示域名与抓取时间。

### 10.2 技术抽屉

展示：

- 路由意图、是否要求时效、Profile；
- active 索引版本；
- KB 候选数、初步门禁结论；
- Web 是否触发、跳过或失败及原因；
- 最终 KB / Web 证据数；
- Final Gate 结论与 reason codes；
- 模块时间线和降级原因。

### 10.3 RAG Policy

首期仅允许管理员配置：

- `Web Search` 开关；
- 可信域名列表。

页面隐藏发布阶段、意图阈值、最低证据数、最大 Web 数量和 Profile 版本；这些字段暂由服务端保留现值并统一管理，GET 响应与旧客户端读取保持兼容。

说明文案改为：

```text
控制当前知识库是否允许受控 Web 补检，并限定可访问的可信域名。
Web 只补充知识库证据，不会自动写入知识库或绕过知识库范围。
```

不再展示“发布阶段”或“受控模块编排已正式发布”等尚未成立的产品承诺。

## 11. 错误处理

| 错误 | 行为 |
|---|---|
| Router 分类器失败 | 完整问题降级到 `fact_lookup`，记录 `fallback_used` |
| KB 无 active version | 返回现有索引不可用错误，不尝试用 Web 绕过 |
| KB 无合格证据 | `insufficient_evidence`，Web 不能形成独立答案 |
| Web Provider 失败 | 普通问题拒答；时效问题有 KB 历史证据时 `answered_stale` |
| 开启 Web 但未配置 Provider | 策略保存失败并返回 `WEB_SEARCH_PROVIDER_NOT_CONFIGURED` |
| 旧客户端尝试修改内部策略字段 | 返回 `RAG_POLICY_FIELD_READ_ONLY`，不部分保存 |
| 未知 Reranker 分数语义 | `RAG_PROFILE_INCOMPATIBLE`，不跳过门禁 |
| 生成失败 | 沿用 `retrieval_only / generation_failed` 受控降级 |
| 引用校验失败 | 不返回 `answered` 或 `answered_stale` 成功状态 |

## 12. 现状差距与实施落点

| 现状 | 本轮修正 |
|---|---|
| Router 不识别问候 | 新增 `greeting + social` 旁路 |
| `requires_web` 混合了需求与策略 | 引入 `requires_freshness`，旧字段过渡兼容 |
| `shadow` 阻止已启用 Web 策略执行 | `fact_lookup` 改为直接遵循 Web 开关和可信域名 |
| `canary / full` 执行路径相同却作为成熟发布能力展示 | 页面和技术抽屉移除发布阶段，对外 Profile 只声明 `fact_lookup_v1` |
| “意图置信度”只影响 LLM 分类却看似控制全部路由 | 从管理员页面移除，保留为系统内部参数 |
| “最少证据”当前只统计候选数量 | 从管理员页面移除，由完整 Evidence Gate 接管 |
| “Web 结果上限”属于系统保护参数 | 从管理员页面移除，继续使用内部上限 |
| 页面允许在 Provider 未配置时开启 Web | 保存时校验 Provider 配置，失败不落库 |
| 技术抽屉用最终来源反推联网状态 | 改为读取 `retrieval.web_policy` 的稳定决策与结果指标 |
| PUT 依赖前端回传全部内部参数 | 服务端读取并保留内部字段，仅更新 Web 开关和可信域名 |
| 候选不足会执行第二轮 KB 补检 | 删除第二轮 KB 补检，最多一轮 KB + 一轮 Web |
| Web 在 KB 精排前决定是否触发 | 增加 KB 精排后的 Preliminary Gate |
| Final Gate 只检查数量和强制 Web | 增加 KB anchor、相关性、引用完整性、访问有效性和时效检查 |
| 时效 Web 失败只能拒答 | 有 KB 历史证据时返回 `answered_stale` |
| Query 内 Min-Max 分数不适合门禁 | 使用按 Reranker 版本登记的原始分数阈值 |
| Policy 页面暴露过多系统字段 | 只保留 Web 开关与可信域名 |

## 13. 测试与验收

实施时编写测试，但按照项目规则，本轮未获“轻量验证”或“完整验证”授权前不执行测试、Lint、类型检查或构建。

### 13.1 后端测试

- 纯问候走 `social`，不调用检索、Web、Reranker 和 Generator；
- 问候加事实问题进入 `fact_lookup`；
- KB 证据充足时不调用 Web；
- KB 证据不足且 Web 开启时只调用一次 Web；
- 显式 KB 过滤时不调用 Web；
- 普通问题 Web 失败返回 `insufficient_evidence`；
- 时效问题 Web 成功返回 `answered`；
- 时效问题 Web 失败且有 KB 证据返回 `answered_stale`；
- 时效问题没有 KB anchor 时，即使 Web 有结果也拒答；
- 引用字段不完整的候选不通过门禁；
- 未登记 Reranker 返回稳定配置错误；
- 单候选不会因 Min-Max 归零被错误拒绝；
- 每个请求最多一轮 KB 检索和一轮 Web 检索；
- 执行轨迹完整记录 Preliminary Gate、Web 决策和 Final Gate；
- Provider 未配置时不能开启 Web，且原策略不变；
- 旧客户端提交相同内部字段仍兼容，尝试修改时稳定拒绝；
- Pipeline Profiles 接口只返回 `fact_lookup_v1`。

### 13.2 前端测试

- `answered_stale` 显示“时效未验证”和固定说明；
- `direct_response` 不展示证据与检索性能；
- KB / Web 引用分组和字段正确；
- 技术抽屉正确展示两段门禁与 Web 决策；
- 技术抽屉能区分 Web 未启用、未触发、范围受限、失败、无结果和已采用；
- RAG Policy 只显示 Web 开关和可信域名；
- 保存 RAG Policy 时不再回传隐藏的内部字段；
- 旧响应缺少新字段时页面仍可读取。

### 13.3 验收场景

1. “索引版本是什么？”：KB 足够，直接回答并引用 KB，不联网。
2. “截至目前最新的索引版本是什么？”：必须尝试 Web；Web 失败但 KB 有历史资料时回答并标记“时效未验证”。
3. “查找指定文档中的索引配置”：保持 KB 范围，不联网。
4. KB 只有无关内容：即使 Web 搜到答案，也因缺少 KB anchor 拒答。
5. “你好，在吗”：固定回复，不产生检索与生成模块执行。

## 14. 实施顺序

1. 先收口 RAG Policy 页面和公开 Profile，只展示已成立的能力；
2. 修正策略写契约、Provider 配置校验和兼容字段保护；
3. 路由契约、问候旁路与 Reranker 分数语义；
4. `fact_lookup_v1` 一轮 KB 检索、Preliminary Gate 与一次 Web 补检；
5. Final Gate、`answered_stale`、生成约束和真实 Web 状态轨迹；
6. API、持久化与流式事件兼容；
7. 问答区与技术抽屉状态展示；
8. 补齐测试代码与验收清单。

本轮不 Commit、不 Push；代码完成后等待用户在 VS Code 检查并明确授权。
