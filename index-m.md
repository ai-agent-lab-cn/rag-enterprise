# RAG Enterprise 数据同步与索引治理分层实施规格

> 用途：直接交给 Claude Code，在现有项目上分阶段实施数据同步与索引治理改造。
>
> 总体原则：两条 Pipeline 分层设计、独立状态、契约衔接；先完成索引治理核心，再接入数据同步和自动编排。
>
> 核心链路：`Data Source → Sync Run → Document Revision → Document Snapshot → Index Version → Index Build → Validate → Activate`

## 1. 目标与边界

在不推翻现有数据接入、索引和检索能力的前提下，将数据同步与索引治理拆成两个职责清晰、可独立重试、可通过稳定契约组合的生产 Pipeline。

完成后必须满足：

1. 配置、不可变版本、构建尝试、验证报告、单文档状态各自建模。
2. 配置变更只能创建新 Version；已有 Version 的配置快照不可修改。
3. 一个 Version 允许多次 Build；重试必须新增 Build attempt。
4. Build 成功不等于可以上线；三层 Validate 全部通过后才能进入 `ready`。
5. Vector、Keyword、Metadata、ACL/Citation 数据必须归属同一个 `index_version_id`。
6. Activate/Rollback 只做事务内原子指针切换，不重新 Parse、Chunk、Embedding 或复制数据。
7. 线上检索只读取 Knowledge Base 当前 `active_version_id` 对应的数据。
8. legacy 数据明确标注信息缺失，不伪造配置、维度或验证结果。
9. 数据同步成功只表示源数据变化已经可靠落入 Document Asset，不等于索引构建或发布成功。
10. Index Version 同时冻结配置快照和输入文档快照，确保同一版本可复现。
11. 同步状态、索引状态、文档修订状态不得混成一套状态机。

非目标：不借此更换 Parser、Embedding 模型或存储引擎；不重写完整检索链路；不自动删除历史物理索引；不以仅禁用前端按钮代替后端门禁；不把 Sync、Build、Validate、Activate 合并成一个巨大任务状态机；不要求配置变更时重新同步数据源。

## 2. 总体分层与实施决策

### 2.1 最终分层

```text
数据源层 Data Source
Connector / Upload / API / Object Storage
        ↓
数据同步层 Data Sync Pipeline
Discover → Fetch → Normalize → Detect Change → Persist Revision
        ↓
文档资产层 Document Asset
Document → Document Revision → Change Set
        ↓
索引输入快照层 Document Snapshot
冻结某一时刻可参与构建的 Document Revision 集合
        ↓
索引治理层 Index Governance Pipeline
Index Definition → Index Version → Index Build → Validate → Activate
        ↓
检索服务层 Retrieval Runtime
Active Version → Vector + Keyword + Metadata/ACL → Fusion/Rerank
```

### 2.2 实施决策

采用“整体设计一起定稿、分阶段实施”的方案：

1. **先完成 Index Governance Core**：先建立 Version、Document Snapshot、Build、Validation、Activate/Rollback 和统一版本检索闭环。
2. **再拆 Data Sync Pipeline**：建立 Data Source、Sync Run、Cursor、Document Revision、Change Set 和 Tombstone。
3. **最后增加 Orchestration**：同步完成可自动生成 Candidate、触发 Build/Validate，但 Activate 默认保持人工或显式策略审批。

不采用一体化大 Pipeline：

```text
Sync → Parse → Chunk → Embed → Validate → Activate
```

原因：它会混淆同步失败与构建失败，使配置变更错误触发数据同步，使 Connector 重试重复构建，也无法独立回放同一批文档或使用不同配置构建多个候选版本。

### 2.3 分层责任边界

| 层 | 回答的问题 | 负责 | 不负责 |
|---|---|---|---|
| Data Source | 数据从哪里来 | 连接配置、源身份、凭证引用、同步策略 | 解析和索引发布 |
| Data Sync | 源数据发生了什么变化 | 拉取、游标、去重、Revision、删除检测、重试 | Chunk、Embedding、Activate |
| Document Asset | 当前有哪些可治理的内容修订 | Document 身份、Revision、内容哈希、来源追踪 | 索引生命周期 |
| Document Snapshot | 这次索引输入是什么 | 冻结 Revision 集合、水位、统计与指纹 | 修改源文档或配置 |
| Index Governance | 如何构建并安全发布索引 | Definition、Version、Build、Validate、Activate/Rollback | 连接外部数据源 |
| Retrieval Runtime | 当前查询使用哪个索引 | 一次解析 active Version、同版本混合检索 | 自动选择未发布 Candidate |

### 2.4 两条 Pipeline 的正式契约

Data Sync 的唯一正式产物是稳定的 Document Asset 变化：

```text
Sync Run
  └── Change Set
      ├── added_revision_ids
      ├── updated_revision_ids
      ├── deleted_document_ids / tombstones
      ├── source_watermark
      └── completeness
```

Index Governance 不直接读取 Connector 临时响应。创建 Index Version 前，先创建不可变 Document Snapshot：

```text
Index Version = Config Snapshot + Document Snapshot
```

Document Snapshot 至少保存：

- `document_snapshot_id`
- `knowledge_base_id`
- `source_watermark` 或各 Data Source watermark map
- 纳入的 `document_revision_id` 集合或可重现查询边界
- included/deleted/excluded 数量
- `snapshot_fingerprint`
- 创建时间和创建原因

