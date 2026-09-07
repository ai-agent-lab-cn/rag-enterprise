# 创建索引版本业务场景与页面向导 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将固定参数的“重建索引”按钮升级为可解释、可预览、可验证并能安全激活的“创建索引版本”完整业务闭环。

**Architecture:** 后端提供 Creation Context、Candidate Preview 和原子 Create 三个边界，统一计算 capability、配置/Snapshot 差异和阻塞原因。前端使用五步向导消费这些结果，不自行复制领域规则；构建、验证、激活保持三个独立动作。

**Tech Stack:** FastAPI、Pydantic、Python 3、PostgreSQL/psycopg、React、TypeScript、Vite、现有 UI 组件与 pytest/Vitest。

**Spec:** `docs/design/index-version-creation-wizard.md`，并遵循 `docs/design/index-governance-roadmap.md` 已完成的 P0A 状态机与 Document Snapshot 约束。

## Global Constraints

- 当前分支为 `main`，工作区存在大量用户未提交改动；只做局部增量修改，不清理、不重置、不覆盖无关内容。
- 本阶段固定 6 个任务，用户明确启动后连续执行；仅遇到真实阻塞或改变范围的重大决策时暂停。
- 本阶段只开放 `chunk_size`、`chunk_overlap` 编辑；Parser、Embedding、Keyword、Metadata、Citation、ACL 只读展示。
- `Index Version = Config Snapshot + Document Snapshot`；Version 和 Snapshot 创建后不可变。
- 普通创建必须存在 Config 或 Snapshot 差异；强制重建仅管理员可用且要求原因。
- Validation 与 Activate 分离；`ready` 只由三层门禁通过产生。
- 不增加多 Parser、多 Embedding、自动 Activate、Data Sync 改造或假健康报告。
- 默认只执行相关轻量验证；不执行完整测试、容器构建、Commit、Push、Tag、Release 或部署。
- 用户在 VS Code 检查并明确回复“提交代码”前，不创建 Commit。

## File Map

- `backend/app/pipeline_governance.py`：修正 Build 聚合终态，不把 Version `validating` 映射为 Build `failed`。
- `backend/app/index_versions.py`：提供创建上下文、Candidate Preview、Config/Document Set 指纹、差异和原子创建领域函数。
- `backend/app/schemas.py`：Creation Context、Preview、Create、Activate 的请求响应模型。
- `backend/app/main.py`：三个创建接口；将 Activate 改为只切换通过验证的 ready Version。
- `backend/app/service.py`：向知识库详情聚合当前创建提示所需的最小信息；不承载创建规则。
- `backend/migrations/0037_index_version_creation_reason.sql`：增加创建原因、强制原因和预览校验所需字段/约束。
- `frontend/src/types.ts`：创建上下文、预览、创建请求和 capability 类型。
- `frontend/src/api.ts`：三个创建接口与分离后的 Validate/Activate client。
- `frontend/src/components/IndexVersionCreationWizard.tsx`：五步向导，单一职责组件。
- `frontend/src/components/KnowledgeBaseDetailPage.tsx`：入口、状态操作和向导装配。
- `frontend/src/components/ui/PipelineStepper.tsx`：仅在现有映射不能准确表达 Build→Validate→Activate 时做局部调整。
- `backend/tests/test_index_versions.py`：领域函数、差异、无变化阻塞、强制创建和激活约束。
- `backend/tests/test_document_snapshots.py`：Snapshot 绑定和重试不可漂移。
- `backend/tests/test_api.py`：接口、权限、错误码和 Validate/Activate 分离。
- `frontend/src/App.test.tsx`：向导、状态操作、错误态和 API payload。

---

### Task 1: 修复 Build 状态和验证入口断点

**Files:**
- Modify: `backend/app/pipeline_governance.py:410`
- Modify: `backend/app/main.py:1630`
- Modify: `backend/app/schemas.py:950`
- Modify: `frontend/src/components/KnowledgeBaseDetailPage.tsx:237`
- Modify: `frontend/src/api.ts:237`
- Test: `backend/tests/test_document_snapshots.py`
- Test: `backend/tests/test_api.py`
- Test: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: `finalize_building_version(database_url, index_version_id) -> str`、现有 `POST .../validations`。
- Produces: Build 成功终态与 Version `validating` 独立；`validating` 行可执行验证；`ready` 行只执行激活。

- [ ] **Step 1: 写 Build/Version 独立状态的失败测试**

在 `backend/tests/test_document_snapshots.py` 构造一个文档完整成功的 Build，调用 `aggregate_index_build()` 后断言：

```python
assert index_build_status == "ready"
assert index_version_status == "validating"
```

