# 概览 / 评测 / 问答（阶段 4）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 迁完剩下的 14 个组件，清空绝大部分 legacy CSS，让 preflight 试开的红图数逼近 0，为阶段 5 启用 preflight 铺平路。

**Architecture:** **按 legacy class 的共享关系分组，而不是按文件大小。** 前三个阶段反复遇到「迁完却一个 class 都删不掉，因为还有别人在用」。阶段 4 是最后一批，把共享同一组 class 的文件放进同一个任务，迁完立刻能清。

**Tech Stack:** React 19 + TypeScript + Tailwind v4 + `radix-ui@1.6.7` + vitest/jsdom + Playwright

**Spec:** `docs/superpowers/specs/2026-08-31-frontend-ui-overhaul-design.md`

**前序:** 阶段 1（基座 18 组件）、阶段 2（五个列表页）、阶段 3（详情页与四个面板）

## Global Constraints

- **Node 版本下限 `^20.19.0 || >=22.12.0`**，本机默认 v20.18.0 **不满足**。在它下面 `npm test` 输出 `Test Files no tests / Errors 8 errors` 而**退出码仍是 0**。**每一条 npm/npx 命令都必须前置：**
  ```bash
  export PATH="$HOME/.nvm/versions/node/v20.20.2/bin:$PATH"
  ```
  **每份实现报告必须贴 `node --version` 的实际输出。**