如果两次构建使用不同 Document Revision 集合，即使配置相同，也必须属于不同 Index Version 或明确的新输入快照版本；不得让同一个 Version 在重建时静默改变输入数据。

## 3. 领域模型拆分

```text
Knowledge Base
  ├── Data Source
  │     └── Sync Run
  │           └── Change Set
  ├── Document
  │     └── Document Revision
  ├── Document Snapshot
  ├── Index Definition（可修改配置源）
  │     └── Index Version（不可变配置快照）
  │           ├── Index Build（一次构建尝试，1:N）
  │           ├── Validation Report（一次验证执行，1:N）
  │           └── Document Index State（版本内单文档状态，1:N）
  ├── active_version_id
  └── previous_version_id
```

### 3.1 Data Source 数据源

保存数据源身份、类型、启停状态、非敏感连接配置、凭证引用和同步策略。凭证值不得落入普通配置或日志。

### 3.2 Sync Run 数据同步执行

记录一次全量或增量同步：触发方式、起止 watermark、游标、拉取/新增/修改/删除/失败数量、重试、错误与完成状态。重试同一 Run 必须幂等；创建新 Run 时保留历史。

### 3.3 Document 与 Document Revision 文档资产

`Document` 表达稳定业务身份；`Document Revision` 表达某次内容状态：

```text
Document
  id, knowledge_base_id, data_source_id, source_document_key

Document Revision
  id, document_id, revision_no
  source_uri, source_etag, source_modified_at
  content_hash, content_location, metadata
  sync_run_id, revision_status, created_at
```

源删除使用 tombstone/删除 Revision 表达，不立即破坏历史 Version 需要的数据。

### 3.4 Change Set 变更集合

保存一次 Sync Run 产生的新增、修改、删除和排除结果，是同步层向编排层发出的稳定事实，不是直接构建命令。

### 3.5 Document Snapshot 文档输入快照

冻结 Index Version 使用的 Document Revision 集合。Snapshot 创建后不可修改，其指纹和统计必须可校验。它隔离“文档持续变化”和“索引版本必须可复现”这两个不同时间模型。

### 3.6 Index Definition 索引定义

保存“下一版索引应该如何构建”的可编辑配置，包括 Parser、Chunking、Embedding、Keyword、Metadata Schema、ACL Schema 和 Retrieval。修改 Definition 不得影响已有 Version 或线上流量。

### 3.7 Index Version 索引版本

保存创建时刻完整、规范化且不可变的配置快照，承载版本生命周期状态。

- 创建后 `config_snapshot`、`config_fingerprint` 不可更新。
- 配置变化必须从 Definition 创建新 Version。
- 创建时必须绑定一个不可变 `document_snapshot_id`。
- `config_fingerprint` 对规范化快照计算，例如 SHA-256，并固定字段排序、序列化和空值规则。
- Version 不保存一次任务的进度与错误细节；这些属于 Build 或 Validation。

### 3.8 Index Build 索引构建

记录某 Version 的一次构建尝试，包括阶段、进度、计数、耗时、日志引用和失败原因。关系为 `Index Version 1:N Index Build`。

推荐阶段：

```text
discover → parse → chunk → enrich_metadata_acl → embed
→ build_vector → build_keyword → build_metadata → finalize
```

重新构建必须生成新 `attempt_no`，不得覆盖失败记录或修改 Version 快照。

### 3.9 Validation Report 验证报告

记录某次成功 Build 的正式上线验证，绑定 `index_version_id` 和 `index_build_id`，保存门禁策略版本、阈值、指标、失败项和 Active 基线。重新验证创建新报告，不覆盖历史。

### 3.10 Document Index State 文档索引状态

描述单个 Document 在指定 Version/Build 中各处理阶段的结果，是 Build 汇总、失败定位和生产排障的证据来源；不替代 Version 或 Build 状态。

## 4. 统一状态机

### 4.1 Sync Run

| 英文状态 | 中文展示 | 含义 |
|---|---|---|
| `pending` | 等待同步 | 已创建，尚未执行 |
| `running` | 同步中 | 正在发现、拉取和持久化修订 |
| `success` | 同步成功 | 本次同步完整完成 |
| `partial_success` | 部分成功 | 已落库部分有效变化，同时存在明确失败项 |
| `failed` | 同步失败 | 未形成可接受的同步结果 |
| `cancelled` | 已取消 | 同步被取消 |

`partial_success` 是否允许生成 Document Snapshot 必须由明确策略决定；默认不自动进入索引构建，需人工处理或只纳入已成功且可证明完整的范围。

### 4.2 Index Version

| 英文状态 | 中文展示 | 含义 |
|---|---|---|
| `draft` | 草稿 | 快照已创建，尚未构建 |
| `building` | 构建中 | 存在运行中的构建 |
| `build_failed` | 构建失败 | 最近一次构建失败，可重新构建 |
| `validating` | 验证中 | 正在执行正式验证 |
| `validation_failed` | 验证失败 | 构建成功但未通过发布门禁 |
| `ready` | 待发布 | 最新正式验证通过，可激活 |
| `active` | 当前生效 | 正在承载线上检索流量 |
| `previous` | 上一生效版本 | 保留用于快速回滚 |
| `retired` | 已退役 | 不承载流量，物理索引仍保留 |
| `cleaned` | 已清理 | 物理资源已清理，仅保留治理记录 |

