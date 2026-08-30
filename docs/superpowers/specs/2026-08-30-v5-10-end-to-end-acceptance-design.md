# V5-10 真实链路总验收设计

## 目标

把 V5-2～V5-9 已有能力组织成一条可复核的企业 RAG 验收链路，保存运行上下文、八个阶段的结论、证据与明确缺口；不以静态文案冒充未发生的真实操作。

## 核心设计

- PostgreSQL 保存 `acceptance_runs`，每次运行绑定 Git Commit、Schema、知识库与创建人。
- 验收器读取真实数据源、同步运行、解析版本、索引版本、评测报告和回归集状态，生成八个稳定步骤。
- 步骤状态为 `passed / failed / blocked`；缺少真实 S3、增量删除或 ACL 证据时必须显示 blocked。
- 评测中心增加“链路验收”Tab；首次进入不显示整页 Loading，运行时显示局部进度。
- Index Version 在知识库“版本治理”Tab 独立展示，不再把 Document Version 当作 Index Version。
- V5-9 `evaluation_runs` 由总验收写入；回归失败必须重开对应 Bad Case。

## 边界

- 本阶段不自动创建或修改用户的 S3 对象，不自动改变 ACL，不自动切换索引。
- 没有真实外部数据源或增量证据时，验收结果必须阻塞并给出下一步。
- 不做容器构建、K8s 演练、生产部署、OCR、定时同步或新增 SaaS 连接器。

## 验收

- 八个步骤顺序稳定，安全门失败时总结果失败，前置证据缺失时总结果 blocked。
- 运行记录可分页读取，详情包含证据与未实现能力。
- 普通成员只读，管理员可以启动验收。
- Index Version、文件格式提示和评测中心页面与后端能力一致。
- 后端专项 Pytest/Ruff；前端 Vitest/ESLint/TypeScript；`git diff --check`。
