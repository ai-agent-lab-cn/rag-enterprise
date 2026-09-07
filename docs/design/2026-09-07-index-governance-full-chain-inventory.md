# RAG 索引治理全链路：现状梳理、分层边界与缺口清单

日期：2026-09-07  
分支：`main`  
数据库现场：Schema V36  
工作性质：只读梳理，不实施业务功能、不修改数据库、不运行测试

## 1. 本轮目标

依据已确认的索引治理生产链，对当前仓库、数据库、API 与前端页面进行一次事实盘点，回答四个问题：

1. 目标链路中的每个领域对象，目前是否真实存在、由谁写入。
2. `Index Version`、`Index Build`、`Validation Report` 与通用任务状态是否已经分开。
3. 数据同步 Pipeline 与索引治理的职责边界在哪里。
4. 后续实施应先修哪些断点，哪些能力属于 P1 / P2。

本轮不把设计稿中的按钮或状态视为已实现功能。

## 2. 结论先行

当前实现已经具备索引治理的主要数据库骨架，但尚未形成可由页面完整操作的生产闭环。

| 结论 | 当前判断 |
| --- | --- |
| 领域实体 | `Index Version`、`Index Build`、`Validation Report`、`Document Snapshot`、`Document Index State`、生命周期事件已存在 |
| Index Definition | 持久化表已在 V26 删除；当前只有“全局设置 + 请求参数”组成的隐式有效配置 |
| 状态机 | Version 主状态基本齐全，但 Build、Operation 与 Version 的收口存在真实矛盾 |
| 构建 | 支持一个 Version 多次 Build attempt；页面尚无重试入口 |
| 验证 | 后端已有三层结构，但校验项未覆盖目标图中的 ACL、Citation、Metadata、Recall@10、nDCG@10 等项目 |
| 激活 | 核心数据库切换已是原子事务；API 和页面仍把“验证 + 激活”耦合成一次操作 |
| 回滚 | 已实现 `previous → active`；原 active 回到 `ready`，便于再次激活 |
| 退役 / 清理 | 新版本激活时自动退役旧 previous；清理函数与页面仍使用已废弃的 `failed` 状态，失败版本实际无法清理 |
| 前端入口 | 只有一个硬编码 `500 / 50` 的“重建索引”按钮，没有创建场景、配置差异、文档范围预览与确认向导 |
| 当前线上数据 | 存在 `Version=validating / Build=failed / Document lanes=ready / Operation=ready` 的跨表矛盾 |

因此，下一步不应继续堆页面，而应按顺序完成：状态收口一致性 → 验证与激活解耦 → 创建版本向导 → 完整门禁 → 运维治理。

## 3. 目标分层

### 3.1 三层职责

```text
Data Sync Pipeline（数据同步）
  Data Source → Sync Run → 发现差异 → 拉取 / 规范化
  → Document / Document Revision / Tombstone
  → 输出“可索引变更”

Document Processing & Index Execution（资料处理与索引执行）
  Parse → Chunk → Embed → Vector / Keyword / Metadata 写入
  → 输出 Index Build 与 Document Index State

Index Governance（索引治理）
  Index Definition → Config Snapshot → Index Version
  → Build → Validate → Activate / Rollback → Retire → Cleanup
```

### 3.2 边界规则

| 层 | 应负责 | 不应负责 |
| --- | --- | --- |
| Data Sync | 连接数据源、游标、水位、变更发现、Document Revision、删除事实、失败重试 | 直接写 `Index Version.status`、决定是否激活、生成质量放行结论 |
| Processing / Build | 按冻结配置处理明确的文档集合，记录逐文档执行结果与构建尝试 | 移动线上 active 指针、把“构建成功”解释成“可以上线” |
| Index Governance | 冻结配置、创建版本、收口构建、执行门禁、原子切换、回滚、退役与清理 | 承担连接器同步游标、远端资源重试与内容发现 |
| Retrieval Runtime | 一次请求只解析一次 active Version，并让 Vector / Keyword / Metadata 使用同一版本 | 在检索途中重新读取 active 指针，混用两个版本 |

