# 前端交互与视觉治理设计

## 目标

把 CRUD 交互、页面展示、列表与指标样式三层问题一次治理到位：建立 shadcn 风格的组件基座并把交互规则编码进类型，再逐页收口、删除 legacy CSS，最终启用 Tailwind preflight。

## 现状与问题

依据是当前代码与 `frontend/e2e/visual-baseline.spec.ts-snapshots/` 的 17 张基线截图。

**根因：列表有两套实现，没有共享组件。**
`KnowledgeBasesPage.tsx:43` 用真 `<table class="management-table">`，`MembersPage.tsx:168` 用
`div[role="table"]` + CSS grid 的 `.member-row`。三张表三个行高（成员 72px、知识库 ~59px、
文档 ~52px）。知识库页有搜索/筛选/排序/分页，成员页与文档页一个都没有。

**交互层缺陷（可指认）：**

| 位置 | 问题 |
| --- | --- |
| `MembersPage.tsx:220,223` | `reasonHidden` 让「不能修改自己的账号」只留在 `title` 里，页面上是两个不解释自己的灰按钮 |
| `DocumentPanel.tsx:107` | 「应用到 N 份」有两个禁用原因（没勾资料 / 没选分类），`reasonHidden` 的理由是「文案里的 N 就是原因」——N 只解释得了第一个 |
| `DocumentPanel.tsx:120` | checkbox 与文件名同处一个 `<td>`，没有独立列，截图上 checkbox 浮在文件名上方 |
| `DocumentPanel.tsx:133` | 删除确认的「确认删除」带 `autoFocus`，回车直接删 |
| `App.tsx:161` vs `MembersPage.tsx:124` | 顶栏操作两套机制：一处硬编码在 App 的路由分支里，一处走 `TopbarPortal` |
| 全站 | 写操作成功后无任何反馈，页面静默刷新 |

**样式层缺陷：**

- 所有表格无行分隔线——`--border` 等 5 个变量从未定义，相关 CSS 声明失效（`CLAUDE.md` 已记录）。
- 徽章三套样式：状态（可用/已索引）浅底胶囊、类型（独立知识库）另一套、「未授权」白底描边。
- 概览页 6 套装饰色图标底（`is-purple/green/blue/amber/slate/gray`），不携带任何信息。
- 表格内每个主链接都是紫色，一屏十几个紫色等于没有重点。
- 文档数、切片数等数字非等宽，逐行宽度不同，列看着是歪的。
- 知识库名过长被截断后徽章掉到第二行，行高不齐。

**规模：** `styles.css` 2629 条声明 / 396 个选择器 / ~130 个顶层类；24 个组件文件共约 350 处
legacy className 引用。

## 已定决策

1. 三层问题都做，顺序为 交互 → 展示 → 样式。
2. 引入 shadcn/ui 完整基座，不做定点修补。
3. 重做质感，**保留信息架构**（页面该有什么不变）。
4. 基座先行、逐页收口，而非按页面纵向切。
5. 依赖用官方聚合包 `radix-ui@1.6.7`，卸掉 `@radix-ui/react-dialog`——依赖条目净增 0，
   后续任何 primitive 不必再装包。已验证该包 `dependencies` 含 55 个 primitive，
   `dist/` 下逐 primitive 分文件，可 tree-shake。
6. **字阶不改**。6 档原样保留（`xs 10 / sm 11 / base 12 / md 13 / lg 15 / xl 20`），
   正文基准即左侧菜单项的 12px（`styles.css:57`）。
7. 装饰色收敛为中性、表格主链接去紫色，两条都做。颜色从此只用于表意。

## 组件基座

写在 `frontend/src/components/ui/`，沿用现有 5 个组件的路子（cva + `@theme` token +
把规则编码进类型），**不引 shadcn CLI**。

