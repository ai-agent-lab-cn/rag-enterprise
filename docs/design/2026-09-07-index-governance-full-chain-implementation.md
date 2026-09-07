# RAG 索引治理全链路实施记录

> 依据：`docs/design/2026-09-07-index-governance-full-chain-inventory.md`  
> 实施状态：代码、迁移、页面与测试用例已在本地工作区落地；尚未执行测试、Lint、类型检查、构建、数据库迁移或浏览器验收。  
> 数据库目标版本：V38。

## 1. 实施目标

把索引治理从“直接重建 + 验证并激活”的混合动作，改造成可解释、可审计、可恢复的生产链：

```text
Index Definition
  → Config Snapshot / Document Snapshot
  → Index Version: building
  → Index Build: succeeded | partial_failed | failed | cancelled
  → Index Version: validating
  → Validation Report: pass | failed
  → Index Version: ready
  → Activate: active / previous
  → Rollback | Retire
  → Cleanup: cleaned
```

实施同时固定三层边界：

| 层 | 职责 | 明确禁止 |
| --- | --- | --- |
| Data Sync Pipeline | 连接、游标、水位、变更发现、Document Revision、Tombstone、资源重试 | 直接决定 Version 是否发布 |
| Processing / Index Build | 按冻结文档集合执行 Parse、Chunk、Embed、Vector、Keyword、Metadata，记录 Build 与逐文档状态 | 移动 active 指针、把构建成功当成验证通过 |
| Index Governance | Definition、Snapshot、Version、Validate、Activate、Rollback、Retire、Cleanup | 承担连接器同步游标和远端内容发现 |

## 2. 领域对象与状态所有权

### 2.1 Index Definition

- 采用有效配置只读聚合，不恢复空壳 `index_definitions` 表。
- 汇总 Chunking、Parser、Embedding、Vector、Keyword、Metadata、ACL、Citation、Reranker 的当前真实来源。
- 返回 active 配置及 drift，页面不再硬编码 Chunk Size / Overlap。

### 2.2 Index Version

- 每个 Version 保存不可变 `config_snapshot`、`component_manifest`、`config_fingerprint`、`document_snapshot_id` 与 `release_fingerprint`。
- 增加知识库内递增 `version_no`、`creation_reason`、`force_reason`、`requested_by`、创建幂等键。
- 状态域只保留：`building / validating / ready / active / previous / retired / cleaned / build_failed / validation_failed`。
- `ready` 只能由三层 Validate 通过产生；Build 或 Activate 不得写入。

### 2.3 Index Build

- 一个 Version 可以有多个 Build attempt。
- Build 只描述构建执行：`queued / building / succeeded / partial_failed / failed / cancelled`。
- 验证和激活不再回写 Build。
- Retry 保留 Version、配置快照、文档快照和旧 attempt，新建独立 attempt。

### 2.4 Validation Report

- 每次验证创建不可变报告，不覆盖历史。
- 报告绑定 Version、Build、策略版本、评测集、基线 Version、三层结果及失败项。
- Version 只绑定最新验证报告；Activate 必须再次核对报告为 `pass` 且属于目标 Version。

### 2.5 Document Index State

- 每个 Build attempt 按文档记录 Vector / Keyword / Metadata 三路状态、Chunk 数、失败阶段与原因。
- 聚合状态不再把部分失败覆盖成普通失败。
- 取消后的迟到 Worker 不能把 Build/Operation 改回成功。

## 3. 状态转换规则

```text
building
  ├─ Build succeeded → validating
  └─ Build failed / cancelled → build_failed

validating
  ├─ Validate pass → ready
  └─ Validate failed → validation_failed

validation_failed
  ├─ Revalidate → ready | validation_failed
  └─ Retry Build → building

ready
  └─ Activate → active

active
  └─ 下一版本 Activate → previous

previous
  ├─ Rollback → active；原 active → ready
  └─ Retire → retired

retired | build_failed | validation_failed
  └─ Cleanup → cleaned
```

关键不变量：

1. Build 成功不等于 Validate 通过。
2. Validate 通过不等于已发布。
3. 同一知识库最多一个 `active`、一个 `previous`、一个候选 Version。
4. Version 状态、KB active 指针和生命周期事件在同一事务提交。
5. 候选创建、Activate、Rollback 先锁 Knowledge Base 行，按知识库串行。

## 4. 创建索引版本

页面“版本治理”Tab 增加五步向导：

1. 创建原因：首版、配置变化、文档集合变化、组件升级、一致性修复、主动创建回滚版本。
2. 配置差异：真实 Chunking、Parser、Embedding 与统一组件版本。
3. 文档范围：新增、移除、更新、未变化；解析失败和无 current revision 的排除明细。
4. 影响确认：文档数、Chunk 估算、Embedding 输入规模、并发容量、单次上限。
5. 提交构建：携带配置、文档集合与发布指纹及幂等键。

后端提交时重新计算所有指纹。Preview 后配置、文档集合或组件变化时返回冲突，不接受过期确认。