- [ ] **Step 2: 运行该测试并确认当前失败**

Run: `uv run pytest backend/tests/test_document_snapshots.py -k "build_status_remains_ready_while_version_validates" -q`

Expected: FAIL，当前 Build 被写成 `failed`。

- [ ] **Step 3: 修正聚合映射**

在 `aggregate_index_build()` 中只根据文档构建结果决定 Build 终态。`finalize_building_version()` 的返回值只推进 Version，不再反写 Build 为 `failed`：

```python
version_status = finalize_building_version(database_url, str(version[0]))
if version_status == "build_failed":
    # 仅覆盖/任务确实失败时写 failed；完整构建进入 validating 时保持 ready。
    update_build_status("failed")
```

- [ ] **Step 4: 写验证与激活分离的 API 测试**

断言：

```python
POST validations: validating -> ready
PUT active: ready + stored pass report -> active
PUT active with validating -> 409 INDEX_VERSION_NOT_READY
PUT active does not create another validation_report
```

- [ ] **Step 5: 将 Activate 请求改为不再要求外部评测报告 ID**

新增空请求或无 body 的激活动作，调用：

```python
switch_to_version(database_url, index_version_id, audit, actor)
```

保留 `POST .../validations` 接收 `evaluation_report_id` 并调用 `validate_index_version()`。

- [ ] **Step 6: 补齐页面操作**

`governedIndexVersionColumns` 映射：

```typescript
validating          -> 执行验证
validation_failed   -> 重新验证
ready               -> 激活
```

验证弹窗选择正式评测报告；激活弹窗只确认版本 ID 和状态，不再选择报告。

- [ ] **Step 7: 运行 Task 1 轻量验证**

Run:

```bash
uv run pytest backend/tests/test_document_snapshots.py backend/tests/test_index_versions.py backend/tests/test_api.py -q
cd frontend && npm test -- --run
```

Expected: 相关测试通过；没有新增失败。

### Task 2: 建立 Creation Context 与 Capability

**Files:**
- Modify: `backend/app/index_versions.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Test: `backend/tests/test_index_versions.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `active_config_drift()`、`document_snapshots`、当前 `index_settings`、Active Version。
- Produces: `get_index_version_creation_context(database_url, knowledge_base_id, chunk_size, chunk_overlap) -> dict[str, Any]` 和 GET endpoint。

- [ ] **Step 1: 定义响应模型及稳定字段**

在 `schemas.py` 新增：

```python
class IndexVersionCapability(BaseModel):
    field: str
    editable: bool
    value: object
    reason: str | None = None

class IndexVersionCreationContextResponse(BaseModel):
    scenario: Literal["initial_build", "candidate", "no_change"]
    active_version: IndexVersionResponse | None
    latest_document_snapshot: dict[str, object] | None
    active_document_snapshot: dict[str, object] | None
    document_diff: dict[str, int]
    capabilities: list[IndexVersionCapability]
    creation_allowed: bool
    blocked_reasons: list[str]
```

- [ ] **Step 2: 写 Context 领域测试**

覆盖：无文档、首个版本、配置漂移、Snapshot 变化、完全无变化、存在 building Candidate 六种情况。

- [ ] **Step 3: 实现统一 Context 计算**

规则：

```text
无可索引文档                     -> blocked: INDEX_BUILD_EMPTY_KNOWLEDGE_BASE
已有 building/validating Candidate -> blocked: INDEX_VERSION_IN_PROGRESS
无 active 且有文档               -> initial_build
配置或 Snapshot 有变化            -> candidate
均无变化                          -> no_change
```

capability 中只有 `chunk_size`、`chunk_overlap` 为 `editable=true`。

- [ ] **Step 4: 暴露 GET endpoint**

```text
GET /api/knowledge-bases/{knowledge_base_id}/index-version-creation-context
```

保持管理员与知识库访问校验；PostgreSQL 不可用时返回 `POSTGRES_REQUIRED`。

- [ ] **Step 5: 增加前端类型与 client**

```typescript
getIndexVersionCreationContext(id: string): Promise<IndexVersionCreationContext>
```

- [ ] **Step 6: 运行 Task 2 轻量验证**

Run: `uv run pytest backend/tests/test_index_versions.py backend/tests/test_api.py -q`

Expected: Creation Context 场景、阻塞和权限测试通过。

### Task 3: 实现 Candidate Preview 和差异计算

**Files:**
- Modify: `backend/app/index_versions.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Test: `backend/tests/test_index_versions.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: Task 2 Creation Context、`config_fingerprint()`、Document Snapshot 指纹。
- Produces: `preview_index_version_candidate(...) -> IndexVersionCandidatePreview`、稳定的 Config/Document Set 指纹。

