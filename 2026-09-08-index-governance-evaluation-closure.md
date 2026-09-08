# Index Governance Evaluation Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐候选索引版本从正式检索评测、三层验证到原子激活的产品内闭环，并统一索引治理步骤条、版本详情、运行记录和长内容展示。

**Architecture:** 数据同步、索引构建、质量评测、发布治理保持四层分离。扩展 `evaluation_runs` 作为正式评测运行事实与可靠队列，由独立 Evaluation Worker 使用隔离评测数据库执行；`operations` 仅作为长任务投影。Validate 与 Activate 保持短事务，不进入任务队列。

**Tech Stack:** Python 3.12、FastAPI、psycopg、PostgreSQL/pgvector、React 19、TypeScript、Vite、Radix UI、Vitest、Playwright。

**执行依据:** 本计划是 Claude Code 的唯一执行入口；`docs/superpowers/specs/2026-09-08-index-governance-evaluation-closure-design.md` 仅用于追溯已确认设计。如两者出现差异，以本计划的范围、约束、接口和验收标准为准。

## 业务分层与状态真值

| 层级 | 核心对象 | 职责 | 明确不负责 |
|---|---|---|---|
| 数据同步层 | Data Source / Document / Document Version | 获取、解析、更新、删除资料并维护可索引源版本 | 不创建发布版本，不决定上线 |
| 索引构建层 | Index Definition / Index Version / Index Build / Document Index State | 冻结配置与资料快照，构建 Vector / Keyword / Metadata 统一版本 | 不执行正式质量结论，不自动上线 |
| 质量评测层 | Evaluation Run / Retrieval Evaluation Report | 在隔离评测库运行正式检索评测并沉淀不可变证据 | 不修改候选版本，不直接激活 |
| 发布治理层 | Validation Report / Lifecycle Event / Active Pointer | 执行三层门禁、原子激活、回滚、退役、清理 | 不承担长任务构建或评测 |

统一生命周期：

```text
draft → building → validating → ready → active → previous → retired → cleaned
             ├─ build_failed
             └─ validation_failed → validating（重新验证）
                                  └→ building（创建新 Build 重建）
```

状态转换约束：

- Snapshot 创建 `Index Version` 后，`config_snapshot`、`config_fingerprint`、Document Snapshot 均不可修改。
- Build 成功只进入 `validating`；Build 失败进入 `build_failed`，重试产生新的 `Index Build attempt`。
- 正式评测是 `validating` 阶段的前置证据，不新增 Index Version 生命周期状态；其自身状态为 `queued/running/succeeded/failed/cancelled`。
- 三层验证包含完整性、技术、检索质量，生成不可变 Validation Report；通过进入 `ready`，失败进入 `validation_failed`。
- Activate 只允许 `ready`，单事务完成候选 `active`、原 active `previous`、知识库 active 指针和 Lifecycle Event。
- Rollback 不是直接改状态：目标历史版本必须满足可发布约束，并复用同一原子切换函数，使目标变为 `active`、当前版本变为 `previous`。
- Retire 只作用于非 active 历史版本；Cleanup 只作用于 `retired`，先清物理索引再记录 `cleaned`。

## P0 / P1 / P2 实施顺序

- **P0 — 业务闭环与数据安全：** Task 1–5。先完成 schema、正式评测队列、独立 Worker、报告/验证 API、源文件预检；P0 未完成不得接入前端“运行正式评测”。
- **P1 — 前端入口与交互闭环：** Task 6–9。完成步骤条、Tooltip、版本详情弹框、运行记录弹框，以及评测 → 验证 → 激活操作链。
- **P2 — 运行检查与现状复盘：** Task 10。只基于真实实现与运行证据输出业务链路、闭环项和缺失项，不把规划能力写成已实现。

## Global Constraints

- `Index Version` 创建后配置和 Document Snapshot 不可修改。
- `official` 表示可信正式来源；`passed` 表示绝对阈值结论，两者不得继续等价。
- 正式评测使用 `EVALUATION_DATABASE_URL`，不得在业务数据库写入临时评测语料。
- Evaluation Worker 与 Index Worker 分离；正式评测不得阻塞上传、同步和索引构建。
- Validate 只生成不可变 Validation Report；通过后进入 `ready`，不得自动激活。
- Activate 必须保持单事务 active / previous 原子切换。
- 源文件缺失不得自动删除数据库记录；必须提供明确恢复路径。
- 页面遵循 `docs/design/ui-baseline.md`；详情弹框桌面最大宽度 900px，移动端全屏。
- 保留用户当前未提交修改，不重置、不覆盖无关文件。
- 未收到“提交代码”前不得 commit 或 push；计划中的阶段检查均只保留本地改动。
- 当前未授权轻量或完整验证：实现时可编写测试，但不运行测试、Lint、类型检查或生产构建；只启动项目并检查真实页面。