一致性修复和主动创建回滚版本没有自然差异，必须 `force=true` 且填写非空业务原因。

## 5. 三层 Validate 门禁

### 5.1 Integrity

- Document coverage
- `missing_document`
- `orphan_chunk`
- `duplicate_chunk`
- Chunk index continuity
- ACL consistency
- Non-empty coverage

### 5.2 Technical

- Vector dimension
- Knowledge Base / Version ownership
- Chunk required fields
- `component_manifest` 与运行组件版本一致
- Version 级 HNSW 索引存在
- Vector / Keyword / Metadata lane 健康
- Metadata schema
- ACL structure
- Citation structure

### 5.3 Retrieval Quality

- Recall@5、Recall@10
- Vector MRR、Rerank MRR
- nDCG@10
- Metadata filter accuracy
- ACL leak count
- 评测报告 `config_fingerprint` 与 Version 一致
- 相对当前 active 基线无回退

完整配置的新 Version 缺少 Recall@10、nDCG@10、Metadata 过滤或 ACL 泄漏证据时不能放行；Legacy Version 缺失这些历史证据时显示 warning，不伪装成通过。

## 6. Activate、Rollback、Retire、Cleanup

### 6.1 Activate 原子切换

Activate 只接受 `ready`，不接收评测报告参数、不重新执行 Validate：

1. 锁定 Knowledge Base 与目标 Version。
2. 核对绑定的持久化 Validation Report。
3. 旧 previous → retired。
4. 当前 active → previous。
5. 目标 ready → active。
6. 更新 `knowledge_bases.active_index_version_id`。
7. 同事务写生命周期事件。

### 6.2 Rollback

- 目标必须为唯一 `previous`，且物理 Chunks 尚未清理。
- 页面先读取配置差异、冻结文档差异、当前内容差异、可检索文档/Chunk 数及两侧验证报告。
- previous 与当前资料集合不一致时，API 默认拒绝；页面只有在展示差异后才显式传入内容时间点确认。
- 回滚执行 `previous → active`、原 `active → ready`，不重跑验证。
- 方案 A 不在旧 Version 上隐式回放当前内容，避免在历史配置中混入不同 Embedding 或组件产物。内容补齐走“创建索引版本”向导：当前文档集合 drift 会自动形成 `document_snapshot_changed` 候选，完成 Build、Validate、Activate 后重新收敛。

### 6.3 Retire / Cleanup

- Retire 只接受 `previous`，表示明确放弃快速回滚点，不删除物理资源。
- Cleanup 只接受 `retired / build_failed / validation_failed`，删除 Version Chunks 和专属 HNSW，Version 记录保留并进入 `cleaned`。
- Retention Sweep 默认 dry-run；只选择超过最短保留期且超出每知识库保留数量的 retired Version。实际清理必须显式 `--apply`，未启用自动策略时还需 `--force`。

## 7. Vector / Keyword / Metadata 同版本

- 一次检索只解析一次 active Version ID，并贯穿 Vector、Keyword、Metadata、ACL 与 Citation 查询。
- Keyword 缓存以 `knowledge_base_id + index_version_id` 分区。
- 所有 Chunks 均带 `index_version_id`，普通任务写 active，全量 Build 写冻结 candidate。
- 统一组件清单参与 `config_fingerprint`，不是仅供页面展示。

## 8. Legacy 与 Snapshot 迁移

- Legacy Version 标记 `creation_reason=legacy`、`config_completeness=unknown`、`legacy_migrated=true`。
- 不虚构缺失的配置快照和组件版本；页面明确提示不可复现及不可按新门禁证明。
- 提供 `scripts/backfill_index_governance.py`，支持 dry-run / apply。
- V38 将 Snapshot 成员的 `filename`、`content_sha256` 固化并解除 `Document Version` 级联外键。删除在线资料不再改写历史 Build 输入事实。

## 9. API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/knowledge-bases/{kb}/index-definition` | 有效 Definition、active 与 drift |
| GET | `/api/knowledge-bases/{kb}/index-version-creation-context` | 向导初始上下文、范围与容量 |
| POST | `/api/knowledge-bases/{kb}/index-versions/preview` | 只读 Preview，不落库 |
| POST | `/api/knowledge-bases/{kb}/index-versions` | 原子创建 Snapshot、Version、首个 Build |
| POST | `/api/knowledge-bases/{kb}/index-versions/{version}/builds` | Retry Build attempt |
| POST | `/api/knowledge-bases/{kb}/index-versions/{version}/builds/cancel` | 取消 Build |
| POST | `/api/knowledge-bases/{kb}/index-versions/{version}/validations` | 独立 Validate |
| GET | `/api/knowledge-bases/{kb}/index-versions/{version}/validations` | 验证历史 |
| GET | `/api/knowledge-bases/{kb}/index-validation-policy` | 当前门禁策略 |
| PUT | `/api/knowledge-bases/{kb}/index-versions/{version}/active` | 只激活 ready Version |
| GET | `/api/knowledge-bases/{kb}/index-versions/{version}/diff` | 配置、文档、内容与报告差异 |
| POST | `/api/knowledge-bases/{kb}/index-versions/rollback` | 回滚 previous |
| POST | `/api/knowledge-bases/{kb}/index-versions/{version}/retire` | 显式退役 |
| DELETE | `/api/knowledge-bases/{kb}/index-versions/{version}/content` | Cleanup 物理内容 |
| GET | `/api/knowledge-bases/{kb}/index-versions/{version}/events` | 生命周期事件 |