```text
draft → building → validating → ready → active → previous → retired → cleaned
           └→ build_failed       └→ validation_failed
```

### 4.3 Index Build

`pending（等待构建） → running（构建中） → success（构建成功）`

失败终态：`failed（构建失败）`、`cancelled（已取消）`。

### 4.4 Validation Report

`pending（等待验证） → running（验证中） → pass（验证通过）`

失败终态：`failed（验证失败）`、`cancelled（已取消）`。

### 4.5 Document Index State

`parse_status`、`chunk_status`、`embedding_status`、`vector_status`、`keyword_status`、`metadata_status` 分别使用 `pending | running | success | failed | skipped`。

## 5. 状态转换规则

所有转换必须由领域服务执行并校验当前状态。Controller、Worker、前端不得直接任意写状态。

| 动作 | 前置状态 | 成功后 | 失败后 | 约束 |
|---|---|---|---|---|
| 开始同步 | Sync Run `pending` | `running` | 保持或 `failed` | 锁定本次起始 watermark |
| 同步完成 | Sync Run `running` | `success` / `partial_success` | `failed` | 先提交 Revision/Change Set，再推进状态 |
| 创建文档快照 | 可接受的 Change Set 或当前资产视图 | Snapshot immutable | 不创建 | 固化 Revision 集合、水位和指纹 |
| 创建快照 | Definition 配置有效 | `draft` | 不创建 | 快照和指纹一次写入 |
| 开始构建 | `draft` / `build_failed` / `validation_failed` | `building` | 保持原状态 | 新建 Build；最多一个 running |
| 构建成功 | Version `building`，Build `running` | `validating` 或等待显式验证 | — | Build 置 `success`，固化计数 |
| 构建失败 | Version `building`，Build `running` | `build_failed` | — | Build 置 `failed`，保存错误 |
| 开始验证 | 存在成功 Build | `validating` | 保持原状态 | 新建 Report |
| 验证通过 | `validating` | `ready` | — | 三层门禁均满足 |
| 验证失败 | `validating` | `validation_failed` | — | 保存失败项和阈值 |
| 激活 | `ready` | `active` | 事务整体回滚 | 最新正式报告必须 `pass` |
| 回滚 | 目标 `previous`，当前版本 `active` | 目标 `active` | 事务整体回滚 | 两版本物理资源可用 |
| 退役 | `previous` 或合规非活动版本 | `retired` | 保持原状态 | 不允许退役 active |
| 清理 | `retired` | `cleaned` | 保持 `retired` 并记错 | 禁止清理 active/previous |

并发与幂等：

- 使用行锁、乐观锁或数据库 advisory lock，以 Knowledge Base 为粒度串行化 Activate/Rollback。
- 状态更新带期望旧状态，例如 `WHERE id=? AND status=?`；影响行数不为 1 返回 409。
- 异步任务重复投递不得产生重复的 running Build/Validation。
- 创建和动作 API 支持 `Idempotency-Key` 或等价 request id。
- 同一 Data Source 默认只允许一个运行中的 Sync Run；并发策略如允许分区同步，必须保证 cursor/watermark 和文档身份不冲突。
- Document 使用 `(data_source_id, source_document_key)` 稳定标识；相同 content hash 不重复创建等价 Revision。

## 6. Activate 原子切换

实现正式领域动作：

```text
activateIndexVersion(knowledgeBaseId, targetVersionId, actor, requestId)
```

在一个数据库事务内：

1. 锁定 Knowledge Base 或其版本指针行。
2. 确认目标 Version 属于该 KB 且状态为 `ready`。
3. 确认目标最新正式 Validation Report 为 `pass`，并绑定目标的成功 Build。
4. 校验 Vector、Keyword、Metadata、ACL/Citation 均属于 `targetVersionId`，物理资源可读。
5. 读取原 `active_version_id`、`previous_version_id`。
6. 原 active（如存在）由 `active → previous`。
7. 更老的 previous 按保留策略转 `retired`；耗时物理清理不得放在本事务内。
8. 目标由 `ready → active`。
9. 原子更新 `active_version_id=target`、`previous_version_id=oldActive`。
10. 写 append-only Audit：before/after、actor、requestId、validationReportId、时间。
11. 提交；任一步失败全部回滚。

禁止：先改状态后改指针；分三次切换 Vector/Keyword/Metadata；切换期间复制索引；依赖前端阻止非法激活。

## 7. Rollback

实现：

```text
rollbackIndexVersion(knowledgeBaseId, targetPreviousVersionId, actor, reason, requestId)
```

- 目标必须等于当前 `previous_version_id` 且状态为 `previous`。
- 目标物理索引仍存在并通过最低可用性检查。
- 当前 active → previous，目标 previous → active；两个指针在同一事务内交换。
- 写 `rollback` 审计记录，保存原因和 before/after。
- 不重新执行 Parse、Chunk、Embedding、Build 或完整 Validate。
- 目标已 `retired`/`cleaned` 时普通 Rollback 必须拒绝；恢复另走独立流程。

## 8. 三层 Validate 发布门禁

Validation 输出结构化 `check_key`、expected、actual、threshold、severity、status、message。任一 critical 项失败，总状态为 `failed`，Version 进入 `validation_failed`。

### 8.1 Integrity 完整性门禁