## Claude Code 执行约束

- 开始前先读取仓库根目录 `AGENTS.md`、本计划、现有领域实现和 `docs/design/ui-baseline.md`，再修改代码。
- 先执行 `git status --short` 和 `git branch --show-current`；保留全部既有修改，不清理、不重置、不覆盖无关文件。
- 严格按 Task 1 → Task 10 顺序连续执行，并在本文件中将已完成步骤由 `- [ ]` 更新为 `- [x]`；遇到真实阻塞才暂停。
- 每个 Task 开始前重新读取其列出的现有文件，优先复用 Repository、Dialog、Tooltip、PipelineStepper、审计和权限组件，不整页重写。
- 不得用假报告、静态指标、假进度或前端本地状态冒充后端业务完成；所有按钮必须连接真实 API 或显示可解释的禁用原因。
- 不得改变数据同步 Pipeline 的职责；正式评测属于质量评测层，Validate/Activate 属于发布治理层。
- 数据库迁移只能前向新增；不得删除、重建或清空业务数据库。评测数据库必须与业务数据库 URL 不同。
- 当前只允许运行“迁移、启动服务、健康检查、真实页面检查”等计划内运行步骤；不得自行运行 pytest、Vitest、Playwright、ESLint、类型检查、生产构建或容器构建。
- 不得 commit、push、创建 PR、关闭 Issue、Tag、Release 或部署；完成后只保留本地工作区，等待用户审阅。
- 每个 Task 结束必须记录：修改文件、完成项、未完成项、阻塞、未执行验证；最终报告必须明确“自动化验证未获授权”。

## File Structure

### Backend

- Create `backend/migrations/0039_index_evaluation_runs.sql`：正式评测队列、报告和 Operation 约束。
- Create `backend/app/index_evaluation_runs.py`：评测任务创建、查询、领取、收口、重试和取消。
- Create `backend/app/index_evaluation_worker.py`：一次正式评测的业务编排。
- Create `scripts/evaluation_worker.py`：独立 Worker 进程入口与租约恢复循环。
- Modify `backend/app/config.py`：评测数据库与 Worker 配置。
- Modify `backend/app/evaluation_reports.py`：统一读取文件报告和数据库报告。
- Modify `backend/evaluation/run_corpus_baseline.py`：支持受控正式运行，拆开 `official` 与 `passed`。
- Modify `backend/app/index_validation.py`：正式来源、配置指纹和相对回退验证。
- Modify `backend/app/postgres_documents.py`：源文件预检和稳定错误码。
- Modify `backend/app/main.py`：正式评测 API 与 Validation Repository 接线。
- Modify `backend/app/schemas.py`：请求与响应模型。
- Modify `docker-compose.yml`、`deploy/kubernetes/workloads.yaml`：Evaluation Worker 运行配置。

### Frontend

- Create `frontend/src/components/ui/ProgressSteps.tsx`：任务与发布流程共享视觉 primitive。
- Modify `frontend/src/components/ui/PipelineStepper.tsx`：复用 ProgressSteps。
- Modify `frontend/src/components/ui/ReleaseFlow.tsx`：复用 ProgressSteps，保留独立状态推导。
- Modify `frontend/src/components/ui/DataTable.tsx`：截断内容 Tooltip 契约。
- Create `frontend/src/components/IndexVersionDetailDialog.tsx`：索引版本详情弹框。
- Remove `frontend/src/components/IndexVersionDetailPage.tsx`：取消独立详情页实现。
- Create `frontend/src/components/OperationDetailDialog.tsx`：构建和正式评测统一详情弹框。
- Modify `frontend/src/components/KnowledgeBaseDetailPage.tsx`：正式评测、验证、详情与运行记录闭环。
- Modify `frontend/src/App.tsx`：移除独立版本详情页面路由渲染，保留深链到弹框的解析。
- Modify `frontend/src/api.ts`、`frontend/src/types.ts`：正式评测 API 与类型。

### Tests and Documentation

- Create `backend/tests/test_index_evaluation_runs.py`。
- Modify `backend/tests/test_postgres_foundation.py`、`backend/tests/test_env_example.py`。
- Modify `backend/tests/test_index_versions.py`、`backend/tests/test_document_snapshots.py`、`backend/tests/test_api.py`。
- Create `frontend/src/components/ProgressSteps.test.tsx`。
- Create `frontend/src/components/IndexVersionDetailDialog.test.tsx`。
- Create `frontend/src/components/OperationDetailDialog.test.tsx`。
- Modify `frontend/src/components/releaseStages.test.ts`、`frontend/src/components/ui/DataTable.test.tsx`、`frontend/src/App.test.tsx`。
- Modify `frontend/e2e/index-governance.spec.ts`。
- Create `docs/design/2026-09-08-index-governance-business-closure-inventory.md`：实施后业务闭环复盘。