- [ ] **Step 1: 定义 Preview 请求响应**

```python
class IndexVersionPreviewRequest(BaseModel):
    reason: Literal[
        "initial_build", "config_changed", "document_snapshot_changed",
        "component_upgraded", "manual_rebuild"
    ]
    document_snapshot_id: str | None = None
    chunk_size: int = Field(ge=100, le=4000)
    chunk_overlap: int = Field(ge=0, le=1000)
    force: bool = False
    force_reason: str | None = Field(default=None, max_length=500)
```

响应包含 normalized config、config fingerprint、document set fingerprint、diff、estimated_documents、blocked_reasons 和 creation_allowed。

- [ ] **Step 2: 写规范化和差异测试**

覆盖 `chunk_overlap >= chunk_size`、Snapshot 不属于 KB、配置变化、文档变化、无变化、force 无原因、非管理员 force。

- [ ] **Step 3: 实现可重现的 Document Set 指纹**

按 `document_id`、`current_version_id`、`content_sha256` 排序并规范化序列化，计算 SHA-256：

```json
{
  "knowledge_base_id": "kb_x",
  "config_fingerprint": "...",
  "document_set_fingerprint": "...",
  "reason": "config_changed"
}
```

Preview 不创建 `document_snapshots` 记录。最终创建时在同一事务内重算指纹并冻结 Snapshot，避免预览产生孤立记录。

- [ ] **Step 4: 暴露 Preview endpoint**

```text
POST /api/knowledge-bases/{knowledge_base_id}/index-versions/preview
```

后端返回全部差异与阻塞原因；前端不得自行决定是否允许提交。

- [ ] **Step 5: 增加前端 client**

```typescript
previewIndexVersion(id: string, payload: IndexVersionPreviewRequest): Promise<IndexVersionCandidatePreview>
```

- [ ] **Step 6: 运行 Task 3 轻量验证**

Run: `uv run pytest backend/tests/test_index_versions.py backend/tests/test_api.py -q`

Expected: Preview、Token、差异和阻塞测试通过。

### Task 4: 实现五步创建向导