- Document coverage：预期与已索引文档一致。
- Chunk consistency：文档、Chunk 数量与归属可追溯。
- ACL consistency：继承正确，无缺失或越权风险。
- `missing_document = 0`。
- `orphan_chunk = 0`。
- `duplicate_chunk` 满足明确阈值，默认 critical 时为 0。
- Build 汇总计数与 Document Index State 聚合一致。
- Document Snapshot 中的每个 included Revision 都有明确处理结果；不得静默遗漏同步资产。

### 8.2 Technical 技术门禁

- 实际 Vector dimension 等于快照声明值；未知或 placeholder 不得通过。
- Vector、Keyword、Metadata 记录数和关联关系满足策略。
- `document_id`、`chunk_id`、`knowledge_base_id`、`index_version_id` 等必需字段完整。
- ACL 继承结果正确。
- Citation 的 document、page/section/offset 等定位信息满足策略。
- Vector/Keyword/Metadata/ACL 的版本均等于 Candidate Version。
- 物理 index/table/collection/namespace 可读且不存在跨版本混写。

### 8.3 Retrieval Quality 检索质量门禁

使用固定、可版本化评测集，对 Candidate 与当前 Active 使用相同查询、过滤和参数执行：

- Recall@5
- Recall@10
- MRR
- nDCG@10

报告保存 evaluation set version、baseline version、Candidate/Active 值、绝对和相对变化、阈值、结论。阈值来自版本化 Validation Policy，不硬编码在 Controller。

首个版本没有 Active 时使用绝对阈值；legacy 无可靠基线时标记 `baseline_unavailable`，不得伪造对比。

## 9. Vector Keyword Metadata 统一版本

所有可检索实体拥有非空 `index_version_id`；如底层按独立 collection/index 存储，资源注册表和命名也必须绑定 Version。

```text
request → resolve active_version_id once
        → Vector search(version_id)
        → Keyword search(version_id)
        → Metadata/ACL filter(version_id)
        → fusion / rerank
```

单次请求只解析一次 active 指针，并贯穿请求上下文，避免中途 Activate 造成混合读取。禁止 Vector vN、Keyword vN-1、Metadata vN 参与同一次 Hybrid Retrieval。

## 10. Legacy 迁移治理

### 10.1 数据表达

- `legacy_migrated = true`。
- `config_completeness = partial | complete | unknown`。
- 未知 Parser、Chunking、Embedding 和 dimension 写 `NULL`，不得用 `legacy` 或 `1` 冒充真实值。
- UI 对 `NULL` 显示“未知/未记录”，不得展示 placeholder `1 维`。
- 历史回填报告使用 `report_source=legacy_backfill`，不得标成标准 Validation `pass`。

### 10.2 迁移步骤

1. 只读盘点现有版本表、物理索引、active 解析方式和历史字段可信度。
2. 先新增表、字段、nullable 外键和索引，不删除旧字段。
3. 为每个 KB 建 Definition 和 legacy Version，只回填可证实信息。
4. 将现有线上版本标为 `active` 并设置 `active_version_id`；仅能明确识别时设置 previous。
5. 为旧索引数据回填 `index_version_id`；无法可靠归属的数据进入异常清单，不猜测。
6. 双读核对后切换 Runtime 到新 active 指针。
7. 稳定观察后停止写旧字段；删除旧结构必须作为独立后续迁移。
8. 对既有 Connector/上传数据回填 Data Source 和 Document Revision 时，只保留有证据的 source key、时间和 hash；未知 watermark 不猜测。
9. 为现有 active Version 创建 legacy Document Snapshot；无法重建精确 Revision 集合时标记 `snapshot_completeness=partial/unknown`。

迁移脚本必须幂等、可中断重跑，支持 dry-run、统计摘要和异常清单。禁止在活动数据库执行 `DROP SCHEMA public CASCADE` 等破坏性测试。

## 11. API 设计

URL 可适配现有路由风格，但不得改变资源语义。

### 11.1 Data Source 与 Sync

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/knowledge-bases/{kbId}/data-sources` | 数据源列表 |
| `POST` | `/knowledge-bases/{kbId}/data-sources` | 创建数据源；凭证单独安全处理 |
| `GET` | `/data-sources/{sourceId}` | 获取数据源与同步摘要 |
| `PATCH` | `/data-sources/{sourceId}` | 更新配置/策略，不改历史 Run |
| `POST` | `/data-sources/{sourceId}/sync-runs` | 发起全量或增量同步 |
| `GET` | `/data-sources/{sourceId}/sync-runs` | 同步历史 |
| `GET` | `/sync-runs/{runId}` | 同步进度、计数与错误 |
| `GET` | `/sync-runs/{runId}/changes` | Change Set 和失败项 |
| `POST` | `/sync-runs/{runId}/cancel` | 取消仍可取消的同步 |
| `POST` | `/knowledge-bases/{kbId}/document-snapshots` | 从明确水位/Revision 视图创建快照 |
| `GET` | `/document-snapshots/{snapshotId}` | 快照统计、指纹和来源 |

### 11.2 Definition 与 Version

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/knowledge-bases/{kbId}/index-definition` | 获取可编辑定义 |
| `PUT` | `/knowledge-bases/{kbId}/index-definition` | 更新定义，不影响已有版本 |
| `POST` | `/knowledge-bases/{kbId}/index-versions` | 从 Definition + Document Snapshot 创建不可变 Version |
| `GET` | `/knowledge-bases/{kbId}/index-versions` | 版本列表 |
| `GET` | `/index-versions/{versionId}` | 版本详情 |

