# 索引治理与正式评测：业务闭环盘点

日期：2026-09-13（实施计划 `2026-09-08-index-governance-evaluation-closure.md` Task 10）
分支：`main`（未提交）
数据库现场：业务库 `rag_demo` Schema V39，评测库 `rag_enterprise_evaluation` Schema V39
工作性质：在真实运行时里走一遍业务链路，记录实测结果与仍存在的缺口

## 1. 结论先行

正式评测这一段在本轮真正接上了：候选版本可以在产品内发起正式检索评测，Worker 在隔离的评测库
里跑完并回写报告，三层验证拿这份报告放行，激活仍是手动的一次原子切换。本次在本机运行时里
**完整跑通了一条链路**，证据见第 5 节。

走查过程中暴露并修掉了 9 个真实缺陷（第 6 节），一并清掉了 25 个长期红着、与本轮改动无关的
后端测试（第 7 节）与 42 个 lint 历史项（第 7.1 节）。

## 2. 当前业务链路

```text
上传资料 ──► 解析/分类（classify job）
                 │
                 ▼
        创建索引版本（preview → create，源文件预检在此拦截）
                 │
                 ▼
        Index Worker 构建 ──► finalize ──► validating
                 │
                 ▼
        创建正式评测任务（202，入队即冻结配置指纹/数据集/基线报告）
                 │
                 ▼
        Evaluation Worker（独立进程，max_concurrency=1，隔离评测库）
          备数据集 → 建语料 → 召回 → 精排 → 算指标 → 出报告 → 完成
                 │
                 ▼
        正式报告（official=true，passed 独立表达阈值结论）
                 │
                 ▼
        三层验证（完整性 / 技术 / 检索质量）──► ready
                 │
                 ▼
        手动激活 ──► active，旧版本 previous，知识库指针原子切换
```

## 3. 四层服务边界

| 层 | 职责 | 代码位置 | 进程 |
| --- | --- | --- | --- |
| 数据同步 | 数据源发现差异、拉取、规范化 | `data_source_sync.py` | Backend / sync worker |
| 索引构建 | 解析、切片、向量与关键词写入 | `postgres_documents.py`、`scripts/index_worker.py` | Index Worker |
| 质量评测 | 在隔离评测库里跑召回与精排，产出正式报告 | `index_evaluation_runs.py`、`index_evaluation_worker.py`、`scripts/evaluation_worker.py` | Evaluation Worker |
| 发布治理 | 三层验证、激活、回滚、退役、清理 | `index_versions.py`、`index_validation.py` | Backend |

边界在本次实测中成立：Evaluation Worker 不导入 Index Worker 的领取循环，评测的临时语料只进
评测库，业务库里只留报告。

## 4. 页面入口与操作路径

知识库详情 →「索引治理」Tab：

- **发布流程步骤条**：索引定义 → 版本快照 → 索引构建 → 正式评测 → 三层验证 → 待激活 → 当前生效。
  每格的状态与原因都进可访问名，并由 `delay=0` 的 Tooltip 在悬停与键盘聚焦时给出。
- **索引版本表**：详情（弹框）、运行正式评测、三层验证、激活、重新构建、回滚、退役、清理。
  深链 `/knowledge-bases/{kb}/index-versions/{iv}` 仍可直接打开对应版本的弹框，关闭后地址
  回到 `/knowledge-bases/{kb}`（实测通过）。
- **正式质量评测表**：报告 ID、评测配置（与当前版本一致 / 与线上版本一致 / 配置已变化）、
  结果（已通过 / 未通过）、发布用途、时间。
- **运行记录**：索引构建与正式评测统一用同一个详情弹框，按 `operation_type` 渲染不同内容。

## 5. 本次实测证据

全部在本机运行时执行，命令与输出见下。