---

### Task 1: Schema V39 and Runtime Configuration

**Files:**
- Create: `backend/migrations/0039_index_evaluation_runs.sql`
- Modify: `backend/app/config.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `deploy/kubernetes/workloads.yaml`
- Modify: `backend/tests/test_postgres_foundation.py`
- Modify: `backend/tests/test_env_example.py`

**Interfaces:**
- Produces: schema version `39`；`Settings.evaluation_database_url: str | None`；`Settings.evaluation_worker_id: str`；`Settings.evaluation_job_stale_seconds: int`。
- Consumes: existing `evaluation_runs`、`operations`、`index_versions`。

- [ ] **Step 1: Add migration contract assertions**

在 `test_postgres_foundation.py` 的连续迁移清单和数据库迁移断言中加入 V39，并在 `test_env_example.py` 保持配置版本一致：

```python
assert apply_migrations(database_url) == 39
assert "index_evaluation" in operation_types
assert column("evaluation_runs", "status").is_nullable is False
assert column("evaluation_runs", "passed").is_nullable is True
```

- [ ] **Step 2: Create V39 migration**

迁移必须完成：

```sql
ALTER TABLE evaluation_runs ADD COLUMN status text NOT NULL DEFAULT 'succeeded';
ALTER TABLE evaluation_runs ADD COLUMN operation_id text REFERENCES operations(operation_id);
ALTER TABLE evaluation_runs ADD COLUMN config_fingerprint text;
ALTER TABLE evaluation_runs ADD COLUMN baseline_report_id text;
ALTER TABLE evaluation_runs ADD COLUMN report_payload jsonb;
ALTER TABLE evaluation_runs ADD COLUMN attempt_count integer NOT NULL DEFAULT 0;
ALTER TABLE evaluation_runs ADD COLUMN max_attempts integer NOT NULL DEFAULT 3;
ALTER TABLE evaluation_runs ADD COLUMN available_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE evaluation_runs ADD COLUMN locked_at timestamptz;
ALTER TABLE evaluation_runs ADD COLUMN locked_by text;
ALTER TABLE evaluation_runs ADD COLUMN error_code text;
ALTER TABLE evaluation_runs ADD COLUMN error_message text;
ALTER TABLE evaluation_runs ADD COLUMN started_at timestamptz;
ALTER TABLE evaluation_runs ADD COLUMN finished_at timestamptz;
ALTER TABLE evaluation_runs ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE evaluation_runs ALTER COLUMN passed DROP NOT NULL;
```

同时增加：

- `status IN ('queued','running','succeeded','failed','cancelled')`；
- `config_fingerprint` 为 64 位小写十六进制或空；
- 同一 `index_version_id` 仅一个 `queued/running` Retrieval Run 的部分唯一索引；
- `operations.operation_type` 增加 `index_evaluation`；
- 旧记录回填 `status='succeeded'`、`finished_at=run_at`、`updated_at=created_at`。

- [ ] **Step 3: Add settings**

```python
evaluation_database_url: str | None = None
evaluation_worker_id: str = "evaluation-worker-local"
evaluation_job_stale_seconds: int = Field(default=1800, ge=60, le=86400)
required_database_schema_version: int = Field(default=39, ge=1)
```

- [ ] **Step 4: Wire runtime configuration**

`.env.example`、Compose 和 Kubernetes 新增 `EVALUATION_DATABASE_URL`、`EVALUATION_WORKER_ID`，但不得把凭据写入仓库。Evaluation Worker 与 Index Worker 使用同一应用镜像、不同 command。Backend 缺少评测配置时仍可启动，但创建正式评测任务返回稳定 `503 EVALUATION_DATABASE_NOT_CONFIGURED`。

- [ ] **Step 5: Guard and initialize the isolated Evaluation DB**

初始化前比较规范化后的 `DATABASE_URL` 与 `EVALUATION_DATABASE_URL`，相同则拒绝启动 Evaluation Worker。评测库不存在时只允许创建专用库 `rag_enterprise_evaluation`；存在时不得删除或清空。对该库应用同一套 V39 迁移，并保留 `_require_empty_evaluation_database()` 的业务数据防护。

- [ ] **Step 6: Apply business migration only**

运行迁移到当前本地 PostgreSQL，确认数据库报告 schema `39`。这一步不是测试，不运行测试套件。

- [ ] **Step 7: Local review checkpoint**

检查 `git diff --check` 和迁移 SQL；不 commit、不 push。

### Task 2: Evaluation Run Repository and Queue Semantics

**Files:**
- Create: `backend/app/index_evaluation_runs.py`
- Create: `backend/tests/test_index_evaluation_runs.py`

**Interfaces:**
- Produces:
  - `create_evaluation_run(database_url: str, index_version_id: str, dataset_id: str, requested_by: str, max_attempts: int = 3) -> dict[str, Any]`
  - `list_evaluation_runs(database_url: str, knowledge_base_id: str, index_version_id: str) -> list[dict[str, Any]]`
  - `get_evaluation_run(database_url: str, knowledge_base_id: str, evaluation_run_id: str) -> dict[str, Any] | None`
  - `claim_evaluation_run(database_url: str, worker_id: str) -> dict[str, Any] | None`
  - `finish_evaluation_run(database_url: str, evaluation_run_id: str, report: RetrievalEvaluationReport) -> dict[str, Any]`
  - `fail_evaluation_run(database_url: str, evaluation_run_id: str, code: str, message: str) -> None`
  - `retry_evaluation_run(...) -> dict[str, Any]`
  - `cancel_evaluation_run(...) -> dict[str, Any]`
- Consumes: schema V39；`create_operation()`；Index Version frozen config。

- [ ] **Step 1: Author repository behavior tests**

覆盖：候选版本状态限制、同版本并发唯一、后端重算配置指纹、自动选择 active 正式报告为 baseline、`FOR UPDATE SKIP LOCKED`、失败重试、取消、租约恢复、知识库归属隔离。

```python
run = create_evaluation_run(db, candidate_id, "corpus_v2", "usr_admin")
assert run["status"] == "queued"
assert run["config_fingerprint"] == candidate["config_fingerprint"]
assert operation(run)["operation_type"] == "index_evaluation"
```

- [ ] **Step 2: Implement immutable enqueue evidence**

创建任务时事务内锁定版本，只接受 `validating` 或 `validation_failed`，保存：版本 ID、配置指纹、配置快照、数据集版本、当前 active 基线报告、请求人。

- [ ] **Step 3: Implement claim and lease recovery**

领取查询必须使用：

```sql
SELECT * FROM evaluation_runs
WHERE evaluation_type='retrieval' AND status='queued' AND available_at <= now()
ORDER BY created_at
FOR UPDATE SKIP LOCKED LIMIT 1;
```

领取和 `operations.status='running'` 在同一事务更新。

- [ ] **Step 4: Implement terminal state convergence**

成功、失败、取消必须同时收口 `evaluation_runs` 与对应 `operations`；错误消息保存技术详情，API 响应层只输出稳定用户文案。

- [ ] **Step 5: Add retry and cancellation guards**

- `retry` 仅允许 `failed` 且未超过最大次数；
- `cancel` 仅允许 `queued`；
- 运行中的取消本轮不实现，返回稳定 `409`。

- [ ] **Step 6: Local review checkpoint**

检查接口签名与 V39 字段一致；不运行测试，不 commit。

### Task 3: Formal Evaluation Execution and Dedicated Worker

**Files:**
- Create: `backend/app/index_evaluation_worker.py`
- Create: `scripts/evaluation_worker.py`
- Modify: `backend/evaluation/run_corpus_baseline.py`
- Modify: `docker-compose.yml`
- Modify: `deploy/kubernetes/workloads.yaml`
- Test: `backend/tests/test_index_evaluation_runs.py`

**Interfaces:**
- Produces:
  - `run_evaluation_once(settings: Settings, run: dict[str, Any], embedder: Any, reranker: Any) -> RetrievalEvaluationReport`
  - CLI loop `python -m scripts.evaluation_worker`
- Consumes: Task 2 claim/finish/fail interfaces；`run_corpus_baseline()`。

- [ ] **Step 1: Add worker orchestration tests with model fakes**

断言 Worker 使用冻结的 `chunk_size`、`chunk_overlap`、模型与数据集；业务 DB 只保存报告，临时语料只进入 Evaluation DB。

- [ ] **Step 2: Decouple report provenance from threshold result**

`run_corpus_baseline()` 返回：

```python
report = RetrievalEvaluationReport(..., official=True, ...)
assert report.official is True
assert report.passed == all(metric.passed for metric in required_metrics)
```

只有测试替身或不受控运行才允许 `official=False`；不得再执行 `official = passed`。

- [ ] **Step 3: Implement evaluation orchestration**

任务开始前检查：

- `evaluation_database_url` 已配置；
- 候选版本仍属于同一知识库；
- 当前指纹仍等于入队指纹；
- Dataset ID 位于服务端白名单；
- 独立评测数据库 schema 为 39 且没有业务用户/知识库。

- [ ] **Step 4: Persist complete report payload**

成功后通过 `finish_evaluation_run()` 写入完整 `model_dump(mode='json')`、metrics、`official`、`passed`、`report_id` 和候选版本冻结的 `component_manifest`。Vector、Keyword、Metadata、ACL、Citation 版本必须来自后端 Version Snapshot，不由 Worker 或前端重新推导。

同时按阶段更新对应 Operation：`queued → prepare_dataset → build_corpus → retrieve → rerank → calculate_metrics → persist_report → complete`。失败时保留最后阶段并写入稳定错误码，前端不得直接展示宿主绝对路径。

- [ ] **Step 5: Implement Worker process loop**

参考 Index Worker 的 SIGTERM、轮询和租约恢复，但设置 `max_concurrency=1`，禁止并行污染同一个 Evaluation DB。

- [ ] **Step 6: Add deployment process**

Compose 服务名 `evaluation-worker`，Kubernetes Deployment 名 `rag-evaluation-worker`。若未配置 `EVALUATION_DATABASE_URL`，进程明确退出并输出稳定配置错误，Backend 仍可启动。

- [ ] **Step 7: Local review checkpoint**

确认 Evaluation Worker 不导入或调用 Index Worker 的任务领取循环；不运行测试，不 commit。

### Task 4: Unified Report Repository, Validation Semantics, and API

**Files:**
- Modify: `backend/app/evaluation_reports.py`
- Modify: `backend/app/index_validation.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/tests/test_api.py`
- Modify: `backend/tests/test_index_versions.py`

**Interfaces:**
- Produces API：
  - `POST /api/knowledge-bases/{kb}/index-versions/{version}/evaluation-runs`
  - `GET /api/knowledge-bases/{kb}/index-versions/{version}/evaluation-runs`
  - `GET /api/knowledge-bases/{kb}/evaluation-runs/{run}`
  - `POST /api/knowledge-bases/{kb}/evaluation-runs/{run}/retry`
  - `POST /api/knowledge-bases/{kb}/evaluation-runs/{run}/cancel`
- Consumes: Tasks 2–3 repository and report payload。

- [ ] **Step 1: Add API contract tests**

覆盖管理员权限、知识库归属、202 enqueue、409 duplicate、404 cross-KB、retry/cancel、报告详情以及缺少 Evaluation DB 的 503。

- [ ] **Step 2: Add response models**

新增 `IndexEvaluationRunCreateRequest`、`IndexEvaluationRunResponse`、`IndexEvaluationRunDetailResponse`。响应不得包含数据库 URL、密钥或宿主绝对路径。

- [ ] **Step 3: Extend EvaluationReportRepository**

Repository 构造函数接受可选业务数据库 URL：

```python
EvaluationReportRepository(reports_path: Path, database_url: str | None = None)
```

`list_official()` 和 `get_official()` 合并文件正式报告与数据库正式报告，按 `report_id` 去重，数据库记录优先。

- [ ] **Step 4: Correct validation report selection**

`create_scoped_index_validation()` 使用统一 Repository 加载 `official=true` 的报告，不要求 `passed=true`。`check_retrieval_quality()` 继续检查配置指纹、证据完整性、ACL、Metadata 与相对回退；绝对阈值写入说明，不直接替代三层门禁结论。

- [ ] **Step 5: Add routes and audit events**

审计动作使用：`index_evaluation.create`、`index_evaluation.retry`、`index_evaluation.cancel`。验证与激活保留原审计动作。

- [ ] **Step 6: Protect lifecycle, rollback, and legacy governance**

在既有生命周期测试中补充回归约束：

- Activate 仅允许 `ready`，并验证 active / previous / active pointer / lifecycle event 同事务收口；
- Rollback 必须复用 `switch_to_version()`，不得新增一套直接 UPDATE 状态的逻辑；
- `legacy/unknown` 版本不得伪造配置指纹、组件版本或正式报告；证据不完整时禁止验证与激活，但保留查看、退役、清理能力；
- Validation Report 保存并核对 Version Snapshot、Build、Evaluation Report 的同一 `config_fingerprint` 与 `component_manifest`。

- [ ] **Step 7: Local review checkpoint**

检查 OpenAPI 响应与前端类型所需字段一致；不运行测试，不 commit。

### Task 5: Source File Preflight and Stable Failure Handling

**Files:**
- Modify: `backend/app/postgres_documents.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/tests/test_document_snapshots.py`
- Modify: `backend/tests/test_index_versions.py`

**Interfaces:**
- Produces:
  - `validate_snapshot_sources(upload_root: Path, members: list[dict[str, Any]]) -> list[MissingSource]`
  - stable code `SOURCE_FILE_MISSING`
- Consumes: `document_versions.source_path`、Document Snapshot members。

- [ ] **Step 1: Add missing-source regression tests**

测试数据库保留 ready Document Version 但删除物理文件，断言 Preview/Create 不创建 Version、Build、Operation 或 Job，并返回：

```json
{
  "code": "SOURCE_FILE_MISSING",
  "message": "源文件已丢失，无法构建索引。",
  "details": {"documents": [{"document_id": "doc_x", "filename": "x.docx"}]}
}
```

- [ ] **Step 2: Implement controlled path validation**

通过 `Path.resolve()` 确认文件仍在 `upload_root` 内，检查存在性、普通文件、记录大小；不得允许 `../` 越界。

- [ ] **Step 3: Wire preview and create**

给 `preview_index_version_candidate()` 和 `create_index_version_candidate()` 增加 `upload_root` 参数。Preview 返回阻塞原因；Create 在事务写入前再次核对，防止 Preview 后文件消失。

- [ ] **Step 4: Add Worker defense**

`read_bytes()` 的 `FileNotFoundError` 转换为稳定 `SOURCE_FILE_MISSING`，Document Index State 与 Operation 使用用户文案；相对路径只保留在管理员技术详情中。

- [ ] **Step 5: Local review checkpoint**

用当前缺失的测试资料记录执行只读 Preview，确认得到稳定阻塞结果；不得自动清理该记录，不运行测试，不 commit。

### Task 6: Shared Progress Visuals and Table Overflow Contract

**Files:**
- Create: `frontend/src/components/ui/ProgressSteps.tsx`
- Create: `frontend/src/components/ProgressSteps.test.tsx`
- Modify: `frontend/src/components/ui/PipelineStepper.tsx`
- Modify: `frontend/src/components/ui/ReleaseFlow.tsx`
- Modify: `frontend/src/components/releaseStages.ts`
- Modify: `frontend/src/components/releaseStages.test.ts`
- Modify: `frontend/src/components/ui/DataTable.tsx`
- Modify: `frontend/src/components/ui/DataTable.test.tsx`

**Interfaces:**
- Produces:
  - `ProgressStep { key: string; label: string; state: 'done'|'current'|'todo'|'blocked'|'failed'|'retrying'; note?: string }`
  - `ProgressSteps({steps,label,compact,showPercent})`
  - `Column<T>.tooltip?: (row: T) => ReactNode`
- Consumes: existing Pipeline stage maps and `releaseStages()`。

- [ ] **Step 1: Author visual-state component tests**

断言 completed、current、blocked、failed、retrying 有文字和可访问名，不只依赖颜色；Tooltip 可被键盘聚焦触发。

- [ ] **Step 2: Extract ProgressSteps visual primitive**

统一使用文件进度条现有的 16px 圆点、连线、成功/警告/失败色与紧凑标签。组件只渲染传入状态，不推导业务状态。

- [ ] **Step 3: Refactor PipelineStepper**

保留 `PIPELINES`、真实 `current_stage` 别名与进度推导，仅把最终步骤数组交给 `ProgressSteps`。

- [ ] **Step 4: Refactor ReleaseFlow**

生命周期步骤改为：索引定义、版本快照、索引构建、正式评测、三层验证、待激活、当前生效。状态继续由版本、评测、验证和 active 指针推导。

- [ ] **Step 5: Extend DataTable tooltip behavior**

简单 string/number 自动设置完整 `title`；复杂内容使用 `column.tooltip(row)` 包装统一 Tooltip。错误、文件名、ID、指纹列接入时不得扩大行高。

- [ ] **Step 6: Local review checkpoint**

启动 Vite 后检查步骤条与表格无布局溢出；不运行 Vitest、Lint 或 build，不 commit。

### Task 7: Index Version Detail Dialog

**Files:**
- Create: `frontend/src/components/IndexVersionDetailDialog.tsx`
- Create: `frontend/src/components/IndexVersionDetailDialog.test.tsx`
- Remove: `frontend/src/components/IndexVersionDetailPage.tsx`
- Modify: `frontend/src/components/KnowledgeBaseDetailPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Produces:
  - `IndexVersionDetailDialog({open, knowledgeBaseId, versionId, onClose, onActionComplete})`