### 11.3 Build 与 Validation

| Method | Path | 用途 |
|---|---|---|
| `POST` | `/index-versions/{versionId}/builds` | 新建 Build attempt |
| `GET` | `/index-versions/{versionId}/builds` | 构建历史 |
| `GET` | `/index-builds/{buildId}` | 构建进度、计数、错误 |
| `GET` | `/index-builds/{buildId}/documents` | 单文档状态 |
| `POST` | `/index-builds/{buildId}/cancel` | 取消构建 |
| `POST` | `/index-versions/{versionId}/validations` | 对成功 Build 发起验证 |
| `GET` | `/index-versions/{versionId}/validations` | 报告历史 |
| `GET` | `/validation-reports/{reportId}` | 三层门禁详情 |

### 11.4 生命周期动作

| Method | Path | 用途 |
|---|---|---|
| `POST` | `/index-versions/{versionId}/activate` | 原子激活 ready 版本 |
| `POST` | `/knowledge-bases/{kbId}/index-versions/rollback` | 原子回滚 previous |
| `POST` | `/index-versions/{versionId}/retire` | 退役非活动版本 |
| `POST` | `/index-versions/{versionId}/cleanup` | 清理 retired 物理资源 |
| `GET` | `/index-versions/{versionId}/events` | 生命周期与审计事件 |

### 11.5 错误规范

稳定领域错误码：

- `VERSION_NOT_READY`
- `VALIDATION_NOT_PASSED`
- `VERSION_STATE_CONFLICT`（HTTP 409）
- `ACTIVE_VERSION_CANNOT_RETIRE`
- `PREVIOUS_VERSION_UNAVAILABLE`
- `INDEX_COMPONENT_VERSION_MISMATCH`
- `PHYSICAL_INDEX_UNAVAILABLE`
- `BUILD_ALREADY_RUNNING`
- `VALIDATION_ALREADY_RUNNING`
- `SYNC_ALREADY_RUNNING`
- `SYNC_RESULT_INCOMPLETE`
- `DOCUMENT_SNAPSHOT_NOT_READY`
- `DOCUMENT_SNAPSHOT_CHANGED`

错误响应包含 `code`、`message`、`request_id`、可选 `details`，不泄露内部堆栈。

## 12. 页面改造

### 12.1 数据源与同步页

- Data Source 列表：类型、状态、最近同步、watermark、Document 数和操作。
- Sync Run 详情：阶段、游标、拉取/新增/修改/删除/失败计数、错误与 Change Set。
- 明确区分“同步成功”“已生成文档快照”“索引构建完成”“索引已发布”。
- `partial_success` 显示影响范围和是否允许生成 Candidate，不得显示成完全成功。

### 12.2 版本列表页

列：版本、状态、配置摘要、最近构建、最近验证、创建时间、操作。

- 使用统一英文值和中文映射，突出 active/previous。
- 操作由后端 capability 或唯一共享规则决定，避免各页面自行判断。
- building 展示进度；build_failed 展示日志/重新构建；validation_failed 展示报告/重新验证/重新构建；ready 展示激活。

### 12.3 版本详情页

改为五个 Tab：

1. 概览：状态、版本、配置指纹、Document Snapshot 指纹、来源水位、时间、Document/Chunk/Vector 数量。
2. 配置快照：Parser、Chunking、Embedding、Keyword、Metadata、ACL。
3. 构建记录：一 Version 多 Build，attempt 倒序，可看失败文档。
4. 验证报告：完整性、技术、检索质量、Active 对比。
5. 生命周期：创建、构建、验证、激活、回滚、退役、清理。

### 12.4 知识库索引治理页

固定展示 Active、Previous、Candidate；提供“激活 ready 版本”和“回滚到 previous”。危险动作确认内容包含版本 ID、影响和不可执行原因。

### 12.5 Legacy 展示

显示“历史迁移版本”和配置完整度。未知值展示“未知/未记录”，解释来源；不把多个 `legacy` 当配置值，也不把 `legacy-backfill` 当正式质量通过。

## 13. 数据库字段建议

类型、主键和时间字段遵循现有项目规范；以下是最低语义集合。

### `data_sources`

```text
id, knowledge_base_id, source_type, name, status
connection_config, credential_reference, sync_policy
last_successful_watermark, created_at, updated_at, created_by
```

`connection_config` 不保存明文密码、Token 或密钥。

### `sync_runs`

```text
id, data_source_id, run_type, trigger_type, status
start_watermark, end_watermark, cursor_state
items_discovered, items_fetched, revisions_created
items_added, items_updated, items_deleted, items_failed
started_at, finished_at, heartbeat_at
error_code, error_message, request_id, created_by
```

### `documents` 与 `document_revisions`

```text
documents:
id, knowledge_base_id, data_source_id, source_document_key
current_revision_id, lifecycle_status, created_at, updated_at

document_revisions:
id, document_id, revision_no, sync_run_id
source_uri, source_etag, source_modified_at
content_hash, content_location, metadata
revision_status, created_at
```

约束：`UNIQUE(data_source_id, source_document_key)`、`UNIQUE(document_id, revision_no)`；Revision 内容不可原地修改。

### `sync_change_sets`

