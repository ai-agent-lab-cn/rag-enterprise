# V5-10 End-to-End Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立企业 RAG 真实链路验收运行、证据报告与统一页面。

**Architecture:** PostgreSQL 持久化验收运行；纯函数根据真实运行快照生成八步结论；FastAPI 提供只读列表/详情和管理员启动接口；React 在评测中心展示运行进度与证据，并在知识库页面补齐 Index Version 视图。

**Tech Stack:** Python 3.12、FastAPI、PostgreSQL、Pydantic、React、TypeScript、Vitest。

**Spec:** `docs/superpowers/specs/2026-08-30-v5-10-end-to-end-acceptance-design.md`

## Global Constraints

- 固定八步连续实施，不要求逐步确认。
- 缺少真实证据时输出 blocked，不生成假成功。
- 普通成员只读，管理员启动验收。
- 默认轻量验证，不做生产构建或容器构建。

---

### Task 1: 验收模型与 Schema V14
- [x] 增加验收运行、步骤、证据和状态模型。
- [x] 增加幂等 Schema V14 迁移与版本配置。

### Task 2: 真实数据源证据
- [x] 核对 S3 兼容数据源和连接测试证据。
- [x] 缺少真实外部数据源时明确 blocked。

### Task 3: 增量同步证据
- [x] 聚合全量、增量、删除、重试与游标证据。
- [x] 不以文件上传记录冒充外部同步。

### Task 4: 解析与索引证据
- [x] 聚合 Parser/Chunk 版本与失败状态。
- [x] 核对 active Index Version 并补齐前端只读视图。

### Task 5: Retrieval 与 ACL 证据
- [x] 关联 Query Rewrite、Hybrid/Rerank 和 Metadata 指标。
- [x] ACL 泄漏不为 0 时安全门失败。

### Task 6: 可信回答证据
- [x] 关联回答、拒答、冲突与 Citation 指标。
- [x] 引用安全指标失败时总验收失败。

### Task 7: Evaluation 与 Bad Case 回归
- [x] 总验收写入 evaluation_runs。
- [x] 回归失败重开 Bad Case，并在报告中保留证据。

### Task 8: 页面与轻量验收
- [x] 增加链路验收 Tab、运行历史、步骤进度和阻塞说明。
- [x] 统一上传文件格式提示。
- [x] 运行专项后端与前端质量门，不提交代码。