### 3.3 当前仍需确认的核心语义

当前代码同时存在两种理解：

- 重建候选版本会冻结 `Document Snapshot`，强调构建可复现。
- 日常上传与数据同步会直接把新分块写入 active Version，意味着 active 内容集合会继续变化。

后续实施前必须固定一种口径：

| 方案 | Version 含义 | 优点 | 代价 |
| --- | --- | --- | --- |
| A. 配置代际（建议延续当前产品口径） | Version 冻结 Parser / Chunking / Embedding 等配置；Document Snapshot 只冻结一次全量 Build 的输入 | 日常同步无需每次发布新版本，符合“配置变化或损坏时重建”的现有业务场景 | previous 可能落后于最新内容；回滚前需提示数据时间点，或通过变更日志补齐 |
| B. 内容发布版本 | Version 同时冻结配置与完整语料集合，active 永不原地变化 | 可严格复现与精确回滚 | 每次同步都要产生候选版本并验证、激活，复杂度与成本显著提高 |

本清单按方案 A 继续梳理，但将“previous 内容时间点提示 / 差异回放”列为 P1。若改选方案 B，需要单独重写数据同步与发布编排，不应混入页面向导任务。

## 4. 目标生产链与当前映射

```text
Index Definition
  └─ Snapshot
      └─ Index Version: building
          ├─ Build failed → build_failed
          │    ├─ 查看日志
          │    └─ Retry Build → building（同 Version，新 attempt）
          └─ Build succeeded → validating
               ├─ Validate failed → validation_failed
               │    ├─ 查看报告
               │    ├─ Revalidate → validating
               │    └─ Rebuild → building 或新 Version
               └─ Validate passed → ready
                    └─ Activate → active
                         └─ 下一版本激活 → previous
                              ├─ Rollback → active
                              └─ Retire → retired
                                   └─ Cleanup → cleaned
```

| 目标节点 | 当前载体 | 完成度 | 主要缺口 |
| --- | --- | --- | --- |
| Index Definition | 无持久化实体；`Settings`、`index_settings`、Build 请求参数共同形成有效配置 | 部分 | 无统一读取 API、无字段来源说明、无配置差异预览 |
| Config Snapshot | `index_versions` 的配置字段与 `config_fingerprint` | 部分 | 没有完整 `config_snapshot`；Keyword / Metadata / ACL / Citation / Reranker 未形成显式版本清单 |
| Document Snapshot | `document_snapshots`、`document_snapshot_members` | 已实现 | API 与页面不可见；排除原因未进入创建向导 |
| Index Version | `index_versions` | 基本实现 | 创建即 `building`；没有显式创建 API；生命周期由 Build API 隐式创建 |
| Index Build | `index_builds` + `operations` | 部分 | attempt 已支持；成功收口会被错误改成 `failed`；状态域仍混有 Validate / Activate 语义 |
| Document Index State | `document_index_states` | 部分 | 三路字段存在，但并非三个独立物理构建任务；页面只显示 overall 状态 |
| Validation Report | `validation_reports` | 部分 | 可持久化且不可覆盖；门禁项目不完整；页面没有执行验证入口 |
| Lifecycle Event | `index_lifecycle_events` | 已实现基础 | 创建、构建、验证、激活、回滚、退役、清理可留痕；前端只在详情中展示 |
| Activate | `switch_to_version()` | 核心已实现 | 路由仍调用 `activate_with_report()`，导致激活时重新验证 |
| Rollback | `rollback_to_previous()` | 已实现 | 缺风险确认、内容时间点与差异提示 |
| Retire | 下一版本激活时自动执行 | 部分 | 无显式 Retire API / 页面动作 |
| Cleanup | `cleanup_version()` | 部分 | 只接受 `retired / failed`，但 Version 已无 `failed` 状态；页面同样判断错误 |

## 5. 领域模型现状

### 5.1 Index Definition（索引定义）