```text
id, sync_run_id, data_source_id
added_revision_ids, updated_revision_ids, deleted_document_ids
source_watermark, completeness, summary, created_at
```

大型集合不要强塞单个 JSON；按现有数据库规模拆子表或事件记录。

### `document_snapshots` 与成员表

```text
document_snapshots:
id, knowledge_base_id, snapshot_fingerprint
source_watermarks, snapshot_completeness
included_count, excluded_count, deleted_count
created_at, created_by, reason

document_snapshot_members:
document_snapshot_id, document_id, document_revision_id, inclusion_status
```

约束：Snapshot 和成员创建完成后不可修改；`UNIQUE(document_snapshot_id, document_id)`。

### `index_definitions`

```text
id, knowledge_base_id
parser_config, chunking_config, embedding_config, keyword_config
metadata_schema, acl_schema, retrieval_config
revision, created_at, updated_at, created_by, updated_by
```

### `index_versions`

```text
id, knowledge_base_id, definition_id, version_no
document_snapshot_id
config_snapshot, config_fingerprint
parser_version, chunking_version, embedding_model, embedding_dimension
metadata_schema_version, acl_schema_version
status, legacy_migrated, config_completeness
created_at, created_by, activated_at, retired_at, cleaned_at
```

约束：`UNIQUE(definition_id, version_no)`；应用层和数据库层共同防止快照字段更新。是否对 fingerprint 唯一，先明确是否允许同配置生成新 Version。

### Knowledge Base 或运行时指针表

```text
active_version_id, previous_version_id, pointer_revision, updated_at
```

外键指向 Version；领域服务校验同一 KB。使用约束/部分唯一索引保证一个 KB 最多一个 active、一个 previous。

### `index_builds`

```text
id, index_version_id, attempt_no, status, current_stage, progress_percent
documents_total, documents_processed, documents_success, documents_failed, documents_skipped
chunks_total, vectors_total, keyword_docs_total, metadata_docs_total
started_at, finished_at, heartbeat_at
error_code, error_message, log_reference, created_at, created_by
```

约束：`UNIQUE(index_version_id, attempt_no)`；对 running 记录做并发保护。

### `validation_reports`

```text
id, index_version_id, index_build_id
status, policy_version, evaluation_set_version, baseline_version_id
integrity_result, technical_result, retrieval_result
summary, failure_items, report_source
started_at, finished_at, created_at, created_by
```

JSON 结果必须带稳定 schema version。

### `document_index_states`

```text
id, document_id, index_version_id, index_build_id
parse_status, chunk_status, embedding_status
vector_status, keyword_status, metadata_status
chunk_count, vector_count
error_stage, error_code, error_message
started_at, finished_at, updated_at
```

通常 `UNIQUE(document_id, index_build_id)`；按 Build 和失败阶段建排障索引。

### `index_lifecycle_events`

```text
id, knowledge_base_id, index_version_id
event_type, from_status, to_status
actor_id, request_id, reason
before_state, after_state, created_at
```

append-only，不允许业务更新/删除审计记录。

## 14. 后端服务边界

- `IndexDefinitionService`：校验/更新 Definition，生成规范化快照与指纹。
- `IndexVersionService`：创建不可变 Version、查询详情、合法状态转换；不直接运行耗时任务。
- `IndexBuildService` + Build Worker：创建 attempt、调度任务、聚合文档状态、完成/失败 Build。
- `IndexValidationService` + Validation Worker：执行三层门禁、生成不可变报告、决定 ready/validation_failed。
- `IndexActivationService`：唯一可修改 active/previous 指针的服务，负责锁、事务、一致性和审计。
- `IndexCleanupService`：执行保留策略和物理清理，可重试，永不清理 active/previous。
- `RetrievalVersionResolver`：请求开始时解析一次 active Version，传给全部检索适配器。
- Repository 只负责持久化/条件更新；Controller 只做鉴权、参数校验、服务调用、错误映射，不直接写状态。

## 15. 测试清单

### 15.1 Data Sync 与 Document Asset

- 同一 Data Source 并发同步规则生效；重复投递不重复创建 Revision。
- cursor/watermark 只在变更可靠持久化后推进。
- 相同 source key + content hash 不生成等价 Revision；内容变化生成新 revision_no。
- 源删除生成 tombstone，不破坏历史 Snapshot/Version。
- success、partial_success、failed 的计数、Change Set 与实际落库一致。
- 凭证不进入数据库普通字段、日志、错误响应或审计 before/after。
- Snapshot 成员和指纹不可修改；相同边界重试结果稳定。

### 15.2 索引治理单元测试

- Definition 规范化和相同输入指纹稳定。
- Index Version 必须绑定 Document Snapshot；不同输入快照不得复用同一 Version。
- Version 创建后快照不可修改。
- Version/Build/Validation 状态转换的允许与拒绝路径。
- 重试 Build 递增 attempt 且保留历史。
- 三层门禁聚合规则和阈值边界。
- legacy 未知值保持 NULL，不转成 1 或伪通过。
- 页面/API capability 与后端状态规则一致。

### 15.3 数据库与集成测试

- 同一 Version 并发 Build 只产生一个 running。
- 两个 ready Version 并发 Activate，最终只有一个 active，指针与状态一致。
- Activate 任一步故障注入后，指针、状态、审计符合事务原子性。
- Rollback 原子交换 active/previous。
- 禁止跨 KB 激活/回滚。
- 禁止激活验证未通过、报告过期或组件版本不一致的版本。
- 禁止清理 active/previous。
- 迁移 dry-run、幂等重跑、可中断恢复。