- Consumes: version、build、evaluation run、validation report、lifecycle event APIs。

- [ ] **Step 1: Add dialog behavior tests**

覆盖打开、加载、错误、关闭、深链、移动端全屏语义，以及五个内容区块。

- [ ] **Step 2: Build the dialog shell**

使用 Radix `Dialog size='lg'`，增加受控最大高度和内部滚动。标题显示版本号与状态，副标题显示缩写 ID；完整 ID 提供 Tooltip 与复制。

- [ ] **Step 3: Move existing detail content**

迁移版本概览、配置快照、组件清单、构建结果、正式评测、三层验证和生命周期；不得丢失 legacy/unknown 提示与治理读取错误态。

- [ ] **Step 4: Replace independent navigation**

版本表“详情”设置 `selectedVersionId`。`/knowledge-bases/{kb}/index-versions/{version}` 深链进入知识库详情后自动打开弹框；关闭恢复 `/knowledge-bases/{kb}`。

- [ ] **Step 5: Remove obsolete page implementation**

确认所有 import 和测试已迁移后删除 `IndexVersionDetailPage.tsx`，不得留下两个详情实现。

- [ ] **Step 6: Local review checkpoint**

检查桌面弹框和移动端全屏布局；不运行自动化验证，不 commit。

### Task 8: Unified Operation Detail Dialog

