# 索引治理正式评测与发布闭环设计

## 1. 目标

补齐知识库索引治理从候选版本构建到线上激活的产品内闭环，同时统一页面信息架构与错误处置体验：

```text
Index Definition
  → Index Version
  → Index Build
  → Formal Retrieval Evaluation
  → Three-layer Validation
  → Ready
  → Activate
  → Active / Previous / Retired / Cleaned
```

本轮覆盖：

- 发布流程步骤条视觉统一；
- 索引版本详情改为弹框；
- 运行记录详情重构；
- 表格截断内容悬浮显示全文；
- 源文件缺失的构建前检查、稳定错误码与恢复入口；
- 产品内正式检索评测异步任务；
- 正式评测报告与三层验证衔接；
- 完成后的索引治理业务闭环复盘。

本轮不包含：

- 回答质量评测的任务化改造；
- 新增登录、权限模型或多租户能力；
- 将 Validate 或 Activate 伪装成长任务；
- 自动激活候选版本；
- 自动删除源文件缺失的资料记录。

## 2. 分层边界

### 2.1 数据同步层

负责发现、获取和规范化外部内容，并将变化资料交给索引层：

```text
Data Source
  → Sync Run
  → Sync Resource Run
  → Document / Document Version
```

数据同步层不决定索引版本是否可以上线，也不持有 active / previous 指针。

### 2.2 索引构建层

负责冻结文档集合与配置，并构建 Vector、Keyword、Metadata、ACL、Citation 同代索引：

```text
Index Definition
  → Document Snapshot
  → Index Version
  → Index Build
  → Document Index State
```

构建成功后版本进入 `validating`，不会自动激活。

### 2.3 质量评测层

负责以冻结数据集、真实模型和候选版本配置运行可追溯检索评测：

```text
Evaluation Run
  → Retrieval Evaluation Report
```

评测运行使用独立 `EVALUATION_DATABASE_URL`，不污染业务数据库。评测报告记录候选版本 ID、配置指纹、数据集版本、代码提交、模型与指标。

### 2.4 发布治理层

负责三层验证、原子激活、回滚、退役与清理：

```text
Retrieval Evaluation Report
  → Validation Report
  → ready
  → Activate transaction
  → active / previous
```

Validate 是短事务；Activate 是单事务指针切换。二者均不创建虚假的异步 Operation。

## 3. 正式评测领域模型

复用现有 `evaluation_runs`，使其同时承担检索评测运行事实与可靠任务队列，不新增语义重复的 `evaluation_jobs`。

### 3.1 字段调整

新增或调整：

| 字段 | 含义 |
|---|---|
| `status` | `queued / running / succeeded / failed / cancelled` |
| `operation_id` | 对应通用运行记录投影 |
| `index_version_id` | 被评测的候选索引版本 |
| `config_fingerprint` | 冻结配置指纹 |
| `baseline_report_id` | 当前 active 版本的比较基线报告 |
| `report_payload` | 完整、不可变的检索评测报告 JSON |
| `passed` | 绝对质量阈值结论，任务未结束时允许为空 |
| `official` | 是否由真实模型、冻结数据集和受控环境产生 |
| `attempt_count / max_attempts` | 重试治理 |
| `available_at / locked_at / locked_by` | Worker 租约 |
| `error_code / error_message` | 稳定错误与诊断信息 |
| `started_at / finished_at / updated_at` | 生命周期时间 |

同一候选版本同时只允许一个 `queued / running` 的 Retrieval Evaluation Run。

### 3.2 official 与 passed

两个概念必须分开：

- `official=true`：报告来源可信、可追溯，可以作为发布验证证据；
- `passed=true`：报告达到冻结的系统绝对阈值。

三层发布验证接受配置指纹匹配的正式报告，不以 `passed` 直接替代发布判断。绝对阈值结果在 UI 中明确展示；版本切换仍由 Retrieval Quality 层执行配置一致性、证据完整性和相对基线回退检查。

## 4. Evaluation Worker

新增独立 `scripts.evaluation_worker`，不复用 Index Worker。

理由：

- 正式评测需要真实 Embedding 与 Reranker，运行时间和资源占用明显高于普通索引任务；
- 复用 Index Worker 会阻塞上传、同步和重建索引；
- 独立 Worker 可以单独配置并发、租约、超时和失败恢复。

### 4.1 运行流程

```text
POST evaluation-runs
  → 锁定并核对候选版本
  → 冻结配置、数据集、基线报告与 config_fingerprint
  → INSERT evaluation_runs(status=queued)
  → INSERT operations(operation_type=index_evaluation)
  → Evaluation Worker claim
  → 在 EVALUATION_DATABASE_URL 构建冻结评测语料
  → 执行真实检索与指标计算
  → 写 report_payload、metrics、official、passed
  → evaluation_runs=succeeded
  → operations=succeeded
```

失败时两张表在同一业务收口函数中更新为终态；Worker 崩溃后按租约恢复到队列。

### 4.2 报告存储

数据库中的 `report_payload` 是产品运行事实源。文件形式保留为 CLI 导出和版本化基线制品，不再要求 Web 运行时通过扫描仓库目录发现新报告。