### 15.4 Worker、编排与 Retrieval 测试

- 任务重复投递幂等；Worker 崩溃/超时有 heartbeat 和失败处理。
- 单文档失败与 Build 聚合计数一致。
- 失败 Build 不能生成通过报告。
- 单次请求的 Vector、Keyword、Metadata/ACL 收到同一 active Version。
- Activate 与并发查询同时发生时，单请求内不混用版本。
- inactive/previous/retired/cleaned 不参与正常线上检索。
- Hybrid Retrieval 结果可追溯到 `index_version_id`。
- Sync success 只能产生 Change Set/Snapshot 候选，不直接改变 active 指针。
- 配置变化可对同一 Document Snapshot 创建新 Version，无需重新同步。
- 文档变化可对同一 Definition 创建新 Snapshot/Version。
- partial_success 默认不自动触发 Candidate；策略允许时仅纳入可证明完整的范围。
- Orchestration 重放不重复创建等价 Candidate、Build 或 Validation。

### 15.5 API、权限和前端测试

- 各动作的成功、非法状态、409、资源不存在、越权、跨租户。
- Idempotency-Key 重试结果一致；错误码稳定且不泄露堆栈。
- Activate/Rollback/Retire/Cleanup 有管理权限和审计主体。
- 中英文状态映射、五个详情 Tab、空态/错误态、legacy 展示正确。
- 按钮与后端 capability 一致；后端拒绝时显示领域原因。
- 桌面/移动端可用，无控制台错误。
- UI 明确区分 Sync、Snapshot、Build、Validate、Activate 五个阶段，状态不串用。

## 16. P0 P1 P2 实施顺序

### P0A 索引治理核心闭环

1. 同时盘点现有数据接入、文档表、索引表、服务、API、Worker、页面、检索版本解析和 legacy 数据，先输出分层映射。
2. 新增 Document Snapshot 及索引治理核心表/字段/枚举/约束。
3. 拆分 Definition、Version、Build、Validation、Document Index State，实现状态机和条件更新。
4. 将 Vector/Keyword/Metadata/ACL 绑定 `index_version_id`。
5. 实现 RetrievalVersionResolver，线上只读 active 指针。
6. 实现三层 Validate 最低门禁；技术层必须拦截维度/版本不一致。
7. 实现带锁、事务、审计的 Activate/Rollback。
8. 为现有文档视图生成 legacy Document Snapshot，完成索引 legacy 安全回填、双读核对和运行时切换。
9. 完成关键单元、集成、并发、故障注入和回归测试。

P0A 结束条件：在不依赖新同步系统的前提下，从当前数据库文档生成 Snapshot，新 Version 能走通构建→验证→激活→原子回滚；线上不跨版本混读。

### P0B 数据同步 Pipeline

1. 建立 Data Source、Sync Run、Cursor/Watermark、Document、Document Revision、Change Set、Tombstone。
2. 将现有上传/Connector 接入适配为 Sync Run，但保持外部接口兼容。
3. 实现幂等增量同步、失败重试、partial_success 和审计。
4. 由 Change Set 创建不可变 Document Snapshot，不直接触碰 active Version。
5. 完成数据迁移、双写/双读核对和同步页面。

P0B 结束条件：源数据变化能可靠转化为可审计的 Document Revision 和可复现 Snapshot，且同步失败不会污染索引状态。

### P0C 编排衔接

1. 用事件或明确服务调用连接 `Sync completed → Snapshot Candidate → Index Version Candidate`。
2. 支持策略化自动 Build/Validate。
3. Activate 默认人工确认；只有未来明确批准的发布策略才能自动激活。
4. 对事件重复、乱序、延迟和中断进行幂等测试。

P0C 结束条件：两条 Pipeline 能协同，但仍可独立运行、失败、重试和审计。

### P1 可观测性和完整管理体验

1. 完善多 Build、Document State、日志引用、进度和失败明细。
2. Validation 支持 Active 对比、评测集/Policy 版本。
3. 改造版本列表、五 Tab 详情页、Active/Previous 操作区、生命周期时间线。
4. 实现 capabilities、稳定错误码、幂等键和完整权限。
5. 增加 Worker heartbeat、超时恢复和监控。
6. 完善 Data Source/Sync Run/Change Set/Snapshot 的管理页面和关联导航。

### P2 生命周期自动化和长期治理

1. Retire/Cleanup 保留策略、后台清理、失败重试。
2. 版本数、存储成本、构建耗时、验证趋势、回滚率监控。
3. Validation Policy 管理、评测集治理和质量告警。
4. legacy 退出策略和旧字段独立清理迁移。

不得为完成 P0B/P0C/P1/P2 越过尚未验收的 P0A 正确性门禁。

## 17. 验收标准