**Files:**
- Create: `frontend/src/components/OperationDetailDialog.tsx`
- Create: `frontend/src/components/OperationDetailDialog.test.tsx`
- Modify: `frontend/src/components/KnowledgeBaseDetailPage.tsx`
- Modify: `frontend/src/types.ts`

**Interfaces:**
- Produces:
  - `OperationDetailDialog({operation, build, buildDocuments, evaluationRun, onClose, onRetryEvaluation, onOpenVersion})`
- Consumes: Task 4 evaluation details、Index Build documents、Task 6 ProgressSteps。

- [ ] **Step 1: Add type-aware detail tests**

覆盖 `index_build` 与 `index_evaluation` 两种类型，失败原因、技术详情、文档级结果、指标和动作必须来自真实响应数据。

- [ ] **Step 2: Implement common summary**

展示类型、状态、版本、开始/结束时间、进度、总数/成功/失败/处理中。未知字段使用 `—`。

- [ ] **Step 3: Implement build-specific section**

Document Index State 表显示文件名、Vector、Keyword、Metadata、整体状态和稳定失败原因，不再在主表格下方展开。

对 `SOURCE_FILE_MISSING` 提供真实恢复入口：

- “前往资料”切换到知识库资料页并定位对应 Document；
- “删除失效资料”复用现有 scoped Delete Document API，二次确认后刷新候选预检；
- “重新上传”打开现有上传入口，不伪造自动恢复；
- 删除或补传后必须重新创建新的 Index Version，不修改旧版本快照。