所有带 `{kb}` 与 `{version}` 的读取/动作在数据库层约束知识库归属，避免借用一个可访问 KB 路径读取或操作另一 KB 的 Version。

旧 `POST /index-builds` 返回 410，强制调用方改走 Preview → Create。

## 10. 后端服务边界

| 模块 | 责任 |
| --- | --- |
| `document_snapshots.py` | 当前文档集合、Snapshot 指纹、不可变成员 |
| `index_versions.py` | Definition、Version、指纹、状态转换、Activate/Rollback/Retire/Cleanup/Compare |
| `index_validation.py` | 三层门禁、策略版本、不可变验证报告 |
| `pipeline_governance.py` | Build / Operation / Document Index State 聚合 |
| `postgres_documents.py` | 候选创建、Build 排队、Retry/Cancel、Worker 执行 |
| `data_source_sync.py` | 外部数据源发现、变更、资源级重试与游标提交 |
| `index_retention.py` | 安全保留候选与显式物理清理 |

模块边界测试禁止 Data Sync 直接调用 Validate / Activate，也禁止 Build 聚合写发布状态。

## 11. P0 / P1 / P2 完成映射

### P0

- Build 成功误写失败：修复聚合与 V37 历史数据收口。
- Cleanup 失败状态：改用 `build_failed / validation_failed`。
- `validating` 页面入口：增加执行验证。
- Validate / Activate 解耦：完成。
- 隐式创建 Version 与硬编码 Chunking：旧 API 停用，改用五步向导。
- Build / Operation / Version 状态混用：状态域与写入所有权分离。

### P1

- 有效 Index Definition 与字段来源：完成。
- 完整 Snapshot / Component Manifest：完成。
- ACL / Citation / Metadata / Keyword / 组件技术门禁：完成。
- Recall@10 / nDCG@10 / ACL / Metadata 质量门禁：完成。
- Retire 与回滚内容时间点：完成。
- Version 配置、文档、内容与报告对比：完成。
- Legacy 完整度与不可验证提示：完成。

### P2

- 自动退役与安全 Cleanup：新版本激活自动退役更旧 previous；Retention Sweep 按数量和最短保留期清理。
- 构建并发、单次上限、估算、取消：完成。
- 验证策略版本与历史兼容：`v2` + Legacy warning。
- 可观测性：索引治理动作指标、审计与 append-only 生命周期事件。
- previous 内容补齐：禁止隐式原地混写，通过差异确认 + 新建完整候选 Version 收敛。

## 12. 测试清单

已补充或更新下列测试用例，但本轮尚未执行：

- 状态域与 Build 成功/部分失败/取消后迟到 Worker。
- Preview/Create 指纹过期、幂等、并发容量、文档任务竞态回滚。
- 文档新增/更新/移除差异与 Snapshot 删除后不可变。
- 三层门禁每个关键检查及完整配置缺少高级指标时失败。
- Activate 原子状态、报告绑定和并发锁。
- Rollback 内容时间点确认、清理后拒绝回滚。
- Retire / Cleanup / Retention dry-run 与 apply。
- API Knowledge Base 范围隔离。
- 五步向导真实配置、范围、排除明细与提交预览证据。
- 评测真实 Metadata Filter 与 ACL 泄漏探针。

## 13. 验收状态

| 验收项 | 当前状态 |
| --- | --- |
| 领域对象、状态与动作代码落地 | 已完成，待验证 |
| API 与数据库范围隔离 | 已完成，待验证 |
| 页面入口、向导、详情与动作矩阵 | 已完成，待验证 |
| V37 状态/配置迁移 | 已编写，未执行 |
| V38 Snapshot 不可变迁移 | 已编写，未执行 |
| 后端与前端测试用例 | 已编写，未执行 |
| Test / Lint / Typecheck / Build | 未执行 |
| 桌面与窄屏浏览器验收 | 未执行 |
| Commit / Push | 未执行 |

只有在验证命令与真实页面验收通过后，才能把“待验证”改为“已验收”。

## 14. 后续操作

1. 轻量验证：后端相关测试、前端相关测试、Lint、类型检查。
2. 完整验证：全量测试、前端构建、迁移演练、容器与桌面/窄屏浏览器验收。
3. 用户在 VS Code 审阅差异。
4. 用户明确“提交代码”后，才允许 Commit / Push。
