# 创建索引版本业务场景与页面向导设计

日期：2026-09-07

状态：待用户审阅，未授权实施

## 1. 决策

将当前没有上下文、固定提交 `chunk_size=500` 与 `chunk_overlap=50` 的“重建索引”按钮，替换为“创建索引版本”业务入口。

本阶段只开放后端真实支持的 Chunking 参数编辑；Parser、Embedding、Keyword、Metadata、Citation、ACL 配置只展示当前实际值和版本差异，不提供不会生效的控件。

创建一个 Index Version 必须同时冻结：

```text
Index Version = Config Snapshot + Document Snapshot
```

普通创建必须存在配置变化或 Document Snapshot 变化。相同输入的强制重建只放在管理员高级操作中，并要求填写原因。

## 2. 三个创建场景

| 场景 | 触发条件 | 创建原因 |
|---|---|---|
| 创建首个索引版本 | 知识库有可索引资料且没有 Active Version | `initial_build` |
| 创建候选版本 | 配置或 Document Snapshot 相对 Active 有变化 | `config_changed` / `document_snapshot_changed` / `component_upgraded` |
| 修复性重建 | 健康检查提供明确异常证据 | `consistency_repair` |

管理员可选择 `manual_rebuild` 强制以相同输入创建候选版本，但必须提交非空原因。修复性重建必须关联健康检查结果；本阶段如果健康检查报告对象尚未实现，只展示为未来能力，不创建假报告或假入口。

## 3. 五步向导

1. 选择场景：创建原因及其可执行条件。
2. 确认 Document Snapshot：当前 Snapshot、文档数、排除数及与 Active 的差异。
3. 确认配置：Chunking 可编辑，其余配置按 capability 只读。
4. 查看差异：Active 与 Candidate 的逐项差异、影响范围和阻塞原因。
5. 确认创建：创建不可变 Version、Build、Operation 和生命周期事件，开始异步构建。

配置和 Snapshot 均无变化时，普通创建被后端拒绝。前端禁用只用于解释原因，不是安全边界。

## 4. 创建后的生命周期

```text
building
  ├── build_failed → 查看失败 / 重新构建
  └── validating → 执行验证
                    ├── validation_failed → 查看报告 / 重新验证 / 重新构建
                    └── ready → 激活
                                └── active
```

验证和激活是两个独立动作。`validating` 必须有“执行验证”入口；`ready` 的“激活”只切换已经通过门禁的版本，不应再次隐式执行验证。

## 5. 后端边界

新增三个接口：

```text
GET  /api/knowledge-bases/{kbId}/index-version-creation-context
POST /api/knowledge-bases/{kbId}/index-versions/preview
POST /api/knowledge-bases/{kbId}/index-versions
```

Creation Context 返回 Active Version、最新/Active Document Snapshot、当前实际配置、可编辑 capability、健康摘要和阻塞原因。

Preview 接收创建原因和 Chunking 参数，读取当前可索引 Document Set，返回规范化配置、配置指纹、文档集合指纹、逐项差异、预计文档数和阻塞原因。Preview 不提前持久化 Document Snapshot，避免用户关闭向导后留下孤立快照。

Create 必须回传 Preview 得到的配置指纹和文档集合指纹。后端在事务内重新计算；任一指纹变化即拒绝创建。校验通过后，在同一事务边界内冻结 Document Snapshot，并创建 Version、Build、Operation 和 lifecycle event；任务入队沿用现有队列和 Worker。

## 6. 当前缺陷先行修复

1. `aggregate_index_build()` 把 Version 的 `validating` 错误映射成 Build `failed`。Build 覆盖完整时应保持成功终态，Version 独立进入 `validating`。
2. 前端虽然已有创建 Validation 的 API client，但 `validating` 行没有操作入口。
3. `ready` 行当前打开“验证并激活”，与已拆分的状态机重复。应改为只激活已持久化通过报告的 Version。

## 7. 非目标

- 不在本阶段实现可切换的 Parser 或 Embedding 执行器。
- 不增加假的 Metadata、Citation、ACL Schema 编辑器。
- 不实现自动 Activate。
- 不改造 Data Sync Pipeline。
- 不执行完整验证、容器构建、Commit、Push、Tag、Release 或部署，除非用户另行授权。