| 组件 | 编码进类型的规则 |
| --- | --- |
| `DataTable` | 泛型表格。`emptyState` 为必填 prop，没有空态编译不过；`columns` 显式声明 `align` 与 `width`，消灭列宽漂移 |
| `RowActions` | 行操作唯一出口。≤2 个平铺，≥3 个收进 `⋯`（Radix DropdownMenu 直接在组件内部使用，**不单独包一层**：全仓库只有行操作这一个消费者，拆成两个文件只会多一层没人直接用的间接层）；`destructive` 项强制排最后并带分隔线 |
| `Badge` | 三套徽章收敛为一个。variant：`neutral`/`success`/`warning`/`danger`/`brand`。**状态用圆角胶囊、类型用方角标签**，从形状区分语义。（初稿还列了第六档 `outline`，阶段 1 实施时未做：现有三套徽章里没有一个是描边样式，它是凭空多出来的一档，真有需要时再加） |
| `Toolbar` | 列表页工具栏骨架：左侧筛选、右侧操作；批量操作区仅在有选中时出现 |
| `Pagination` | 从 `KnowledgeBasesPage.tsx:44` 抽出，成员页/文档页/审计页共用 |
| `EmptyState` | 区分「没数据」与「筛选无结果」两种文案及对应行动按钮 |
| `Skeleton` | 替掉 `.evaluation-state.pulse` 的「正在读取…」，骨架屏保持布局稳定 |
| `MetricCard` | 数值统一 `tabular-nums` 与同一字阶，消灭「3」和「通过」两种视觉重量 |
| `Tabs` | Radix 包装，替掉详情页自定义 tab |
| `Checkbox` | Radix 包装，替掉裸 `<input type="checkbox">` |
| `Tooltip` | Radix 包装，承载禁用原因与截断文本全名 |
| `useConfirm` | 确认弹层 hook。`consequence`（后果描述）必填 |
| `Toast` | 自建约 60 行，承接写操作的成功/失败反馈 |

`Select` 保留原生 `<select>` 不动——换 Radix 只为改选项样式，可访问性与移动端体验反而变差。
`Dialog`/`Input`/`Button`/`FileButton` 只改 import 来源与视觉参数。

## 交互规则的编码化

不靠约定，靠类型与 lint 强制：

1. **废掉 `reasonHidden`**。`blockedReason` 永远可见，但渲染位置从「按钮下方的 `<small>`」
   （会撑高行）改为 **Tooltip + 按钮上的 `aria-describedby` + 按钮末尾一个 `ⓘ` 图标**。
   `ⓘ` 是可见锚点，触屏可点开——这是对 `CLAUDE.md` 第一条的改写而非违反：该条禁止的是
   **只有** `title`、没有任何可见入口。

   **这一条落在阶段 2 的第一个任务，不在阶段 1。** 全仓库有 8 处 `blockedReason` 没加
   `reasonHidden`（`ChatPage.tsx:111,119`、`ParsingPanel.tsx:45`、
   `KnowledgeBaseDetailPage.tsx:71`、`DataSourcesPage.tsx:32` 等），改 Button 的渲染方式
   会立刻改变它们的外观。放在阶段 1 会让「基线 17 张全绿」这个验证点失效——而那正是
   阶段 1 唯一能证明基座没有副作用的证据。阶段 1 只做纯新增：`Tooltip` 组件本身、
   `blockedReason` 接受 `string | string[]`（单字符串行为不变）。
2. **`blockedReason` 支持数组**，多个原因全部列出，不再只显示第一个。
3. **删除确认统一模板**：`useConfirm()` hook，强制传 `consequence`（后果描述）；
   破坏性操作的确认按钮统一 `destructive` variant 且**不 `autoFocus`**。
