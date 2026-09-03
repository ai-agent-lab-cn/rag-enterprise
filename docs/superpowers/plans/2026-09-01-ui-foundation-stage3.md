# 详情页与面板（阶段 3）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把知识库详情页及其四个面板迁到基座上，7 个 Tab 换成 Radix `Tabs`，并借此一次性清掉前两阶段"共享所以删不掉"的那批 legacy class。

**Architecture:** 沿用阶段 2 的做法——每个文件一个任务，迁完 grep 后删它释放的 legacy CSS，跑视觉基线逐张人工确认，真实浏览器验证关键交互。详情页是壳、四个面板是内容，所以**先迁面板、最后迁壳**：壳负责 Tab 切换，等内容都稳定了再动它，避免壳和内容同时变导致定位困难。

**Tech Stack:** React 19 + TypeScript + Tailwind v4 + `radix-ui@1.6.7` + vitest/jsdom + Playwright

**Spec:** `docs/superpowers/specs/2026-08-31-frontend-ui-overhaul-design.md`

**前序阶段:** 阶段 1（`2026-08-31-ui-foundation-stage1.md`，基座 18 组件）、阶段 2（`2026-08-31-ui-foundation-stage2.md`，五个列表页）

## Global Constraints

- **Node 版本下限 `^20.19.0 || >=22.12.0`**，本机默认 v20.18.0 **不满足**。在它下面 `npm test` 输出 `Test Files no tests / Errors 8 errors` 而**退出码仍是 0**。**每一条 npm/npx 命令都必须前置：**
  ```bash
  export PATH="$HOME/.nvm/versions/node/v20.20.2/bin:$PATH"
  ```
  **每份实现报告必须贴 `node --version` 的实际输出**，没有 `v20.20.2` 的，测试结论不作数。
- **提交后必须先跑 `git log --oneline -1` 和 `git status --porcelain` 读到真实 hash 再写报告**。前两阶段出过多次实现者填了 git 里不存在的 hash。
- **类型检查只认 `npm run typecheck`（`tsc -b`）**，`npx tsc --noEmit` 是空跑的。
- **preflight 仍不能启用**，渲染 `<button>` 处必须显式 `border-0`。
- **字阶不改**：`xs 10 / sm 11 / base 12 / md 13 / lg 15 / xl 20`。
- **颜色只用于表意**。
- **`pattern` 属性的 `-` 必须转义**：写 `[A-Za-z0-9._\-]+`。Chrome 125+ 用 `v` flag 解析，未转义会**静默丢弃整个 pattern**，`App.test.tsx` 有测试扫描。
- **真实浏览器验证用 `npx playwright test`，不要用 Playwright MCP**——本机没装 Google Chrome，MCP 固定 `channel: "chrome"` 会报错。在 `frontend/e2e/` 写临时 spec（`__tmp-` 开头），跑完删掉 spec 和 `test-results/`。**页面间跳转必须走页面内导航**，令牌只存内存、`page.goto()` 会丢。
- **视觉基线凭据**：`SMOKE_ADMIN_USERNAME=demo` / `SMOKE_ADMIN_PASSWORD=DemoBaseline2026!`。
  **实现者一律不得 `--update-snapshots`**，把差异报给控制器逐张确认。