现有文件报告继续只读兼容；新的数据库报告通过统一 Repository 返回。

## 5. API

新增：

| Method | Path | 用途 |
|---|---|---|
| `POST` | `/api/knowledge-bases/{kb}/index-versions/{version}/evaluation-runs` | 创建正式检索评测任务 |
| `GET` | `/api/knowledge-bases/{kb}/index-versions/{version}/evaluation-runs` | 查询该版本评测历史 |
| `GET` | `/api/knowledge-bases/{kb}/evaluation-runs/{run}` | 查询任务与报告详情 |
| `POST` | `/api/knowledge-bases/{kb}/evaluation-runs/{run}/retry` | 重试失败评测 |
| `POST` | `/api/knowledge-bases/{kb}/evaluation-runs/{run}/cancel` | 取消未开始的评测 |

保留：

- `POST .../index-versions/{version}/validations`：执行三层验证；
- `GET .../index-versions/{version}/validations`：查询验证历史；
- `POST .../index-versions/{version}/activate`：原子激活。

创建评测任务时后端重新计算候选版本配置指纹，不信任客户端传入的指纹。提交时若版本状态、配置、基线报告或数据集已变化，返回 `409`，要求刷新后重新确认。

## 6. 源文件缺失治理

已确认当前失败数据链：

```text
document_versions.status = ready
source_path 已记录
实际主机与 Docker uploads volume 均无该文件
重建 Worker read_bytes → FileNotFoundError
```

### 6.1 构建前检查

在创建 Document Snapshot 和 Index Build 前逐一核对本地上传来源：

- 文件存在；
- 路径仍位于受控上传根目录；
- 文件大小与已记录值一致；
- 可选校验内容 SHA-256。

任一 Included Document 缺失时不创建构建任务，返回 `SOURCE_FILE_MISSING` 和资料 ID、文件名；不向 UI 返回绝对路径。

### 6.2 Worker 防御

Worker 仍保留二次检查，防止文件在 Preview 与执行之间消失。异常转换为：

- `error_code=SOURCE_FILE_MISSING`；
- 用户文案：`源文件已丢失，无法重建索引`；
- 技术详情：仅在管理员详情弹框中展示受控相对路径。

### 6.3 恢复动作

- `重新上传`：创建新的 Document Version，再重新创建候选索引版本；
- `排除失效资料`：显式确认后将资料退出当前可索引集合；
- 不自动删除数据库记录或历史版本。

## 7. 前端信息架构

### 7.1 发布步骤条

抽取共享视觉 primitive，例如 `ProgressSteps`：

- `PipelineStepper` 继续由 Operation 的真实 `current_stage` 驱动；
- `ReleaseFlow` 继续由 Index Version、Evaluation Run、Validation Report 和 active 指针派生；
- 二者只共享圆点、连线、状态颜色与尺寸，不共享状态推导。

发布流程：

```text
索引定义 → 版本快照 → 索引构建 → 正式评测
→ 三层验证 → 待激活 → 当前生效
```

状态包含：已完成、进行中、未开始、阻塞、失败。每一步的原因通过 Tooltip 和读屏文本提供。

### 7.2 索引版本详情

版本表“详情”统一打开 `IndexVersionDetailDialog`，不跳转独立页面。

弹框规格：

- 桌面：`max-width: 900px`，内容区 `max-height: 82vh`；
- 移动：全屏；
- 支持遮罩、Escape、焦点陷阱与关闭后焦点返回；
- 内部区块：版本概览、配置快照、构建结果、正式评测与三层验证、生命周期；
- 技术 ID 默认缩写，支持复制和悬浮全文。

独立版本详情 URL 不再作为主交互入口；若保留深链，访问后在知识库详情上自动打开对应弹框。

### 7.3 正式评测与三层验证

候选版本构建成功后显示：

- `运行正式评测`：没有匹配报告且没有运行中任务；
- `查看评测进度`：任务已排队或执行中；
- `重新运行评测`：任务失败；
- `执行三层验证`：已有配置指纹匹配的正式报告；
- `激活`：三层验证通过、版本状态为 `ready`。

验证弹框不再只显示空下拉框。无报告时展示明确下一步，并提供“运行正式评测”主操作。

### 7.4 运行记录详情

运行记录包含 `index_build` 与 `index_evaluation`。Validate、Activate 不进入运行记录。

所有行“详情”统一打开 `OperationDetailDialog`：

- 摘要：类型、状态、版本、开始与结束时间；
- 进度：与表格一致的步骤条；
- 计数：总数、成功、失败、处理中；
- 构建详情：Document Index State 列表；
- 评测详情：数据集、候选配置、基线报告、指标与报告；
- 错误诊断：稳定错误码、用户说明、技术详情、建议动作；
- 原始 ID 收在可展开的“技术信息”中。

取消现有构建详情的表格下方展开，避免详情离开触发行且挤压整页布局。

### 7.5 表格溢出

扩展 `DataTable.Column`：

```ts
tooltip?: (row: T) => ReactNode
```