V24 曾创建 `index_definitions`，V26 已删除。删除原因成立：它当时由 Version 反向派生，多个字段是硬编码值，并不驱动构建。

当前真正的配置来源：

- Chunking：Build 请求中的 `chunk_size / chunk_overlap`。
- Parser：代码内 `PARSER_SCHEMA_VERSION` 与具体解析器版本。
- Embedding：全局 `index_settings` 单例。
- Processing options：Version 中的 JSON。
- Keyword / Metadata / ACL / Citation / Reranker：存在实现行为，但未组成统一 Definition。

目标建议：

1. P0 先把 Index Definition 定义为“有效配置只读聚合”，通过 API 汇总真实配置源，不急于重新建表。
2. 只有在确认需要 per-KB 独立编辑配置后，再增加持久化 `index_definitions`，并确保 Build 只能从它生成 Snapshot。
3. 禁止再次创建由 Version 反向填充的装饰表。

### 5.2 Index Version（索引版本）

当前字段已包含：

- `knowledge_base_id`
- `status`
- `chunking_version`
- `parser_version`
- `embedding_model / embedding_dimension`
- `processing_options`
- `config_fingerprint`
- `document_snapshot_id`
- `validation_report_id`
- `rebuild_batch_id`
- `activated_at / retired_at / cleaned_at`

当前优点：

- active、building、previous 均有知识库级 partial unique 约束。
- Candidate 构建不会进入线上检索。
- 配置指纹参与检索质量报告匹配。
- 生命周期转换可写事件。

当前缺口：

- 没有显式 `version_no` 与可读名称 / 创建原因。
- API 响应未返回 `document_snapshot_id`、`validation_report_id`、`cleaned_at` 等完整治理字段。
- 没有显式 `config_snapshot`，当前字段不足以描述 Keyword、Metadata、ACL、Citation 与 Reranker 结构版本。
- Version 由 `POST /index-builds` 隐式创建，页面无法先预览后确认。

### 5.3 Index Build（索引构建）

当前已支持：

- 一个 Version 多次 attempt。
- 同一 Version 进行中的 attempt 可幂等复用。
- 终态后重试会递增 `attempt_no`。
- 每次 attempt 拥有独立 `operation_id`。

当前严重异常：

`aggregate_index_build()` 在文档全部成功后，先把 Build 置为 `ready`，再调用 `finalize_building_version()`。后者正确地把 Version 从 `building` 推到 `validating`；随后聚合函数却使用：

```text
Version == ready  → Build = ready
其他任何状态      → Build = failed
```

因此正常的 `Version=validating` 会被错误解释成 `Build=failed`。

目标状态应彻底分开：

```text
Index Build: queued → building → succeeded | partial_failed | failed | cancelled
Index Version: building → validating | build_failed
```

Build 成功后即终止，不应等到 Validate 或 Activate 才变成 `succeeded`。

### 5.4 Validation Report（验证报告）

当前已实现：

- 一个 Version 可保留多份历史报告。
- 最新验证结论绑定到 `index_versions.validation_report_id`。
- 通过后 Version → `ready`；失败后 → `validation_failed`。
- Activate 会再次核验报告存在、状态为 pass、且属于目标 Version。

缺口见第 7 节。

### 5.5 Document Index State（逐文档索引状态）

当前字段：

- `vector_status`
- `keyword_status`
- `metadata_status`
- `overall_status`
- `chunk_count`
- `failure_stage / failure_code / failure_reason`

代码会按 parsing、chunking、vector、keyword、metadata、validating 写阶段状态，但三点需要明确：

1. Vector、Keyword、Metadata 并不是三套独立持久化任务。
2. Keyword 使用按 `knowledge_base_id + index_version_id` 懒加载的词法缓存，来源仍是同一批 chunks。
3. Metadata 直接存储在 chunk JSON 中，和向量分块在同一事务写入。

所以页面可以展示“同一构建中的组件状态”，但不能把它描述为三个可独立重试、独立发布的 Build。