- [ ] **Step 4: Implement evaluation-specific section**

展示数据集、候选配置、基线报告、绝对阈值结论、关键指标、报告 ID、失败诊断和重试动作。

- [ ] **Step 5: Replace mixed detail interactions**

所有运行记录行“详情”只设置 `selectedOperation`，统一弹框；删除 `selectedBuild` 的行内详情区。

- [ ] **Step 6: Local review checkpoint**

检查长错误只在弹框中换行，主表格行高稳定；不运行自动化验证，不 commit。

### Task 9: Candidate Evaluation, Validation, and Activation UI Closure

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/components/KnowledgeBaseDetailPage.tsx`
- Modify: `frontend/src/components/IndexVersionDetailDialog.tsx`
- Modify: `frontend/src/components/releaseStages.ts`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/e2e/index-governance.spec.ts`

**Interfaces:**
- Produces frontend API：
  - `createIndexEvaluationRun(kb, version, datasetId)`
  - `listIndexEvaluationRuns(kb, version)`
  - `getIndexEvaluationRun(kb, run)`
  - `retryIndexEvaluationRun(kb, run)`
  - `cancelIndexEvaluationRun(kb, run)`
- Consumes: Tasks 4、6、7、8。

- [ ] **Step 1: Add frontend types and client methods**