4. **写操作完成必须给反馈**，由 `Toast` 承接，成功与失败都有。
5. **顶栏操作统一走 `TopbarPortal`**，删掉 `App.tsx:161` 的硬编码分支。
6. **加 ESLint 规则**：`components/` 下禁止裸 `<table>` / `<button>` / `<input type="checkbox">`，
   必须走基座组件。这是 `CLAUDE.md` 第五条「加了约束就加上能自动发现它被破坏的检查」。
   `components/ui/` 自身豁免（基座内部必须写裸元素），用 ESLint 的 `overrides` 按目录排除；
   规则在阶段 1 就加上，但先设为 `warn`，阶段 5 全部迁完后提为 `error`——否则阶段 2-4
   期间 `npm run lint` 会一直红，红色警报久了就没人看。

## 质感参数

| 项 | 现在 | 改成 |
| --- | --- | --- |
| 字阶 | 6 档 | **不动** |
| 表格行高 | 成员 72px / 知识库 ~59px / 文档 ~52px | 统一 `h-14`(56px)；`density="compact"` 为 `h-11`(44px)，仅审计记录页使用（该页行数可达数千） |
| 行分隔 | 无（变量未定义，规则失效） | `border-b border-divider`，最后一行不画 |
| 卡片 | 边框 + 无阴影 | 不变。阴影只留给浮层 |
| 装饰色 | 6 套图标底色 | 全部中性灰底 + 中性图标 |
| 主链接 | 表格内一律紫色 | `text-ink font-medium`，hover 才变紫 |
| 数值 | 普通字体 | `tabular-nums` + 右对齐 |
| 主色与基调 | `#5744dd` + 白卡片 | 不变 |

## 阶段划分

**阶段 1 · 基座。** 装 `radix-ui@1.6.7`、卸 `@radix-ui/react-dialog`、写 13 个新组件及各自的
vitest 用例、调 `@theme` token、加 ESLint 规则。不改任何页面，视觉基线应当 17 张全绿——
这是基座正确性的第一个证据。

**阶段 2 · 列表页收口。** 第一个任务是改 `Button` 的 `blockedReason` 渲染方式并删掉
`reasonHidden`，连同修正受影响的 8 个调用点。

> **这个任务是 `RowActions` 接入 `DataTable` 的强制前置，顺序不能颠倒。** 阶段 1 已把
> `RowActions` 平铺形态的禁用原因改为可见小字（原先只在 `title` 里，违反第一条规则）。
> 但可见小字会多占一行，撑破 `DataTable` 保证的统一行高 `h-14`——而消灭行高不一致
> 正是 `DataTable` 存在的理由之一。两个约束的共同解就是本任务：改成 Tooltip + `ⓘ`
> 图标后，原因既可见又不占行高。在此之前把 `RowActions` 塞进任何表格，都会让那张表
> 出现高矮不齐的行。
>
> **`ⓘ` 图标必须是独立的可用元素，不能直接把 `Tooltip` 包在禁用的 `Button` 外面。**
> 真实浏览器里 `disabled` 的 `<button>` 不派发 `pointerenter`，而 jsdom 会——在 jsdom
> 里测「包住禁用按钮的 Tooltip」会绿着骗人，浏览器里却永远弹不出来。

此后 `KnowledgeBasesPage` → `MembersPage` → `DocumentPanel` →
`AuditPage` → `DataSourcesPage`。成员页那套 `div[role=table]` 直接删掉换 `DataTable`。
每迁完一个文件删它专属的 legacy CSS 规则，跑一次基线看差异。

**阶段 3 · 详情页与面板。** `KnowledgeBaseDetailPage`（37 处 legacy class，最多）
+ `ParsingPanel` + `KnowledgeBaseDataSourcesPanel` + `CategoryTemplateModal` + `TechnicalDrawer`，
自定义 tab 换 Radix `Tabs`。

**阶段 4 · 概览 / 评测 / 问答。** `OverviewPage`、`EvaluationCenterPage`、
`AnswerEvaluationPage`、`EvaluationPage`、`BadCasePage`、`AcceptancePage`、`ChatPage`、
`AnswerPanel`、`SourceCard`、`SystemPage`、`AuthGate`。