## 6. 统一状态机

### 6.1 Index Version 状态

| 英文值 | 中文 | 进入条件 | 可执行动作 |
| --- | --- | --- | --- |
| `building` | 构建中 | 创建 Version 并启动首个 Build，或对同一快照重试构建 | 查看进度、取消 |
| `build_failed` | 构建失败 | Build 失败、取消或覆盖不完整 | 查看日志、重试构建、使用新配置创建新 Version、清理 |
| `validating` | 验证中 | Build 已成功 | 执行验证、查看门禁要求、重新构建 |
| `validation_failed` | 验证失败 | 任一 critical 门禁未通过 | 查看报告、重新验证、重新构建、清理 |
| `ready` | 待激活 | 三层门禁通过并绑定 pass 报告 | 激活、重新验证、清理前需先退役 / 作废 |
| `active` | 当前生效 | 原子激活完成 | 查看、创建下一版本 |
| `previous` | 上一生效版本 | 下一版本激活时由旧 active 进入 | 回滚、退役 |
| `retired` | 已退役 | 更老 previous 自动退役，或操作者放弃回滚点 | 清理 |
| `cleaned` | 已清理 | 物理 chunks 与部分索引已删除 | 查看历史 |

说明：`draft（草稿）`建议只作为页面向导的本地状态。操作者最终确认之前不创建数据库 Version；确认后在一个事务中生成配置快照、文档快照、Version 与首个 Build，直接进入 `building`。

### 6.2 Index Build 状态

建议收窄为：

```text
queued → building → succeeded
                  ├→ partial_failed
                  ├→ failed
                  └→ cancelled
```

`validating / ready / activating` 不属于 Build，它们属于 Version 或独立动作。

### 6.3 Validation Report 状态

```text
pending → running → pass | failed | cancelled
```

每次重新验证新建报告，不覆盖旧报告。

### 6.4 Rollback 的状态规则

当前实现采用：

```text
previous → active
active   → ready
```

建议保留。原 active 已经通过门禁，回到 `ready` 比回到 `previous` 更准确；操作者需要恢复它时可执行普通 Activate。数据库继续保证只有一个 previous。

## 7. 三层 Validate 门禁现状

### 7.1 完整性校验 Integrity

| 目标检查 | 当前状态 |
| --- | --- |
| Document coverage | 已实现：按 Document Snapshot 逐成员核对 |
| `missing_document` | 已实现 |
| `orphan_chunk` | 已实现 |
| `duplicate_chunk` | 已实现 |
| 非空覆盖 | 已实现 |
| Chunk consistency | 部分：只查重复与孤儿；未查 chunk index 连续性、chunk 数异常、内容哈希 |
| ACL consistency | 未实现为 Integrity 门禁 |

### 7.2 技术校验 Technical

| 目标检查 | 当前状态 |
| --- | --- |
| Vector dimension | 已实现：实际向量维度与 Version 声明比对 |
| Version ownership | 已实现：阻止跨知识库混写 |
| Required fields | 已实现 |
| Vector index health | 未实现：未核对版本级 HNSW 索引存在与可用性 |
| Keyword | 未实现正式健康检查 |
| Metadata schema | 未实现 |
| ACL structure / leakage | 未纳入本层门禁 |
| Citation structure | 未实现 |
| Parser / Chunk / Component Version | 未形成统一 manifest，也未执行一致性比对 |

### 7.3 检索质量校验 Retrieval Quality

当前放行只检查：

- `Recall@5` 是否相对基线回退。
- Vector MRR 是否相对基线回退。
- Rerank MRR 是否相对基线回退。
- 评测报告 `config_fingerprint` 是否匹配目标 Version。

目标图与当前差距：

| 指标 | 当前状态 |
| --- | --- |
| Recall@5 | 已进入门禁 |
| Recall@10 | 未进入正式报告模型 / 门禁 |
| MRR | 已有 Vector MRR 与 Rerank MRR |
| nDCG@10 | 未实现；当前评测仅有可选 `nDCG@5`，且未进入门禁 |
| ACL leak count | 评测报告可携带，但未进入此门禁 |
| Metadata filter accuracy | 评测报告可携带，但未进入此门禁 |