定义 `IndexEvaluationRun`、`IndexEvaluationRunDetail`、`EvaluationRunStatus`，字段逐项对齐后端响应，不在前端重算配置指纹。

- [ ] **Step 2: Add candidate row actions**

动作规则：

```text
validating + no matching report + no active run → 运行正式评测
queued/running evaluation → 查看评测进度
failed evaluation → 重新运行评测
official matching report → 执行三层验证
ready → 激活
```

- [ ] **Step 3: Replace empty validation selector dead end**

无匹配报告时，验证弹框主操作改为“运行正式评测”；存在报告时允许选择，并同时显示绝对阈值结论和“最终是否可发布由三层验证决定”的说明。

- [ ] **Step 4: Include evaluation operations in governance records**

索引治理运行记录仅包含 `index_build`、`index_evaluation`。空态文案改为“创建索引版本或运行正式评测后，这里会保留记录”。

为 `index_evaluation` 增加任务类型中文映射和阶段映射；阶段必须与 Worker 写入的 `prepare_dataset / build_corpus / retrieve / rerank / calculate_metrics / persist_report / complete` 一致。

- [ ] **Step 5: Poll active long-running records**

页面仅在存在 `queued/running` 构建或评测时定时刷新；终态后停止轮询，避免常驻请求。

- [ ] **Step 6: Preserve manual activation boundary**