- **这个 demo 环境没起 index worker**（`scripts/index_worker.py`）。任何上传都会卡在 `queued`、文件删不掉（409 INDEX_JOB_ACTIVE）。验证优先用 `page.route()` 拦截，真留了数据要在报告里明说。
- 每个任务结束时 `npm test && npm run lint && npm run typecheck && npm run build` 必须全绿。
- 提交信息用中文，结尾附 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`。

---

## 这一阶段的特殊价值：终于能删那批共享 class

前两阶段反复遇到「想删但还有别人在用」。派发前实测：**阶段 3 的五个文件迁完后，以下 12 个 class 将全站无引用、可以删掉**：

`management-table`、`management-table-wrap`、`truncate-cell`、`base-type-tag`、`status-tag`、
`success-banner`、`detail-tabs`、`detail-tab-panel`、`knowledge-detail-summary`、
`version-governance`、`acl-governance`、`category-template-panel`、`parsing-panel`

其中 `status-tag`（曾被 6 个文件用）、`management-table`（曾被 3 个用）、`base-type-tag`（曾被 4 个用）是拖得最久的。

**仍然删不掉的**（阶段 4 才释放）：`index-loading`（DataSourcesPage）、`metadata-form`（DocumentPanel）、
`governance-table`（AcceptancePage / BadCasePage / EvaluationCenterPage）、
`error-banner`（16 个文件）、`evaluation-state`（7 个）、`product-page`（4 个）。

**每个任务迁完仍要逐个 grep 复核再删**——上面这份清单是我派发前查的，可能查漏（阶段 2 就漏过一个）。

---

## 任务顺序与理由

**先内容、后壳**：四个面板各自独立，先迁完；详情页是承载它们的壳（Tab 切换 + 顶部摘要），最后迁。
这样壳和内容不会同时变，出问题时能定位到是哪一层。

Task 1 `CategoryTemplateModal` → Task 2 `TechnicalDrawer` → Task 3 `ParsingPanel` →
Task 4 `KnowledgeBaseDataSourcesPanel` → Task 5 `KnowledgeBaseDetailPage`（含 7 Tab 换 Radix + 删 12 个 class）→ Task 6 阶段验收

前两个（Modal、Drawer）不在详情页 Tab 里，风险最低，用来先验证基座在弹层/抽屉场景下好不好使。

---

### Task 1: CategoryTemplateModal 迁移

**File:** `frontend/src/components/CategoryTemplateModal.tsx`（110 行）

它是知识库列表页的「知识库分类模板」弹框，不在详情页里。

**先读现状再动手**，把每个交互列成清单写进报告。已知要点：
- 它是**阶段 2「同一操作在不同页面必须行为一致」的反面教材**：`CLAUDE.md` 第二条记着——「同样文案的『新建分类』，分类模板弹框里空名称可点击并报错，知识库详情里却是禁用且无提示」。迁移后要确认这两处行为一致（详情页那半在 Task 5）。
- 用 `ui/Dialog`（已有）、表格部分用 `DataTable`、按钮用 `Button`
- 写操作要有 toast

**释放的 class**：`category-template-panel`（迁完 grep 确认后删）；`truncate-cell` 要等 Task 4/5 一起。

---

### Task 2: TechnicalDrawer 迁移

**File:** `frontend/src/components/TechnicalDrawer.tsx`（93 行）

问答页的技术细节抽屉。**不在详情页里，但属于本阶段一并处理的浮层类组件。**

要点：
- 它现在是自定义抽屉。**判断是否值得换成 Radix 的 Dialog/Popover**——如果它只是一个受控的侧边面板、没有焦点管理需求，保持现状可能更合适。**先分析再动手，把判断依据写进报告**，不要为了"统一"而强行替换。
- 用到的 `technical-drawer` class 只有它自己用，迁完可删

---

### Task 3: ParsingPanel 迁移

**File:** `frontend/src/components/ParsingPanel.tsx`（52 行）

详情页「解析与切片」Tab 的内容。

**不能丢的行为**（先自己读代码补全，以下是我已知的）：
- 切片策略的三个数字输入（目标长度、重叠等）及其校验
- `canManage` 控制的只读态
- 解析状态的展示

**释放的 class**：`parsing-panel`

---

### Task 4: KnowledgeBaseDataSourcesPanel 迁移

**File:** `frontend/src/components/KnowledgeBaseDataSourcesPanel.tsx`（112 行）

详情页「数据源」Tab 的内容。**它和阶段 2 已迁的 `DataSourcesPage` 是同类页面**——照那边的写法来，两处行为必须一致（`CLAUDE.md` 第二条）。

**释放的 class**：`management-table`、`management-table-wrap`、`success-banner`（需 Task 5 也迁完才全释放，逐个 grep 确认）

---

### Task 5: KnowledgeBaseDetailPage 迁移（本阶段最大的一个）

**File:** `frontend/src/components/KnowledgeBaseDetailPage.tsx`（77 行但 37 处 legacy class，全站最多）

**三件事一起做：**

**一、7 个 Tab 换成 `ui/Tabs`（Radix）。** 现在是手写的 `role="tablist"` + 7 个 `<button role="tab">`（`:58-65`）。ARIA 属性齐全，但**缺方向键导航**——规范是方向键在组内切换、Tab 键跳出整组，现在要按 7 下 Tab 才能到内容区。

`ui/Tabs` 只挂载当前 tab 的内容（阶段 1 的设计决定：详情页每个 tab 各自拉数据，全挂载会一次打七个请求）。**核对迁移后仍然只挂载当前 tab。**

Tab 上的计数（资料 5、数据源 5、分类管理 6…）用 `TabItem.count`。

**二、顶部摘要区**（名称/描述/文件占用/索引状态/更新时间）用基座重写。
注意「文件占用 0 KB」是后端 `source_file_bytes` 恒为 0 的问题，**spec 明确列在「不做」里，不要试图修**。

**三、删 12 个 class**（逐个 grep 复核后）：
`management-table`、`management-table-wrap`、`truncate-cell`、`base-type-tag`、`status-tag`、
`success-banner`、`detail-tabs`、`detail-tab-panel`、`knowledge-detail-summary`、
`version-governance`、`acl-governance`、`category-template-panel`、`parsing-panel`

**不能丢的行为**（先自己读代码补全）：
- `remove`/`upload` 回调**必须继续 reject**——阶段 2 Task 6b 刚修的，吞异常会导致 `DocumentPanel` 弹假成功提示
- 「分类管理」Tab 的新建分类：**空名称要可点击并报错，不能是禁用无提示**（`CLAUDE.md` 第二条点名的不一致，另一半在 Task 1）
- 「版本治理」「权限边界」「会话」三个 Tab 的内容
- 「在此知识库提问」跳转、「返回知识库」跳转

**这一页的视觉基线有 7 张**（`kb-detail-documents/data-sources/categories/parsing/versions/members/conversations`），改动面最大，逐张确认。

---

### Task 6: 阶段验收

- [ ] `npm test && npm run lint && npm run typecheck && npm run build` 全绿
- [ ] 12 个 class 全站 grep 为 0，且已从 `styles.css` 删除
- [ ] `styles.css` 行数/声明数对比（阶段 2 结束时：485 行 / 2317 声明）
- [ ] 裸元素 lint warning 对比（阶段 2 结束时：27）
- [ ] 视觉基线 17 张全部确认并更新
- [ ] 详情页 7 个 Tab 的方向键导航在真实浏览器里验证
- [ ] 「新建分类」在模板弹框与详情页两处行为一致（`CLAUDE.md` 第二条）
- [ ] **复核 Radix 是否仍走 `hideOthers` 而非 `inertOthers`**（Task 1 的 reviewer 追查发现）：
      `aria-hidden` 的 `suppressOthers`（`dist/es2015/index.js:164-167`）在浏览器支持原生
      `inert` 时改走 `inertOthers`（:149），**那条路径没有 `[aria-live], script` 豁免**。
      若 Radix 升级后改用它，`Toast` 容器挂 `aria-live` 的修复会**静默失效**，而现有测试
      照样绿（它测的是选择器命中，不是 Radix 走哪条分支）。
      核实方式：`grep -l "hideOthers\|suppressOthers" frontend/node_modules/@radix-ui/*/dist/index.mjs`