> **迁 `AuthGate` 时必须先把 `ToastProvider` 移到 `App.tsx` 的最外层。** 阶段 2 挂它时
> 放在了 `AuthGate` 的提前 return **之后**（`App.tsx:107` return，Provider 在 137 行），
> 所以未登录路径不在 Provider 内。当前无影响（`AuthGate` 不用 toast），但 `useToast`
> 在 Provider 外是 **throw** 而不是静默降级——登录失败想给个 toast 提示，页面会直接白屏。
> 阶段 2 没有顺手改，是因为那要调整 `App.tsx` 的渲染结构、连带重跑 `App.test.tsx`
> 与视觉基线，为一个当时无影响的问题付这个代价不值。

**阶段 5 · 收尾。** 清空 `styles.css` 残余、**启用 preflight**、重跑全部基线并逐张确认、
更新 `docs/design/ui-foundation-tokens.md`。

> **阶段 5 已知的三笔债，都要在这一阶段一次性还掉，不要零敲碎打：**
>
> **四笔债的最终状态（2026-09-03 阶段 5 收尾）：**
>
> | 债 | 状态 | 落点 |
> | --- | --- | --- |
> | 一、`ListItemButton` | **已还** | `ui/ListItemButton`（`ca29661`）收口 9 处；ESLint 规则已从 `warn` 提为 `error`（命中数归零后） |
> | 二、Radix 是否仍走 `hideOthers` | **已核，结论成立，但仍是监测点** | 见下方复核记录 |
> | 三、`DataSourcesPage` 把 `aborted` 显示成「未索引」 | **不修**（超出前端范围，原判维持） | 修法在后端或类型层 |
> | 四、preflight 只能在 `styles.css` 清空后开 | **已还** | `styles.css` 已删除，preflight 已启用（`0709afa`） |
>
> **债二的复核记录（2026-09-03）：** `aria-hidden@1.2.6` 下 `suppressOthers`
> （`dist/es2015/index.js:164-166`）**没有任何 `@radix-ui` 包调用它**。四个 import
> `aria-hidden` 的包全部直调 `hideOthers`：`react-dialog:145`、`react-popover:131`、
> `react-menu:137`、`react-select:331`。而 `[aria-live], script` 豁免就在 `hideOthers`
> 内部（`:131-133`，注释指向 aria-hidden issue #10），正是 `Toast` 常驻容器方案依赖的
> 那一条。另外 `react-dialog` 的 `hideOthers` 只出现在 `DialogContentModal` 的
> `useEffect(…, [])` 一处，非 modal 分支完全不调，而 `ui/Dialog` 的 Root 不传 `modal`
> （默认 `true`）恒走 modal 支——**不存在「hideOthers 被调用时容器不在 DOM 里」的分支**。
>
> **这笔债只是当前版本下成立，不是永久解决。** 风险原样保留：若将来 Radix 改用
> `suppressOthers` 且浏览器支持原生 `inert`，就会走 `inertOthers`（`:143`），
> 那条路径**没有 `[aria-live]` 豁免**，`Toast` 的可达性会静默失效而测试照样绿。
> 升级 `radix-ui` 或 `aria-hidden` 后要重跑上面那条 grep。
>
> **一、`ListItemButton` 基座组件。** 裸元素 ESLint 规则要从 `warn` 提为 `error`，
> 而 `KnowledgeBaseDetailPage`（7 处）、`ChatPage`、`ParsingPanel`（2 处）有意保留了原生
> `<button>` 作列表项。这些不是该改的错，是 `ui/Button` 覆盖不到的场景——把它的固定高度
> （`h-7/h-9/h-11`）和 CTA 单行语义套到全宽多行可选中列表项上，需要覆盖掉全部 variant，
> 抽象收益为负。**正确的偿还方式是新增一个 `ListItemButton` 基座组件**（支持选中态、
> 多行内容、全宽），而不是给每处加 `eslint-disable`。
>
> **二、复核 Radix 是否仍走 `hideOthers` 而非 `inertOthers`。** `aria-hidden` 的
> `suppressOthers`（`dist/es2015/index.js:164-167`）在浏览器支持原生 `inert` 时改走
> `inertOthers`（:149），**那条路径没有 `[aria-live], script` 豁免**。若 Radix 升级后改用它，
> `Toast` 容器挂 `aria-live` 的修复会**静默失效**，而现有测试照样绿（它测的是选择器命中，
> 不是 Radix 走哪条分支）。核实：
> `grep -l "hideOthers\|suppressOthers" frontend/node_modules/@radix-ui/*/dist/index.mjs`
>
> **三、`DataSourcesPage` 会把熔断状态显示成「未索引」。**
> 后端在删除熔断器触发时把数据源状态设为 `aborted`（`data_source_sync.py:532`，
> `SYNC_DELETE_CIRCUIT_BREAKER`），专门区别于普通 `failed`。但
> `backend/app/main.py:2018` 的判定是
> `index_status = raw_status if raw_status in {"queued","running","succeeded","failed"} else "idle"`
> —— **`aborted` 被塌缩成 `idle`**。而 `types.ts:294` 的 `index_status` 类型也不含它，
> 只有标了 `@deprecated` 的 `sync_status`（`:296`）有。
>
> 阶段 2 迁移 `DataSourcesPage` 时用了 `index_status`（`:19,35,91,127-136`，无 `aborted` 兜底），
> 所以**同步被熔断器拦下时，那一页显示「未索引」，用户看不出发生了什么**。
> 阶段 3 的 `KnowledgeBaseDataSourcesPanel` 因此有意保留了 `sync_status`——
> 在「跨页一致」与「不丢失状态信息」冲突时选了后者。
>
> **正确的修法在后端或类型层**（让 `index_status` 支持 `aborted`，或前端补兜底分支），
> 超出前端改造范围，与 `source_file_bytes` 恒为 0 同类。修之前这两页会看起来不一致，
> 那是有意的，不要"顺手统一"成都用 `index_status`——那会把信息丢失扩散到两页。
>
> **四、preflight 只能在 `styles.css` 清空之后开，它不是可以逐页拆的雷。**
>
> **怎么正确地试开**：那行 `@import "tailwindcss/preflight.css" layer(base);` 躺在一个
> CSS 块注释 `/* ... */` **内部**，只去掉行首缩进它仍然是注释。必须在注释块**外面**
> 另加一行才会生效。**每次试开都要先验证它真的开了**——用 Playwright 量一个裸 `<p>`
> 的 `getComputedStyle().marginTop`，preflight 开启时是 `0px`，未开启是 `14px`。
>
> 控制器在阶段 3 期间因为没做这个验证，误报过一次「11/17 张红、已迁页面不红、是可量化
> 的进度条」——那 11 张其实是会话计数的数据漂移，preflight 压根没开。
>
> **阶段 3 结束时的真实测量：17 张全红**（已用探针确认 preflight 生效）。这与 `tailwind.css`
> 注释里记录的改造前状态一致，**说明前三个阶段并没有让 preflight 变得更可开**——
> 因为 `styles.css` 还剩 1893 条声明，而它们正是建立在浏览器默认样式之上的那批。
>
> **所以各任务做的「preflight 排雷」意义是防止新写的 Tailwind 代码新增 UA 依赖，
> 不是在逐步解锁 preflight。** 真正的解锁条件只有一个：`styles.css` 清空。
> 衡量进度请看 `styles.css` 的声明数（起点 2629 → 阶段 3 结束 1893），不要看红图数。
>
> **实际结果（2026-09-03）：这个判断成立。** `styles.css` 清空到 0 之后开 preflight，
> 22 张基线里高度变化 >50px 的是 **0 张**、9 张高度完全不变，最大变化
> `dialog-create-member −46px`；而阶段 3 用同一个探针测的是 17 张全红加多页腰斩
> （评测中心 2745→1491px）。**唯一变量就是 `styles.css` 从 1893 条声明变成 0。**

