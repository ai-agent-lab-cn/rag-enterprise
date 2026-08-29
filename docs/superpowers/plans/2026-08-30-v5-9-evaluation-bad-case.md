# V5-9 Evaluation 与 Bad Case 治理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一企业 RAG 的评测报告、工程指标、质量门和 Bad Case 回归治理。

**Architecture:** 保留现有不可变 JSON 正式报告，同时新增统一聚合契约；PostgreSQL/历史仓储负责 Bad Case 治理，React 评测中心统一展示并按权限提供治理操作。

**Tech Stack:** FastAPI、Pydantic、PostgreSQL、React、TypeScript、Pytest、Vitest。

**Spec:** `docs/superpowers/specs/2026-08-30-v5-9-evaluation-bad-case-design.md`

## Global Constraints

- 固定 8 步连续执行，不逐步等待用户回复。
- 首次页面加载不展示全页 Loading；报告切换使用局部状态。
- 页面不启动重量模型评测。
- 默认轻量验证，不执行完整构建或容器构建。
- 完成后保留本地修改，等待“提交代码”。

### Task 1: 统一评测契约
- [x] 增加统一报告摘要、版本上下文和指标分组。
- [x] 兼容读取现有 Retrieval 与 Answer 报告。

### Task 2: Retrieval 指标
- [x] 增加 nDCG、Hybrid/Rerank 收益、Filter、Rewrite、无结果和 ACL 指标契约。
- [x] 增加安全质量门测试。

### Task 3: Answer 指标
- [x] 增加来源冲突识别率并统一 Citation 指标。
- [x] 确定性安全指标决定报告放行。

### Task 4: Pipeline 指标
- [x] 从同步运行记录聚合工程指标。
- [x] 提供按知识库与数据源筛选的只读 API。

### Task 5: Bad Case 模型
- [x] 增加稳定 Case、严重级别、根因、负责人和处理状态。
- [x] 在线失败与现有历史记录兼容迁移。

### Task 6: 回归闭环
- [x] 实现状态机、修复提交、去重加入回归集和失败重开。
- [x] 管理员可写、普通成员只读。

### Task 7: 统一评测中心页面
- [x] 增加总览、检索、回答、工程指标和 Bad Case Tabs。
- [x] 实现紧凑表格、筛选、详情和局部状态。

### Task 8: 轻量验收
- [x] 后端专项 Pytest 与 Ruff。
- [x] 前端 Test、ESLint、TypeScript。
- [x] `git diff --check`，不提交代码。