- 字符串和数字内容在截断列中自动提供原生全文标题；
- 组合内容通过 `tooltip` 显式提供；
- 文件名、任务 ID、报告 ID、配置指纹、错误原因优先接入；
- Tooltip 最大宽度受控，长路径允许换行；
- 键盘聚焦同样可以读取全文，不能只支持鼠标 Hover。

## 8. 三层验证

### 8.1 Integrity

- Document coverage；
- Snapshot inclusion / exclusion 一致性；
- missing document；
- orphan chunk；
- duplicate chunk；
- Chunk count consistency；
- ACL consistency。

### 8.2 Technical

- Vector、Keyword、Metadata 三条 Lane 完成；
- Vector / Keyword / Metadata / ACL / Citation 组件版本属于同一代；
- Embedding model 与 dimension 一致；
- 配置快照与组件清单完整。

### 8.3 Retrieval Quality

- 报告为正式来源；
- 报告配置指纹与候选版本一致；
- Recall@5、Recall@10、MRR、nDCG@10 证据存在；
- Metadata Filter Accuracy 证据存在；
- ACL leak count 为零；
- 相对当前 active 基线未超过允许回退。

验证通过后：

```text
validating / validation_failed
  → new immutable Validation Report
  → ready
```

验证失败后状态为 `validation_failed`，允许查看报告、重新运行正式评测、重新验证或重新构建。

## 9. 激活与回滚

Activate 仍在单个数据库事务中完成：

1. `FOR UPDATE` 锁定知识库与目标版本；
2. 核对目标版本为 `ready`；
3. 核对绑定 Validation Report 为 `pass` 且属于目标版本；
4. 当前 `active → previous`；
5. 目标 `ready → active`；
6. 更新知识库 active 指针；
7. 写生命周期事件；
8. 提交后新请求才看到新版本。

Rollback 继续复用同一原子切换能力，并保留内容快照差异确认。

## 10. 错误处理

必须使用稳定错误码和用户文案：

| 错误码 | 用户文案 |
|---|---|
| `SOURCE_FILE_MISSING` | 源文件已丢失，无法构建索引 |
| `EVALUATION_ALREADY_RUNNING` | 该版本已有正式评测正在运行 |
| `EVALUATION_DATABASE_UNAVAILABLE` | 正式评测环境暂不可用 |
| `EVALUATION_CONFIG_CHANGED` | 候选版本配置已变化，请刷新后重新运行 |
| `EVALUATION_FAILED` | 正式评测执行失败，请查看详情或重试 |
| `EVALUATION_REPORT_NOT_READY` | 正式评测尚未完成 |
| `EVALUATION_REPORT_MISMATCH` | 质量报告与候选版本配置不一致 |

错误详情不在表格行直接展开，统一进入详情弹框。

## 11. 测试与验收设计

后端覆盖：

- Evaluation Run 创建、并发唯一约束、租约恢复、重试与取消；
- 报告配置指纹由后端计算；
- 独立评测数据库不可用时安全失败；
- official 与 passed 分离；
- 三层验证接受匹配的正式报告并拒绝非正式或指纹不匹配报告；
- 源文件缺失在入队前阻断，Worker 二次检查返回稳定错误码；
- Validate → ready、Activate 原子切换、Rollback 不回归。

前端覆盖：

- 发布步骤条各状态；
- 版本详情弹框与深链；
- 正式评测创建、运行中、成功、失败、重试；
- 无报告时验证弹框提供正确下一步；
- 运行记录详情统一弹框；
- 截断文本鼠标和键盘均可查看全文；
- 桌面和移动布局；
- 空、加载、正常、失败状态。

按照项目 V5 规则，默认只运行项目并完成真实页面检查；测试、Lint、类型检查和生产构建仅在用户明确授权“轻量验证”或“完整验证”后执行。

## 12. 实施顺序

### P0

1. 数据库迁移：扩展 `evaluation_runs`、Operation 类型和可靠任务字段；
2. 正式评测 Repository、Service 与 Evaluation Worker；
3. 正式评测 API 与数据库报告读取；
4. official / passed 语义拆分及三层验证报告来源调整；
5. 源文件构建前检查和稳定错误码；
6. 正式评测、三层验证、激活页面动作闭环。

### P1

1. 共享步骤条视觉 primitive；
2. 索引版本详情弹框；
3. 运行记录统一详情弹框；
4. DataTable 溢出全文能力；
5. 桌面与移动页面调整。

### P2

1. 评测报告导出；
2. 运维级并发与资源配额；
3. 历史文件报告迁移为数据库报告。

业务链路与闭环复盘是本轮必交付结果，不属于可延后的 P2 能力。

## 13. 完成标准

- 管理员可以从候选版本直接发起正式检索评测；
- 评测任务可查看进度、失败原因并可靠重试；
- 成功报告自动与候选版本配置关联；
- 管理员可以继续执行三层验证；
- 验证通过后版本进入 `ready`，且必须手动激活；
- Activate 保持原子切换；
- 源文件缺失不会等到 Worker 深处才暴露原始 Python 异常；
- 索引版本和运行记录详情均使用一致弹框；
- 表格截断字段可以查看全文；
- 完成后重新输出当前业务链路、闭环结论和剩余缺失项。