## 阶段 5 收尾指标（2026-09-03 实测）

| 指标 | 会话起点 | 阶段 4 末 | 阶段 5 末 |
| --- | --- | --- | --- |
| `styles.css` | 515 行 / 2629 条声明 | 117 行 | **文件已删除** |
| legacy className 引用 | ~350 处 | 75 处 / 13 种 | **0**（残留命中全是注释、TS 变量名，及 `App.test.tsx:360` 那条 `.toBeNull()` 断言） |
| `@layer` 声明 | 含 `legacy` | 含 `legacy` | `theme, base, components, utilities`——`legacy` 已取消 |
| preflight | 未启用 | 未启用 | **已启用**，产物验证见下 |
| `no-restricted-syntax` | — | 15 条 warn | **0 条，且规则已提为 `error`** |
| 视觉基线 | 11 张 | 17 张 | **22 张**，含登录页 2 张与 Overview 的加载/错误/空态 3 张 |
| 测试 | — | 180 / 24 files | **192 / 26 files** |
| 硬编码中性灰边框 | — | 42 处 | **0**（收敛到 `line`/`divider`/`line-firm`） |

**preflight 生效的产物级证据**（不是看 import 在不在）：`dist/assets/*.css` 里有
`@layer base{*,:after,:before,::backdrop{box-sizing:border-box;border:0 solid;margin:0;padding:0}`
接 `::file-selector-button{…}` 再接 `html,:host{-webkit-text-size-adjust:100%;tab-size:4;line-height:1.5;…}`。
`::file-selector-button` 与 `::backdrop` 是 v4 preflight 的独有选择器——用 v3 的
`blockquote,dd,dl,figure` 逐元素写法去验会得到假的「未生效」结论，控制器踩过一次。