## 8. Vector / Keyword / Metadata 统一版本

### 8.1 已成立的部分

- chunks 全部带 `index_version_id`。
- Vector SQL 按显式 `index_version_id` 查询。
- Keyword 缓存按 `knowledge_base_id + index_version_id` 分区。
- Metadata 与 ACL 检索条件作用在同一 Version 的 chunks 上。
- 一次混合检索在入口解析一次 active Version，并向下传递给多路召回。

这意味着运行时已经基本做到“同一次请求不混用两个 Version”。

### 8.2 仍缺失的部分

- Version 没有 `component_manifest`，无法回答 Vector / Keyword / Metadata / ACL / Citation 分别用了哪个 schema 或实现版本。
- Keyword 是缓存派生物，没有独立的版本构建产物与健康证据。
- Metadata 与 Citation 只有数据字段，没有 schema 版本和校验报告。
- Activate 只检查 Validation Report，没有显式核对完整组件清单。

建议在 Version 的不可变配置快照中加入：

```json
{
  "parser_schema_version": "...",
  "chunking_policy_version": "...",
  "embedding_model": "...",
  "embedding_dimension": 0,
  "vector_index_schema_version": "...",
  "keyword_index_schema_version": "...",
  "metadata_schema_version": "...",
  "acl_schema_version": "...",
  "citation_schema_version": "...",
  "reranker_version": "..."
}
```

该 JSON 必须参与 `config_fingerprint`，不得只用于页面展示。

## 9. Activate 原子切换与 Rollback

### 9.1 已实现的原子切换

`switch_to_version()` 已在一个数据库事务内完成：

1. 锁定目标 ready Version。
2. 校验绑定的 Validation Report 为 pass 且属于目标 Version。
3. 旧 previous → retired。
4. 当前 active → previous。
5. 目标 ready → active。
6. 更新 `knowledge_bases.active_index_version_id`。
7. 写生命周期事件。

三条 Version UPDATE 的顺序受 partial unique index 约束，后续不可随意调整。

### 9.2 当前接口层问题

`PUT /index-versions/{id}/active` 仍要求传 `evaluation_report_id`，并调用 `activate_with_report()`：先重新验证，再激活。

这与状态机冲突：

- `validating → ready` 已经代表验证完成。
- `ready → active` 应只做发布决策与原子指针切换。
- 页面“验证并激活”让 `ready` 和单独验证 API 失去意义。

目标：Activate 不再接收评测报告 ID，只使用 Version 已绑定的 pass Validation Report。

### 9.3 Rollback 风险提示

后端切换是原子的，但页面目前只有一个直接按钮，缺少：

- 目标 previous 的版本、配置与激活时间。
- 与当前 active 的配置差异。
- 方案 A 下的内容时间点 / 文档差异提示。
- 二次确认与成功结果。

## 10. API 现状与目标补齐

### 10.1 当前已有 API

| 方法 | 路径 | 当前用途 |
| --- | --- | --- |
| GET | `/api/knowledge-bases/{kb}/index-versions` | 版本列表 |
| POST | `/api/knowledge-bases/{kb}/index-builds` | 隐式创建 Version + Snapshot + Build |
| GET | `/api/knowledge-bases/{kb}/index-builds` | Build 列表 |
| GET | `/api/knowledge-bases/{kb}/index-builds/{build}/documents` | 逐文档状态 |
| POST | `/api/knowledge-bases/{kb}/index-versions/{version}/validations` | 执行验证 |
| GET | `/api/knowledge-bases/{kb}/index-versions/{version}/validations` | 验证历史 |
| GET | `/api/knowledge-bases/{kb}/index-versions/{version}/events` | 生命周期事件 |
| PUT | `/api/knowledge-bases/{kb}/index-versions/{version}/active` | 当前为“重新验证 + 激活” |
| POST | `/api/knowledge-bases/{kb}/index-versions/rollback` | 回滚 previous |
| DELETE | `/api/knowledge-bases/{kb}/index-versions/{version}/content` | 清理物理内容 |