**Files:**
- Create: `frontend/src/components/IndexVersionCreationWizard.tsx`
- Modify: `frontend/src/components/KnowledgeBaseDetailPage.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Test: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: Task 2 `IndexVersionCreationContext`、Task 3 `IndexVersionCandidatePreview`。
- Produces: `IndexVersionCreationWizard`，只在最后一步调用 Create endpoint。

- [ ] **Step 1: 写入口场景测试**

断言按钮文案：

```text
initial_build -> 创建首个索引版本
candidate     -> 创建索引版本
no_change     -> 禁用并显示没有检测到变化
```

- [ ] **Step 2: 写五步导航测试**

覆盖前进、返回、关闭后重置、加载失败、Preview 阻塞、重复提交保护。

- [ ] **Step 3: 创建独立 Wizard 组件**

Props：

```typescript
interface IndexVersionCreationWizardProps {
  knowledgeBaseId: string;
  context: IndexVersionCreationContext;
  open: boolean;
  onClose(): void;
  onCreated(result: IndexVersionCreateResult): void;
}
```

内部步骤固定为 `scenario | snapshot | configuration | diff | confirm`。

- [ ] **Step 4: 实现能力感知配置页**

Chunk Size/Overlap 使用 `Input`；其余字段以只读 Definition List 展示“由当前系统版本决定”。不得渲染无效 Select。

- [ ] **Step 5: 实现 Preview 与最终确认页**

进入 Diff 步骤时调用 Preview；展示 Active/Candidate、文档数量、指纹短值、影响说明、blocked reasons。只有后端 `creation_allowed=true` 且两个指纹存在时允许确认。

- [ ] **Step 6: 替换旧按钮**

删除 `createKnowledgeBaseIndexBuild(id, 500, 50)` 的直接调用；由 Context 决定入口文案和禁用原因。

- [ ] **Step 7: 运行 Task 4 轻量验证**

Run:

```bash
cd frontend
npm test -- --run
npm run lint
```

Expected: 向导交互和既有前端测试通过，ESLint 无新增错误。

### Task 5: 实现原子 Create 和生命周期记录

**Files:**
- Create: `backend/migrations/0037_index_version_creation_reason.sql`
- Modify: `backend/app/index_versions.py`
- Modify: `backend/app/postgres_documents.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/components/IndexVersionCreationWizard.tsx`
- Test: `backend/tests/test_index_versions.py`
- Test: `backend/tests/test_document_snapshots.py`
- Test: `backend/tests/test_api.py`
- Test: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: Task 3 Config/Document Set 指纹、现有 `enqueue_rebuild()` 和 `create_building_version()`。
- Produces: `create_index_version_candidate(...) -> dict[str, object]` 和 POST Create endpoint。

- [ ] **Step 1: 写迁移测试与 SQL**

为 `index_versions` 增加：

```sql
creation_reason text NOT NULL DEFAULT 'legacy',
force_reason text NULL,
requested_by text NULL
```

添加 CHECK：合法 reason；`creation_reason='manual_rebuild'` 时 `force_reason` 非空。保持迁移 additive，不删除旧字段。

- [ ] **Step 2: 写 Create 事务测试**

断言一次创建得到同属一个 KB 的：

```text
Document Snapshot
Index Version(status=building)
Index Build(attempt_no=1)
Operation
Lifecycle Event(created/build_started)
Index Jobs
```

任一步注入失败时全部不落库；相同 Idempotency-Key 重试返回同一结果。

- [ ] **Step 3: 验证预览后的输入未漂移**

重新读取 Config 和 Snapshot fingerprint；不一致分别返回：

```text
INDEX_CONFIG_CHANGED_AFTER_PREVIEW
DOCUMENT_SNAPSHOT_CHANGED_AFTER_PREVIEW
```

- [ ] **Step 4: 实现原子领域动作**

将 `enqueue_rebuild()` 中创建 Version/Build/Operation/Jobs 的数据库写入收拢到清晰事务边界；外部或耗时执行仍由提交后的 Worker 完成。不得在 Controller 直接拼 SQL。

- [ ] **Step 5: 暴露 Create endpoint**

```text
POST /api/knowledge-bases/{knowledge_base_id}/index-versions
```

请求包含 reason、规范化 Chunking、expected_config_fingerprint、expected_document_set_fingerprint、force_reason、Idempotency-Key。

- [ ] **Step 6: 接通 Wizard 最终提交**

成功后关闭向导、刷新 Version/Build/Operation，并自动打开新 Candidate 的详情或定位其行。

- [ ] **Step 7: 运行 Task 5 轻量验证**

Run:

```bash
uv run pytest backend/tests/test_index_versions.py backend/tests/test_document_snapshots.py backend/tests/test_api.py -q
cd frontend && npm test -- --run
```

Expected: 原子性、幂等、配置/文档集合漂移拒绝和页面提交测试通过。

### Task 6: 收口文案、状态与相关轻量验证

**Files:**
- Modify: `frontend/src/components/KnowledgeBaseDetailPage.tsx`
- Modify: `frontend/src/components/IndexVersionCreationWizard.tsx`
- Modify if required: `frontend/src/components/ui/PipelineStepper.tsx`
- Modify: `docs/design/index-governance-roadmap.md`
- Test: `backend/tests/test_module_boundaries.py`
- Test: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: Tasks 1–5 的完整闭环。
- Produces: 可供用户在 VS Code 审查的本地改动与轻量验证证据。

- [ ] **Step 1: 统一页面术语**

必须使用：

```text
创建索引版本
创建首个索引版本
构建中
验证中
验证失败
待激活
当前生效
上一版本
修复性重建（仅有真实健康证据时）
```

删除普通入口中的“重建索引”和“验证并激活”混合文案。

- [ ] **Step 2: 核对所有状态操作矩阵**

```text
building          -> 详情
build_failed      -> 详情 / 重新构建
validating        -> 执行验证
validation_failed -> 详情 / 重新验证 / 重新构建
ready             -> 激活
active            -> 详情
previous          -> 回滚 / 退役
retired           -> 清理
cleaned           -> 详情
```

- [ ] **Step 3: 更新路线图事实边界**

只记录本阶段完成的能力和实际验证结果；将多 Parser、多 Embedding、健康报告和自动激活继续标为未来阶段。

- [ ] **Step 4: 执行相关后端轻量验证**

Run:

```bash
uv run pytest backend/tests/test_index_versions.py backend/tests/test_document_snapshots.py backend/tests/test_api.py backend/tests/test_module_boundaries.py -q
uv run ruff check backend/app/index_versions.py backend/app/pipeline_governance.py backend/app/main.py backend/app/schemas.py backend/tests/test_index_versions.py backend/tests/test_document_snapshots.py backend/tests/test_api.py
```

Expected: 全部通过，无新增失败。

- [ ] **Step 5: 执行相关前端轻量验证**

Run:

```bash
cd frontend
npm test -- --run
npm run lint
npm run build
```

Expected: 测试、ESLint 和生产构建全部通过。

- [ ] **Step 6: 交付本地检查清单**

报告修改文件、六项完成状态、实际验证结果、未实施范围和仍存在风险。停止在本地工作区，等待用户使用 VS Code 检查；不得 Commit 或 Push。