| 验证项 | 结果 |
| --- | --- |
| 五件套启动 | Backend `/api/health/ready` → 200；前端 `/` → 200；Index Worker、Evaluation Worker 进程存活 |
| Schema | 业务库与评测库均报告 V39 |
| 源文件缺失拦截 | `preview` 返回 `creation_allowed=false`，原因为「以下资料的源文件已丢失，需重新上传或删除后再创建：…」，**不含任何路径** |
| 未配置评测库 | 另起一个不带 `EVALUATION_DATABASE_URL` 的实例：Backend 照常 `ready=200`，创建评测任务返回 `503 EVALUATION_DATABASE_NOT_CONFIGURED` |
| 完整闭环 | 新建知识库 → 上传 2 份资料 → 创建候选版本 → 构建完成进 `validating` → 创建评测任务 `202` → Worker 跑完 `succeeded` |
| official / passed 解耦 | 该次评测 `official=true, passed=false`（Recall@5 68.6% / 阈值 70.0%），三层验证仍 `pass`，版本进 `ready` |
| 不自动激活 | `ready` 后线上指针未变，手动 `PUT .../active` 才切换 |
| 原子切换 | 激活返回 `{active: iv_new, previous: iv_old}`，知识库 `active_index_version_id` 同步更新 |
| 状态回归 | `index-governance` e2e 12 个用例在真实浏览器下全绿 |
| 移动端 | 412px 下页面无横向溢出，表格在容器内横向滚动，弹框全屏，「详情」点击区 38×44 |
| 错误态 | 索引版本接口 500 时页面给出 `role="alert"` 的可见错误 |
| 加载态 | 骨架占位 + `role="status"`「正在读取知识库」 |
| 空态 | 「还没有索引版本 / 创建首个索引版本后，将按快照构建、验证并等待激活」 |

复现命令：

```bash
# 运行时
uv run uvicorn backend.app.main:app --port 8000
uv run python -m scripts.index_worker
uv run python -m scripts.evaluation_worker
cd frontend && npm run dev

# 状态回归（需要管理员凭据，否则整个 describe 被跳过）
cd frontend && SMOKE_ADMIN_USERNAME=... SMOKE_ADMIN_PASSWORD=... \
  npx playwright test index-governance --project=desktop-chromium
```

## 6. 本轮修掉的缺陷

| # | 缺陷 | 根因 | 修法 |
| --- | --- | --- | --- |
| 1 | 版本一激活，它那次评测的详情就再也打不开（弹框只剩「读取不到这次评测的明细」） | 评测记录只按**候选**版本拉取，而 `active` 不在候选状态集合里；找不到时还是静默 `return` | 新增 `GET /api/knowledge-bases/{kb}/evaluation-runs` 列整库；对不上时给出可见原因 |
| 2 | 运行记录里正式评测的「目标版本」恒为「—」 | 同上，`evaluationRun` 为空 | 同上 |
| 3 | 刚刚放行线上版本的那份报告被写成「配置已变化 · 不可用于发布」 | 「发布用途」先按 `passed` 过滤，且没有候选版本时不回退比对线上版本 | 判据只用 `official` + 配置指纹；无候选时回退比对 active |
| 4 | 跨知识库退役/保留策略/同步资源查询的归属校验从未生效 | `(%s IS NULL OR col=%s)` 缺类型标注，PostgreSQL 直接抛 `IndeterminateDatatype` | 三处补 `%s::text` |
| 5 | 重建完成后重复发起同配置重建会堆积候选版本 | 幂等只认 `building`，而构建完成后版本已进 `validating` | 新增「已构建完成且覆盖全量」的复用判定 |
| 6 | 刚上传的文档删不掉（`INDEX_JOB_ACTIVE`） | 删除校验把 `classify` 任务也算作「正在处理」 | 只拦 `index` / `rebuild` |
| 7 | 数据源「最后索引时间」显示为空 | 取该数据源最近一条 job 的 `finished_at`，而最近一条往往是排队中的 `classify` | LATERAL 子查询限定 `job_type IN ('index','rebuild')` |
| 8 | 加载中整页空白 | `base` 为 null 时直接不渲染 | 骨架占位 + `role="status"` |
| 9 | 移动端表格被压成三四个字，状态徽章被截断 | `w-full table-fixed` 让表格永远等于容器宽，外层 `overflow-x-auto` 形同虚设 | 每列最小 120px，窄屏改为横向滚动 |

## 7. 测试腐烂清单（本轮一并修复）

修复前 `pytest` 为 587 passed / 25 failed，全部与本轮功能改动无关：

| 类别 | 数量 | 表现 |
| --- | --- | --- |
| `FakeService.query` 签名落后于 `RAGService` 协议 | 6 | 流式路由把 `event_callback` 当第 7 个位置参数传入 → `TypeError` |
| `preview_index_version_candidate` 缺 `upload_root` | 3 | Task 5 加了参数，调用方测试没跟 |
| `PROMPT_VERSION` 硬编码旧值 | 3 | 实现已是 `v5-stream-grounded-governance-2` |
| 状态机变化未同步 | 3 | `finalize` 现在停在 `validating`，测试仍期待 `building` |
| 迁移新增列/约束未同步 | 4 | `name_normalized` NOT NULL、`index_versions_one_previous_idx` 唯一索引 |
| 实现缺陷（见第 6 节 #4 #5 #6 #7） | 6 | SQL 参数类型、幂等、删除拦截、最后索引时间 |