### 10.2 页面向导所需 API

| 优先级 | 建议 API | 用途 |
| --- | --- | --- |
| P0 | `GET /index-definition` | 返回当前有效配置、字段来源、active 配置与 drift |
| P0 | `POST /index-versions/preview` | 只读预览配置快照、文档纳入 / 排除、预计范围、触发原因；不落库 |
| P0 | `POST /index-versions` | 按 preview 的配置与文档集合原子创建 Version、Snapshot 与首个 Build |
| P0 | `POST /index-versions/{version}/builds` | 对同一 Version 新建 Build attempt |
| P0 | `POST /index-versions/{version}/validations` | 保留现有独立验证动作 |
| P0 | `PUT /index-versions/{version}/active` | 改为无评测报告参数，只激活 ready Version |
| P1 | `POST /index-versions/{version}/retire` | 显式放弃 previous 回滚点 |
| P1 | `GET /index-versions/{version}/diff` | 配置、文档范围、质量报告差异 |

Preview 不能只依赖客户端回传。提交时后端必须重新计算配置指纹与文档集合指纹；若与预览不一致，返回冲突并要求重新确认。

## 11. 前端入口与页面向导

### 11.1 当前页面

入口位于知识库详情页“版本”Tab：

- “重建索引”直接发送 `chunk_size=500 / chunk_overlap=50`。
- 页面会提示 active 配置 drift，但不能基于差异进入向导。
- Version 表只提供详情、ready 激活、部分状态清理。
- `createIndexVersionValidation()` 已在前端 API 层定义，但页面未调用。
- 激活弹窗要求重新选择正式评测报告，并显示“验证并激活”。
- Build 详情能展示 attempt 和逐文档 overall 状态。

### 11.2 创建索引版本向导

建议 5 步：

1. **创建原因**：配置已变更、完整性修复、质量优化、主动创建回滚版本、其他。
2. **配置差异**：当前 active 与目标 Definition 的 Parser、Chunking、Embedding、Keyword、Metadata、ACL、Citation 差异。
3. **文档范围**：纳入、排除、解析失败、无 current revision 的文档数量与明细。
4. **影响确认**：预计文档 / Chunk / Embedding 规模，是否已有 building Version，是否可回滚。
5. **提交构建**：后端原子创建 Snapshot、Version 与 Build，跳转到进度详情。

如果当前没有配置 drift，仍允许以“索引一致性修复 / 主动创建回滚版本”为原因创建；不能把入口只绑定在 drift 警告上。

### 11.3 Version 行动作矩阵

| 状态 | 页面主动作 | 次动作 |
| --- | --- | --- |
| building | 查看构建进度 | 取消 |
| build_failed | 重试构建 | 查看日志、清理 |
| validating | 执行验证 | 查看构建、重新构建 |
| validation_failed | 重新验证 | 查看报告、重新构建、清理 |
| ready | 激活 | 查看报告、查看差异 |
| active | 当前生效，无危险主按钮 | 创建新版本、查看详情 |
| previous | 回滚 | 退役、查看差异 |
| retired | 清理 | 查看历史 |
| cleaned | 无动作 | 查看历史 |

## 12. 当前数据库现场证据

2026-09-07 只读查询结果：

```text
Knowledge Base  kb_default
active          iv_6b39fd8c414942e0
active status   active

Candidate       iv_f093395248864904
Version status  validating
Snapshot        ds_695193e74f064a3ba815
Snapshot scope  included=1, excluded=0, completeness=complete

Build           ib_89a9915190594debb5be
attempt_no      1
Build status    failed
documents       total=1, succeeded=1, failed=0

Document lanes  vector=ready, keyword=ready, metadata=ready, overall=ready
Operation       status=ready, stage=validate, progress=100%
Lifecycle       created → building; build_succeeded: building → validating
Validation      candidate 暂无报告
```

