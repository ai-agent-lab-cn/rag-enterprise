# 创建索引版本向导修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复创建索引版本五步向导的配置来源、快照一致性、文档范围确认、异步错误恢复和最终提交闭环。

**Architecture:** 使用知识库级 `index_definitions` 持久化当前真正可编辑的 Chunking；其余配置继续从运行事实聚合。Preview 仍保持只读，Create 在同一事务内校验三指纹并持久化 Definition、Snapshot、Version、Build 和 Jobs。前端只消费后端返回的 capability、结构化阻塞和创建结果。

**Tech Stack:** FastAPI、Pydantic、PostgreSQL、psycopg、React、TypeScript、Vitest。

**Spec:** `docs/design/2026-09-17-index-version-creation-wizard-remediation.md`

## Global Constraints

- 保留工作区现有未提交修改，不重置、不覆盖无关文件。
- 不 Commit、不 Push。
- 不增加假的 Parser、Embedding 或健康检查能力。
- Active 同步写入边界只记录后续重构，不在本轮做不完整禁写。
- 本轮按用户约定不运行测试、Lint、类型检查、构建或完整页面验收。

---

### Task 1: 持久化知识库级 Chunking Definition

**Files:**
- Create: `backend/migrations/0040_index_definitions.sql`
- Modify: `backend/app/index_versions.py`
- Modify: `backend/app/postgres_documents.py`
- Test: `backend/tests/test_index_versions.py`

**Interfaces:**
- Produces: `read_effective_chunking_definition(connection, knowledge_base_id, defaults) -> tuple[int, int, str]`
- Produces: `upsert_index_definition(connection, knowledge_base_id, chunk_size, chunk_overlap, requested_by) -> str`

- [x] 写回归测试：Definition 缺失时回退默认值，创建候选后持久化所选 Chunking，重新读取不回退默认值。
- [x] 新增 `0040_index_definitions.sql`，只保存知识库级 Chunking 和更新时间/更新人。
- [x] Creation Context 从 Definition 读取有效 Chunking；Preview 参数仍可覆盖目标值。
- [x] Create 事务内写 Definition，并让后续 Context 返回持久值。
- [ ] 运行 `uv run pytest backend/tests/test_index_versions.py -q`。

### Task 2: 统一 Config Snapshot 与创建事实

**Files:**
- Modify: `backend/app/index_versions.py`
- Modify: `backend/app/postgres_documents.py`
- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_index_versions.py`

**Interfaces:**
- Produces: `parser = {schema_version: string, runtime_versions: string[]}`
- Produces: `excluded_documents_acknowledged: bool` on preview/create payloads and Version snapshot.

- [x] 写回归测试：Preview 与持久化 Version 的 Parser Snapshot 结构相同。
- [x] 写回归测试：存在排除资料且未确认时 Create 被拒绝。
- [x] 统一 Parser Snapshot，并在 Version 记录排除确认事实。
- [x] 扩展请求/响应模型且保持旧 Version 读取兼容。
- [ ] 运行相关后端定向测试。

### Task 3: 恢复相同内容重新上传时缺失的源文件

**Files:**
- Modify: `backend/app/postgres_documents.py`
- Test: `backend/tests/test_postgres_foundation.py`

**Interfaces:**
- Consumes: 现有 `write_private_file()` 和源路径 containment 校验。
- Produces: 相同 `content_sha256` 且物理文件缺失时恢复文件，不创建重复 Version/Job。

- [x] 写回归测试：missing → same-content reupload 恢复文件。
- [x] 写回归测试：现有文件保持幂等、越界路径拒绝、Version/Job 不重复。
- [x] 在内容哈希短路前完成受控恢复。
- [ ] 运行源文件恢复定向测试。

### Task 4: 修复五步向导信息结构与 Preview 状态

**Files:**
- Modify: `frontend/src/components/IndexVersionCreationWizard.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Test: `frontend/src/components/IndexVersionCreationWizard.test.tsx`

**Interfaces:**
- Consumes: Definition capabilities、结构化 exclusions、Config/Document/Release fingerprints。
- Produces: 创建场景、目标配置、文档快照、变更与影响、确认并构建五步 UI。

- [x] 为主要五步提交路径、字段展示和排除确认补充回归测试。
- [x] 重命名五步并按“切片配置 / 模型配置 / 索引结构版本”分组。
- [x] 使用 capability 展示可编辑性和配置来源；长值省略并用 Tooltip/title 展示完整值。
- [x] 文档范围加入排除确认；源文件丢失展示明确恢复动作说明。
- [x] 把字节估算改成“粗略输入规模”，不冒充 Token 或准确 Chunk 数。
- [x] 最终页完整汇总并提供短指纹/完整值。
- [x] 增加独立 Preview Loading、请求序号和“重新生成预览”。
- [ ] 运行向导定向测试。

### Task 5: 修复创建成功后的页面闭环

**Files:**
- Modify: `frontend/src/components/KnowledgeBaseDetailPage.tsx`
- Modify: `frontend/src/components/IndexVersionCreationWizard.tsx`
- Modify: `frontend/src/types.ts`
- Test: `frontend/src/App.test.tsx`

**Interfaces:**
- `onCreate(preview) -> Promise<IndexVersionBuildResult>`
- Produces: 成功 Toast、新 Version/Build 定位信息、刷新失败与创建失败分离。

- [ ] 补充页面层回归测试：创建成功返回 ID，刷新失败不显示“创建失败”。
- [x] 保留 Create 返回值，关闭向导并刷新列表。
- [x] 成功后显示 Version/Build 标识并打开对应版本详情；刷新失败显示独立提示。
- [ ] 运行相关前端定向测试。

### Task 6: 定向验证与事实更新

**Files:**
- Modify: `docs/design/2026-09-17-index-version-creation-wizard-remediation.md`

- [ ] 运行 Task 1–3 涉及的后端定向测试。
- [ ] 运行 Task 4–5 涉及的前端定向测试。
- [ ] 通过实际数据库检查迁移顺序和新增字段的旧数据兼容。
- [x] 更新设计文档的实际完成项和未实施的 Active/Data Sync 解耦风险。
- [x] 输出本地改动、验证结果和剩余项；不 Commit、不 Push。