正式评测和三层验证成功后不得自动调用 Activate。只有 `ready` 版本显示“激活”，弹框明确当前 active 将变为 previous。

- [ ] **Step 7: Local review checkpoint**

用真实页面走到“运行正式评测”确认弹框、任务记录和禁用原因正确；不实际启动重量评测模型，除非本地 Evaluation DB 已安全配置；不运行自动化验证，不 commit。

### Task 10: Runtime Inspection and Business Closure Inventory

**Files:**
- Create: `docs/design/2026-09-08-index-governance-business-closure-inventory.md`
- Modify: `README.md`
- Modify: `docs/operations/postgres-migration-recovery.md`

**Interfaces:**
- Produces: 当前实现的业务链路、已闭环能力、缺失项和后续优先级。
- Consumes: Tasks 1–9 的实际代码与运行证据。

- [ ] **Step 1: Start current workspace runtime**

使用 schema V39 启动 PostgreSQL、Backend、Index Worker、Evaluation Worker 和 Frontend。检查：

```text
GET /api/health/ready → 200
GET / → 200
```

- [ ] **Step 2: Inspect desktop UI**

检查知识库详情索引治理：发布步骤条、版本详情弹框、正式评测入口、验证入口、运行记录弹框、长内容 Tooltip、缺失文件提示。

- [ ] **Step 3: Inspect mobile UI**

检查 `<768px`：顶部导航、全屏详情弹框、步骤条横向滚动、表格卡片或安全横向滚动、44px 点击区域。

- [ ] **Step 4: Inspect empty/loading/success/failure states**

不得用假数据冒充评测成功。未配置 Evaluation DB 时展示稳定配置失败；缺失源文件展示 `SOURCE_FILE_MISSING`；历史版本展示 unknown/legacy。

- [ ] **Step 5: Write the post-implementation inventory**

文档必须包含：

- 当前业务链路；
- 四层服务边界；
- 页面入口和用户操作路径；
- 已闭环项；
- 仍缺失或部分闭环项；
- 数据一致性和运维风险；
- P0/P1/P2 后续建议；
- 事实证据对应的代码、迁移和运行结果。

- [ ] **Step 6: Update operations documentation**

README 与迁移恢复文档增加 Evaluation DB 初始化、Worker 启动、任务失败重试和报告恢复说明，不写真实凭据。

- [ ] **Step 7: Final local review checkpoint**

汇总修改、运行状态、未执行的自动化验证、仍存在的风险；不 commit、不 push，等待用户在 VS Code 审阅并明确“提交代码”。

## 最终验收标准

- 知识库详情“索引治理”步骤条使用文件处理进度样式，状态可访问且不只依赖颜色。
- 索引版本“详情”统一为弹框；深链仍可打开指定版本，关闭后回到知识库详情。
- 运行记录不再混用行内展开与弹框；Index Build 与 Index Evaluation 均使用统一详情框架和类型化内容。
- 表格中的文件名、ID、配置指纹和错误信息截断后可通过 Hover/键盘焦点查看完整内容，且行高不抖动。
- 源文件缺失在创建版本前被拦截；并发丢失在 Worker 内以 `SOURCE_FILE_MISSING` 稳定失败，不泄露宿主绝对路径。
- 候选版本没有匹配指纹报告时可直接创建正式评测任务；运行记录可查看 queued/running/succeeded/failed，并支持允许范围内的重试/取消。
- 正式报告的 `official` 与 `passed` 分离：受控正式运行无论阈值是否通过都可作为三层验证证据，阈值结论必须如实展示。
- 三层验证使用相同配置指纹的正式报告，成功后只进入 `ready`；不会自动激活。
- Activate 仍以单事务完成新版本 `active`、旧版本 `previous` 和知识库 active 指针切换；Rollback 仍复用同一原子切换能力。
- Vector、Keyword、Metadata、ACL、Citation 的组件版本与配置指纹贯穿 Version Snapshot、Build、Evaluation、Validation 和 Activate，不允许前端重算。
- legacy/unknown 版本保持可读、可退役、可清理，但没有可信指纹或正式报告时不得激活。
- 数据同步、索引构建、质量评测、发布治理四层边界在代码、页面动作和复盘文档中一致。
- 最终复盘文档明确列出当前业务链路、已闭环项、仍缺失项、P0/P1/P2 建议和对应代码/迁移/运行证据。
- 本轮没有用户授权的自动化验证时，交付报告不得声称测试、Lint、类型检查、生产构建或 E2E 已通过。