判定：候选 Version 正常完成了构建，生命周期事件也记录为成功；Build 的 `failed` 是聚合收口逻辑错误，不是实际构建失败。

## 13. 已确认缺口与异常

### P0 阻断项

1. Build 成功后被错误写为 `failed`。
2. `cleanup_version()` 与前端仍判断已删除的 `failed`，应识别 `build_failed / validation_failed`。
3. 页面没有 `validating → Validate` 入口，用户会停在“验证中”。
4. Activate API 与页面重复执行验证，未做到 `ready → active` 单一职责。
5. Build API 隐式创建 Version，且前端硬编码 Chunking 参数，没有业务场景与确认向导。
6. Build / Operation 状态仍混入 Validate / Activate 语义，三层状态容易再次不一致。

### P1 能力缺口

1. Index Definition 有效配置聚合与字段来源说明。
2. 完整 `config_snapshot / component_manifest`。
3. ACL、Citation、Metadata、Keyword、组件版本技术门禁。
4. Recall@10、nDCG@10 与 ACL / Metadata 质量门禁。
5. 显式 Retire 与 previous 内容时间点 / 差异提示。
6. Version 对比页、报告差异、文档范围差异。
7. Legacy Version 的配置完整度与不可验证原因展示。

### P2 运维治理

1. 自动退役与清理策略、保留数量、最短保留期。
2. 大规模构建并发、限流、成本预估与可取消性。
3. 验证策略版本管理与历史兼容。
4. 构建 / 验证 / 激活的告警与可观测性。
5. 方案 A 下 previous 的内容差异回放或同步补齐机制。

## 14. 建议实施顺序（尚未启动）

后续收到明确实施指令后，固定按以下 6 步执行：

1. **状态收口**：修复 Build / Version / Operation 一致性，修正失败状态清理规则。
2. **动作解耦**：Validate 与 Activate 分开，补齐页面状态动作。
3. **创建入口**：有效 Index Definition、Preview API、创建 Version API 与五步向导。
4. **门禁补齐**：Integrity / Technical / Retrieval Quality 缺失检查与组件 manifest。
5. **生命周期完善**：Retry Build、Revalidate、Retire、Rollback 确认、Cleanup。
6. **验收与迁移**：Legacy 展示、API / DB / UI 测试、真实页面桌面与移动端验收。

在用户明确启动前，以上均为规划，不代表已实现。

## 15. 后续验收标准

### 领域与状态

- 每个状态只有一个明确领域所有者。
- Build 成功不等于 Validate 通过；Validate 通过不等于已经 Activate。
- 任一失败状态都能从页面看到原因与正确恢复动作。
- 数据库不存在 Version、Build、Operation 互相矛盾的终态组合。

### 创建向导

- 用户能说明“为什么创建新版本”。
- 用户在提交前看到真实配置差异与文档范围。
- Preview 后配置或文档集合变化时，后端拒绝使用过期确认。
- 不再从页面发送硬编码 Chunking 配置。

### Validate / Activate / Rollback

- `validating` 页面可执行验证。
- `validation_failed` 可查看报告、重新验证或重建。
- `ready` 激活不再重新选择评测报告。
- Activate 的 Version 状态、KB 指针与事件同事务完成。
- Rollback 明确展示目标版本及内容 / 配置差异。

### 版本一致性

- 单次检索的 Vector / Keyword / Metadata 全部使用同一 active Version。
- 组件 manifest 与实际产物一致并进入技术门禁。
- Cleanup 后物理资源消失、Version 保留为 `cleaned`，且不能被回滚或激活。

## 16. 本轮边界

- 未修改后端、前端、迁移、运行配置或数据库数据。
- 未运行测试、Lint、构建或容器重建。
- 未提交、未推送、未创建 Issue / PR。
- 当前工作区原有大量未提交改动，本轮不清理、不覆盖。

