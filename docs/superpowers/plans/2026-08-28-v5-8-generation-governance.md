# V5-8 Generation Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立生成治理和 Citation 回到原文的全链路。

**Architecture:** 扩展现有 Prompt 状态协议和 QueryResponse，不新增模型调用；PostgreSQL Repository 提供 ACL 保护的 Citation 定位读取，React 问答页负责状态解释和原文弹窗。

**Tech Stack:** FastAPI、Pydantic、PostgreSQL/pgvector、React、TypeScript、Vitest、Pytest。

**Spec:** `docs/superpowers/specs/2026-08-28-v5-8-generation-governance-design.md`

## Global Constraints

- 固定 8 步连续执行，不逐步等待用户回复。
- 首次页面加载不展示 Loading；Citation 请求使用局部状态。
- 默认轻量验证，不执行完整构建或容器构建。
- 完成后保留本地修改，等待“提交代码”。

### Task 1: 治理契约
- [x] 为回答状态与治理结果增加强类型 Schema。
- [x] 增加引用编号和声明引用覆盖的失败测试。
- [x] 实现确定性输出校验。

### Task 2: Citation 模型
- [x] 扩展 Source 的版本、哈希和原文位置字段。
- [x] 从 Chunk metadata 和文档版本补齐响应。

### Task 3: 生成前治理
- [x] 记录最低证据阈值、实际证据数和 ACL/版本再校验结果。
- [x] 证据不足时不调用生成模型。

### Task 4: 生成后治理
- [x] 输出合法引用编号、引用有效性和声明覆盖结果。
- [x] 冲突状态至少引用两个真实来源。

### Task 5: 原文定位 API
- [x] Repository 按知识库、当前版本、检索状态和 ACL 查询单个 Chunk。
- [x] 新增 Citation API 和 404 权限隐藏语义。

### Task 6: 问答页面
- [x] Citation 点击读取 API 并展示局部加载、正常和失败状态。
- [x] 原文弹窗展示版本、哈希和结构化位置。

### Task 7: 技术详情
- [x] 展示证据阈值、引用校验、声明覆盖和降级原因。

### Task 8: 轻量验收
- [x] 后端专项 Pytest 与 Ruff。
- [x] 前端 Test、ESLint、TypeScript。
- [x] `git diff --check`，不提交代码。
