# V5-9 Evaluation 与 Bad Case 治理设计

## 目标

在现有检索评测、回答评测和失败历史基础上，建立统一的 Evaluation Run、工程指标、质量门与 Bad Case 回归闭环。

## 边界

- 页面只读取、筛选和治理评测结果，不从浏览器启动重量模型评测。
- PostgreSQL 承载企业治理数据；历史 JSON 报告继续只读兼容。
- 确定性规则负责安全质量门；LLM Judge 只能作为辅助信号。
- 本阶段不做自动调参、自动部署、Prometheus/Grafana 或 V5-10 总链路验收。
- 页面首次加载不显示全页 Loading；切换报告使用局部 Loading。

## 统一模型

Evaluation Run 绑定 Dataset Version、Git Commit、Prompt、Parser、Chunking、Index 与模型版本。Report 汇总 retrieval、answer、pipeline、security 四类指标，并保存不可变质量门结论。

Bad Case 使用稳定 Case ID，来源包括在线问答、离线评测和人工创建。状态机为 `new → confirmed → fixing → resolved → regression_added`，允许转为 `ignored`；回归失败会重新打开。

## 指标

- Retrieval：Recall@K、MRR、nDCG@K、Hybrid 收益、Rerank 收益、Metadata Filter 正确率、Query Rewrite 成功/回退率、无结果率、ACL 泄漏数。
- Answer：正确性、完整性、忠实度、引用有效率、引用支持率、声明引用覆盖率、无支持声明率、拒答准确率、冲突识别率、失败策略稳定性。
- Pipeline：同步延迟、资源增删改、解析/分类/索引失败率、积压、单文档耗时、重试、死信、游标恢复率、Index 激活耗时。

## 页面

统一评测中心使用 `总览 / 检索质量 / 回答质量 / 工程指标 / Bad Case` Tabs。指标使用紧凑表格与小型进度条；Bad Case 支持筛选、详情、状态治理、修复提交和加入回归集。普通成员只读，管理员可治理。

## 质量门

ACL 泄漏必须为 0；Retrieval、Citation、拒答指标不得低于冻结阈值；无证据断言率、Pipeline 失败率和延迟不得超过上限；核心 Bad Case 回归必须通过。任一安全门失败，总结论失败。

## 验证

运行指标计算、状态机、权限、回归去重、ACL 安全门专项 Pytest，以及前端 Test、ESLint、TypeScript 和 `git diff --check`。不执行完整验证、生产构建或容器构建。