- [ ] Data Sync 与 Index Governance 在代码、数据库、API 和 UI 中分层，状态互不混用。
- [ ] Sync success 只表示数据已形成可靠 Revision/Change Set，不表示 Build、Validation 或发布成功。
- [ ] Document 与 Document Revision 分离，源更新和删除不破坏历史版本可复现性。
- [ ] Document Snapshot 不可变，包含明确水位、Revision 集合、统计和指纹。
- [ ] Index Version 同时绑定 Config Snapshot 与 Document Snapshot。
- [ ] 同配置不同文档输入、同文档输入不同配置都能生成独立且可追踪的 Candidate。
- [ ] 五个核心对象职责独立，Build/Validation 不再只是 Version 字段。
- [ ] Version 快照不可修改，配置变化生成新 Version。
- [ ] 一个 Version 支持多个 Build attempt，历史失败可查。
- [ ] Version、Build、Validation 三套状态不混用，非法转换由后端拒绝。
- [ ] 三层 Validate 有结构化报告；未通过不能 Activate。
- [ ] 维度异常、组件版本不一致、关键 ACL/Citation 问题会被技术门禁拦截。
- [ ] Vector、Keyword、Metadata/ACL 都绑定并查询同一 `index_version_id`。
- [ ] Activate 在一个事务中更新状态、指针和审计；故障注入证明无半切换。
- [ ] Rollback 不重建索引，并在一个事务中交换指针。
- [ ] 并发 Activate 下一个 KB 始终最多一个 active。
- [ ] Runtime 单请求固定一个 active Version，不会混读。
- [ ] legacy 未知配置展示“未知/未记录”，不再展示伪造的 1 维或正式验证通过。
- [ ] 迁移支持 dry-run、幂等重跑、异常清单和可核对统计。
- [ ] 版本列表、五 Tab 详情、构建详情、验证报告、生命周期页面完成。
- [ ] 权限、审计、领域错误、空态/失败态、桌面/移动端、控制台检查通过。
- [ ] 既有测试与新增测试全部通过，并提供实际执行结果。

## 18. Claude Code 执行约束

### 18.1 开始前

1. 阅读仓库 `CLAUDE.md`、`AGENTS.md`、README、迁移和测试说明，以实际仓库规则为准。
2. 第一步只做只读盘点，不改代码。分别输出“现有数据接入/同步”和“现有索引治理”的实现映射，再标出二者当前耦合点。
3. 检查当前分支、未提交修改和测试基线；不得覆盖用户已有修改。
4. 不虚构文件路径、表名、框架能力或已实现功能；先搜索代码，再决定最小改动点。
5. 若本规格与已批准设计冲突，暂停冲突项，报告证据和选择，不擅自扩大范围。

### 18.2 实施中

1. 严格按 P0A→P0B→P0C→P1→P2；每阶段小提交、可审查，未经授权不跨阶段。
2. 先写/更新失败测试，再以最小实现使其通过；状态机、并发、迁移必须有测试。
3. 迁移优先 additive：加表/字段→回填→双读核对→切换→最后才考虑删旧结构。
4. 破坏性迁移、物理 Cleanup、生产数据修改、不可逆操作必须停下请求确认。
5. 不得用直接 SQL、Controller、Worker 绕过领域服务写生命周期状态。
6. 不得只实现前端禁用；门禁必须由后端强制。
7. 保持现有技术栈和风格；非必要不引入框架或大规模重构。
8. API/数据库命名可适配现有规范，但不可改变领域语义和原子性。
9. 暂未实现的指标/页面明确标为未实现，不返回伪造成功数据。
10. Data Sync 不得直接更新 Version 生命周期或 active 指针；Index Build 不得自行推进 Sync cursor。
11. 初始 P0A 应能基于现有数据库文档生成 Snapshot，不得为了索引治理强行先重写全部 Connector。

### 18.3 阶段交付

每阶段结束报告：

- 修改文件和迁移。
- 已完成规格条目。
- 实际测试命令及结果。
- 迁移/回填统计和异常清单。
- 未完成项、风险、兼容性影响、下一步。
- 数据库证据：active/previous 指针与 Version 状态一致。
- 端到端证据：UI/API → 领域服务 → 数据库 → Retrieval 正确。
- 分层证据：Sync Run → Revision/Change Set → Document Snapshot → Index Version 的关联可追踪，失败状态不串层。

不得仅凭代码阅读宣称完成，不得将“规划中”写成“已实现”。提交、推送、部署、PR 和生产迁移均服从用户的单独授权及项目治理规则。

## 19. 建议启动提示词

```text
请严格按照本文件实施。第一步只做只读盘点：阅读仓库规则，分别定位现有数据源/上传/同步/文档修订能力，以及索引版本、构建、验证、激活、回滚、检索版本解析、数据库迁移和相关页面。输出“现有数据同步 → 目标 Data Sync Pipeline”和“现有索引治理 → 目标 Index Governance Pipeline”的两份映射，明确当前耦合点、Document Snapshot 接入点、差距、风险和 P0A 文件级实施计划。此阶段不要修改代码、数据库或配置。待我确认后再开始 P0A；未经确认不得进入 P0B/P0C，并严格遵守本文件的执行约束和验收标准。
```
- `DataSourceService`：管理源身份、非敏感配置、凭证引用和同步策略。
- `DataSyncService` + Connector Worker：创建 Sync Run，管理 cursor/watermark、幂等拉取、Revision 和 Change Set。
- `DocumentAssetService`：维护 Document 稳定身份、不可变 Revision 和 tombstone。
- `DocumentSnapshotService`：按明确水位冻结 Revision 集合并生成指纹；不启动 Build。
- `IndexOrchestrationService`：消费成功 Change Set/Document Snapshot，根据策略创建 Candidate 和触发 Build/Validate；不越权自动 Activate。