- **提交后必须先跑 `git log --oneline -1` 和 `git status --porcelain` 读到真实 hash 再写报告**。前三阶段多次出现实现者填了 git 里不存在的 hash。
- **类型检查只认 `npm run typecheck`（`tsc -b`）**，`npx tsc --noEmit` 是空跑的。
- **preflight 仍不能启用**，渲染 `<button>` 处必须显式 `border-0`。
- **字阶不改**：`xs 10 / sm 11 / base 12 / md 13 / lg 15 / xl 20`。
- **颜色只用于表意**。
- **`pattern` 的 `-` 必须转义**：`[A-Za-z0-9._\-]+`。Chrome 125+ 用 `v` flag 解析，未转义会静默丢弃整个 pattern。
- **浏览器验证用 `npx playwright test` 写临时 spec（`__tmp-` 开头，跑完删掉 spec 和 `test-results/`），不要用 Playwright MCP**——本机没装 Google Chrome。**页面间跳转必须走页面内导航**，令牌只存内存。
- **视觉基线凭据** `demo` / `DemoBaseline2026!`。**实现者一律不得 `--update-snapshots`**，差异报给控制器逐张确认。
- **demo 环境没起 index worker**，任何上传都会卡在 `queued`、文件删不掉（409）。验证优先用 `page.route()` 拦截。
- 每个任务结束时 `npm test && npm run lint && npm run typecheck && npm run build` 必须全绿。
- 提交信息用中文，结尾附 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`。

## 每个任务都要做的三件事

1. **先记录现状**：把该组件现有的每个交互列成清单写进报告，迁移后逐条对照。**这是防止迁移顺手丢功能的唯一手段**，这个仓库出过「测试全绿但功能不可用」不止一次。
2. **preflight 排雷**：把 CSS 改写成 Tailwind 时，凡依赖浏览器 UA 默认样式（`p`/`h1-h6`/`ul`/`ol`/`dl`/`table`/`blockquote` 的 margin、`table` 的 border-spacing、`ul` 的 list-style）的地方必须补显式类。方法：用 Playwright 量 `getComputedStyle()` 拿实际值；数据触发不到的分支注入探测元素实测。
   **报告要给出实际输出。没核的就写「未核」**——阶段 3 有个任务写了「已核对无依赖」而实际没核，那比不写更糟。

   > **⚠️ shorthand 的隐式清零是这里最容易漏的一类，Task 1 已经栽过一次。**
   >
   > 原 CSS 写 `margin: 7px 0 0`（3 值）或 `margin: 7px 0`（2 值）时，**right/bottom/left
   > 是被显式设成 0 的**。迁移时只看着 `margin-top` 补一个 `mt-[7px]`，另外三个方向就
   > 悄悄回落到浏览器默认值——而 `<dl>`/`<p>` 的 UA 默认 `margin-bottom` 是 `1em`，不是 0。
   >
   > 这个缺陷**现在完全看不出来**（preflight 没开，UA 默认值还在，视觉基线也是绿的），
   > 要到阶段 5 才会露出来，那时已经攒了几十处、根本查不清是哪次迁移引入的。
   >
   > **所以：量 computed style 时必须量四个方向**（`marginTop`/`Right`/`Bottom`/`Left`，
   > padding 同理），与 `git show <迁移前的 commit>:frontend/src/styles.css` 里级联后的
   > 真实生效值逐一对照。**报告里给出四个方向的数字**，不接受「已补齐」这种说法。

   > **⚠️ 四方向核对覆盖不到的一类：`inline` → `block` 让原本无效的声明生效。**
   >
   > Task 2 出过一次：`.acceptance-steps` 的 `<small>` 原本是 inline 元素，CSS 写着
   > `margin-top: 2px` —— 但**inline 元素的垂直 margin 不参与布局，那 2px 从来没有效力**。
   > 迁移时给它加了 `block`，这 2px 就**首次产生了可见间隙**。
   >
   > 量 computed style 发现不了：前后都是 `2px`，差别在 `display`。
   >
   > **改写时如果给某个元素加了 `block`/`flex`/`grid`，回头查一下原 CSS 有没有对它写过
   > 垂直 margin —— 那些声明在 inline 状态下是死的，一旦变成块级就会复活。**
   > 同类还有：inline 元素上的 `width`/`height`/`padding-top`/`padding-bottom`。
3. **legacy CSS 逐个 grep 后再删**：`grep -rn "class-name" frontend/src --include="*.tsx"` 返回空才删。**计划里的清单不可靠**——它在前三个阶段错过 4 次（ParsingPanel 说 1 个实际 9 个、Task 4 列 9 个实际 10 个、阶段 2 漏 `member-avatar`、`file-type-tag` 说法过时）。

4. **报 lint 变化要列「新增哪几条 / 消失哪几条」，不要只报总数差。**
   Task 2 出过一次：它新增了一条裸元素警告，同时删掉了另一条，**净数持平把新增项掩盖了**，
   报告因此漏报。方法：
   ```bash
   export PATH="$HOME/.nvm/versions/node/v20.20.2/bin:$PATH"
   cd frontend && npm run lint 2>&1 | grep -E "^\s+[0-9]+:[0-9]+"
   ```
   对比改动前后的具体行号列表，而不是 `✖ N problems` 那个总数。

## 可量化的靶子：看 `styles.css` 声明数，不要看 preflight 红图数

**控制器在阶段 3 报过一个假数字，这里更正。** 当时说「开 preflight 有 11/17 张红、已迁页面已不红、是可量化的进度条」——那是错的：那行 `@import "tailwindcss/preflight.css"` 躺在 CSS 块注释 `/* ... */` **内部**，只去掉行首缩进它仍是注释，preflight 压根没开启；测出的 11 张红其实是会话计数的数据漂移。

**在注释块外真正加上那行后，阶段 3 结束时的实测是 17 张全红**，与改造前一致。

结论：**preflight 不是可以逐页拆的雷，只要 `styles.css` 还在就开不了。** 各任务做的「preflight 排雷」意义是**防止新写的 Tailwind 代码新增 UA 依赖**，不是在逐步解锁 preflight。

**所以阶段 4 的进度用这两个数字衡量：**
- `styles.css` 声明数（会话起点 2629 → 阶段 3 结束 **1893**）
- 全站 legacy className 引用数（会话起点约 350）

**如果要试开 preflight 验证某处改动，必须先确认它真的开了**：用 Playwright 量裸 `<p>` 的 `getComputedStyle().marginTop`，开启时 `0px`、未开启 `14px`。不做这个验证的测量结果一律不作数。

---

## 任务分组与理由

**按 class 共享关系分组。** 派发前实测的共享矩阵：

| class | 使用者 |
| --- | --- |
| `error-banner` | **16 个文件**（全站最广，最后一个迁完才能删） |
| `evaluation-state` | AcceptancePage、AnswerEvaluationPage、BadCasePage、EvaluationPage、EvaluationCenterPage、OverviewPage |
| `quality-*` 全家（grid/card/bar/meta/heading）、`report-*` 全家（picker/details/context）、`readonly-note`、`evaluation-page`、`evaluation-content` | **AnswerEvaluationPage + EvaluationPage 两个共享，共 12 个** |
| `evaluation-panel`、`governance-table`、`evaluation-center` | AcceptancePage、BadCasePage、EvaluationCenterPage（+ App/AppNavigation 引用路由名） |
| `admin-page`、`admin-state`、`admin-loading`、`status-pill` | AuditPage(已迁)、MembersPage(已迁)、PermissionDeniedPage、SystemPage |
| `empty-copy` | ChatPage、KnowledgeBaseDataSourcesPanel、KnowledgeBaseDetailPage、OverviewPage、ParsingPanel |
| `product-page` | DataSourcesPage、KnowledgeBaseDetailPage、KnowledgeBasesPage、OverviewPage |

**顺序**：Task 1 评测孪生页 → Task 2 评测中心三兄弟 → Task 3 概览页 → Task 4 问答工作台 → Task 5 管理页与外壳 → Task 6 阶段验收。

先做评测那两组，因为它们的 class 只在组内共享，迁完立刻能清一大批；概览和问答涉及最多跨组共享 class，放中间；外壳（`App.tsx`）最后，因为它要还 `ToastProvider` 那笔债。

---

### Task 1: 评测报告孪生页

**Files:** `components/AnswerEvaluationPage.tsx`（20 处 legacy）、`components/EvaluationPage.tsx`（17 处）、`src/styles.css`

**这两个必须一起迁**——它们共享 12 个 class，分开迁的话一个都删不掉。

**共享的 12 个（两个都迁完后可删，逐个 grep 复核）：**
`quality-grid`、`quality-card`、`quality-bar`、`quality-meta`、`quality-heading`、
`report-picker`、`report-details`、`report-context`、`readonly-note`、
`evaluation-page`、`evaluation-content`、`section-kicker`（后者还被 TechnicalDrawer 用，**不能删**）

**不能删**：`error-banner`、`evaluation-state`（跨组共享）

**不能丢的行为**（自己读代码补全）：
- 报告选择器（`report-picker`）的切换与 URL 锚点联动
- 指标卡（`quality-card`/`quality-bar`）的阈值判定与通过/未通过配色
- `readonly-note` 的只读提示
- 两页的指标表格

**用基座**：指标卡用 `MetricCard`，表格用 `DataTable`，报告选择器用 `Select`，徽章用 `Badge`。

---

### Task 2: 评测中心三兄弟

**Files:** `components/EvaluationCenterPage.tsx`（24 处）、`components/BadCasePage.tsx`（10 处）、`components/AcceptancePage.tsx`（9 处）、`src/styles.css`

**共享的（三个都迁完后可删）：** `evaluation-panel`、`governance-table`、`evaluation-section`、`acceptance-*` 系列、`bad-case-*` 系列

注意 `evaluation-center` 这个名字同时是**路由标识**（`App.tsx`/`AppNavigation.tsx` 里的 `AppPage` 类型值），**不是 CSS class**，别误删。

**不能丢的行为**：
- 评测中心的四类质量纵向排列（注释解释了为什么不做成 Tab）
- Bad Case 的筛选与编辑
- 链路验收的八个步骤状态（passed/failed/blocked 三态）
- `BadCasePage.test.tsx` 现有测试必须继续通过

---

### Task 3: 概览页

**File:** `components/OverviewPage.tsx`（22 处）、`src/styles.css`

**这一页承载用户最初三个诉求里的第三个（列表/指标展示样式）中最明显的一处。**

**必须做的两件事：**
1. **6 套装饰色图标底收敛为中性**（`is-purple`/`is-green`/`is-blue`/`is-amber`/`is-slate`/`is-gray`）。spec 明确：「六种颜色不携带任何信息，只是装饰。这是"看不出主次"的直接原因」。**颜色只用于表意。**
2. **指标卡用 `MetricCard`**——现在「3」和「通过」两种视觉重量完全不同（`OverviewPage.tsx:7` 的 `valueClass`），四张卡读起来不像一组。

**独占 class（迁完可删）**：`overview-*` 全家、`quick-actions`、`latest-base*`、`action-icon`
**不能删**：`error-banner`、`evaluation-state`、`empty-copy`、`product-page`

---

### Task 4: 问答工作台

**Files:** `components/ChatPage.tsx`（32 处，全站最多）、`components/AnswerPanel.tsx`（9 处）、`components/SourceCard.tsx`（7 处）、`src/styles.css`

**三个一起迁**——它们构成问答页的整体，`SourceCard.test.tsx` 有现成测试。

**注意**：`ChatPage` 的 `.question-footer button { min-width: 76px; ... background: linear-gradient(...) }` 这类**标签选择器**在阶段 2 咬过新组件（把 `ⓘ` 撑成 76px 紫色椭圆）。迁移时这些规则会被删掉，届时要确认 `ⓘ` 和发送按钮的渲染仍然正确。

**不能丢的行为**：流式回答、引用来源联动、示例问题、`TechnicalDrawer` 的展开（阶段 3 已迁，别动它）。

---

### Task 5: 管理页与外壳

**Files:** `components/SystemPage.tsx`（13 处）、`components/PermissionDeniedPage.tsx`（2 处）、`components/AuthGate.tsx`（15 处）、`App.tsx`（9 处）、`components/AppNavigation.tsx`（4 处）、`src/styles.css`

**这一组要还一笔债**（spec 第二节记着）：

> **`ToastProvider` 必须移到 `App.tsx` 最外层。** 阶段 2 挂它时放在了 `AuthGate` 的提前 return **之后**（`App.tsx:107` return，Provider 在 137 行），未登录路径不在 Provider 内。`useToast` 在 Provider 外是 **throw** 而非静默降级——登录失败想给个 toast 提示，页面会直接白屏。

**共享 class 到此全部释放**：`admin-page`、`admin-state`、`admin-loading`、`status-pill`、
以及**全站 16 个文件用的 `error-banner`**——这是最后一批使用者，迁完就能清空。

**不能丢的行为**：登录/初始化引导、权限拒绝页、侧边导航的分组与激活态、topbar 的 portal 机制。

---

### Task 6: 阶段验收

- [ ] `npm test && npm run lint && npm run typecheck && npm run build` 全绿
- [ ] **preflight 试开的红图数**（阶段 3 结束时的基数见 ledger）——目标接近 0
- [ ] `styles.css` 行数/声明数对比
- [ ] 裸元素 lint warning 数对比，并确认剩余的都是 spec 第一笔债里记的 `ListItemButton` 场景
- [ ] 全站 legacy className 引用数对比（会话开始约 350 处）
- [ ] 视觉基线全部确认并更新
- [ ] `ToastProvider` 已在 `App.tsx` 最外层，未登录路径也在 Provider 内
- [ ] 概览页的 6 套装饰色已收敛为中性