修复后：**612 passed，0 failed**。

### 7.1 Lint 历史项

`ruff` 32 个（`I001` import 顺序 10 个自动修复，`E501` 超长行 21 个手工折行，1 个未使用变量）
与 `eslint` 10 个 error 一并清零。其中三处值得记下来：

- **4 个裸 `<button>` 全部能装进基座 `Button`**，理由和前两次一样不成立：文字按钮用
  `variant="link"`，窄屏图标入口用 `variant="ghost" size="icon"`，`aria-expanded` / `title` /
  `aria-label` 都能通过 `...rest` 透传。
- **`prompts.py` 的 `E501` 用 per-file-ignore 而不是折行**：提示词正文是模型的输入，折行会改变
  发给模型的文本与 `prompt_hash`；而 `# noqa` 写在三引号字符串里只会变成提示词的一部分。
- **「打开弹层时重置表单」的 effect 换成了 `key` 重挂**：调用方本来就是
  `{creationContext ? <Wizard .../> : null}`，每次打开都是新实例，那个逐个 `setState` 的 effect
  只是把同一件事又做了一遍，还触发级联渲染告警。

剩余 2 个 `react-refresh/only-export-components` warning 保留（见第 8 节第 1 项）。

## 8. 仍缺失或需要决策的项

1. ~~`ruff` 32 个、`eslint` 10 个历史项~~ **已于本轮清零**，详见第 7.1 节。
   剩下的是 2 个 `react-refresh/only-export-components` **warning**（`ui/ProgressSteps.tsx` 与
   `ui/Toast.tsx` 在导出组件的同时导出了常量/hook）。拆文件只为消掉 warning 不划算，保留。
2. **`.env` 会让 `pytest` 失败**：`DATABASE_URL` 一旦在 `.env` 里有值，认证仓储就走真实
   PostgreSQL，`conftest` 的 bootstrap 直接 409。本地同时要跑服务和测试时，测试命令必须写成
   `DATABASE_URL= TEST_DATABASE_URL=... uv run pytest -q`。
3. **表格里截断的 ID / 配置指纹 / 错误信息只有原生 `title`**：桌面悬停约一秒可见，触屏看不到。
   完整内容在「详情」弹框里都有，所以不是唯一途径；若要在表格内做到键盘可达，需要给每个截断
   单元格加可聚焦触发器，会显著增加 Tab 停留点，值不值得另议。
4. **`mobile-chromium` 仍然没有视觉基线**，响应式改动只能靠手动跑。
5. **本次走查留下的数据已决定保留为演示数据**（2026-09-13）：知识库「Task10 闭环验证库」
   （`kb_6197ce8967db`，2 份资料、2 个索引版本、1 份 `official=true / passed=false` 的正式报告）
   与管理员账号 `claude-inspect`。它们是第 5 节全部证据的来源，也是目前**唯一**一条走完整
   链路、状态干净的数据：演示正式评测与三层验证时用它，不要用「企业知识库」——那个库的
   源文件已丢失，任何构建都会以 `SOURCE_FILE_MISSING` 失败。
   两点约束：**`claude-inspect` 的密码在协作记录里出现过明文**，这套数据若要带出本机（演示
   环境、共享数据库）必须先改密码；这个库和账号不要再被"清理临时数据"一类操作顺手删掉。

## 9. 后续建议

**P0**

- 把 `ruff` 与 `eslint` 接进 CI。历史项已于本轮清零（见第 7 节），但没有 CI 守着的检查会
  按第五条的规律重新烂掉——这次能一次修完，是因为它只积累了三十几条。

**P1**

- 给 `mobile-chromium` 建一组最小视觉基线（登录页 + 知识库详情 + 索引治理），只覆盖布局骨架，
  不绑具体数据。

**P2**

- 运行记录目前不带 `index_version_id`，页面靠 `operation_id` 反查评测记录。若 Operation 直接
  带上关联实体 ID，这一层反查可以去掉。
- 评测任务当前只支持 `corpus_v2` 与 `corpus_v2_paraphrased` 两个白名单数据集，扩展时需要同时
  改白名单与前端下拉。