**ESLint 规则提 `error` 后当场验过它真会拦**：临时在 `src/components/` 放一个带
裸 `<table>`/`<button>`/`<input type="checkbox">` 的探针文件，三条全部报 `error`
（`3 errors`），删除探针后回到 `0 errors`。只改配置不验证等于没验证。

## 验证

每阶段必跑，附实际输出：

```bash
cd frontend && npm test && npm run lint && npm run typecheck && npm run build
```

类型检查只认 `npm run typecheck`（`tsc -b`）——根 `tsconfig.json` 是 `files: []` +
project references，`npx tsc --noEmit` 是空跑的（`CLAUDE.md` 第七条）。

需要管理员凭据的验证（视觉基线对比、真实浏览器 CRUD 走查）从阶段 1 收尾起用得上：

```bash
cd frontend
SMOKE_ADMIN_USERNAME=... SMOKE_ADMIN_PASSWORD=... \
  npx playwright test visual-baseline --project=desktop-chromium
```

跑基线前先给 `visual-baseline.spec.ts` 那两处点开弹层的操作补稳定等待——该脚本
2026-08-30 曾误把 reader 改成管理员，成因已记在脚本注释里。浏览器走查用已启用的
playwright MCP 驱动，不再手搓一次性脚本。

## 不做

- 不改信息架构：页面清单、导航分组、详情页 7 个 tab 的划分、概览页放什么，全部保持。
- 不改主色与白卡片基调，不做深色模式。
- 不动后端。`source_file_bytes` 恒为 0 导致「存储空间 0 KB」是后端问题，本设计只记录不修。
- 不引 shadcn CLI、不引 sonner 等第三方 toast、不把 `Select` 换成 Radix。
- 不重拍视觉基线作为常规动作——它是迁移期的变化确认工具，只在阶段 5 结束后逐张确认并更新。
