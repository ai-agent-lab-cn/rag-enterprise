# UI Foundation 阶段 5 实施计划：清空 styles.css 与启用 preflight

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> to implement this plan task-by-task.

**Goal:** 清空 `frontend/src/styles.css`、启用 Tailwind preflight，并结清阶段 1–4 累积的样式债。

**Architecture:** legacy 层（`@import "./styles.css" layer(legacy)`）现存 117 行 / 32 种 class
选择器，其中 13 种仍被引用（75 处）、约 17 种已死。清空路径分三段：先删死规则，再把 13 种
活 class 收进基座或 utility，最后把元素级全局规则搬进 `tailwind.css` 并开 preflight。

**Tech Stack:** Tailwind v4 + `@theme` token + cva/tailwind-merge，vitest + jsdom，Playwright 视觉基线。

**Spec:** `docs/superpowers/specs/2026-08-31-frontend-ui-overhaul-design.md`

## Global Constraints

- **字号不改**：6 档字阶原样保留，正文基准 12px。迁移不得改变任何文字的 computed 字号。
- **颜色收敛**：表意色（成功/警告/危险/状态）必须走 token；装饰色能走就走。token 在 `frontend/src/tailwind.css` 的 `@theme` 块。
- **信息架构不动**：只改质感，不改页面结构、不增删功能、不改交互流程。
- **用基座别自创**：`frontend/src/components/ui/` 下 18 个组件，每个文件顶部有中文注释写明设计理由。
- **不许改弱测试。** 基数：180 tests / 24 files 全绿，lint 16 warnings / 0 errors。
- **Node 下限**：每条 npm/npx 命令前置 `export PATH="$HOME/.nvm/versions/node/v20.20.2/bin:$PATH"`。默认 v20.18.0 下 `npm test` 输出「no tests」却返回**退出码 0**。
- **类型检查用 `npm run typecheck`**（`tsc -b`）。`npx tsc --noEmit` 是空跑的（根 tsconfig 是 `files: []` + project references）。
- **`max-[Npx]:` 写 N+1**：Tailwind v4 的 `max-[767px]:` = `width < 767`，原 CSS `@media (max-width:767px)` = `≤767`。正确写法 `max-[768px]:`。
- **不许用 `git checkout <commit> -- <files>` 或 `git stash` 取 A/B baseline**，用 `git worktree`。前者被中断会把工作区留在半回退状态，且外观上与「实现者主动回退」无法区分。
- **像素差异不许整体归因**。不接受「数据漂移」「抗锯齿噪声」「渲染噪声」之类的整体结论——这个说法已经藏住过 3 次真回归。判定噪声的唯一合法方式是拿出该元素 `getBoundingClientRect` 与相关 computed 属性的 A/B 数值，证明两版数值相同、差异只在亚像素。**数值不同就是位移。**
- **测多视口必须每视口重新加载页面**，不要循环 `setViewportSize`（会读到过渡中间态，已造成过一次假报告）。

---

## 关键前提：preflight 的破坏范围已实测（2026-09-02）

`tailwind.css:14-26` 的注释记录的是 **2026-08-30** 的探测结论（当时 legacy 层 2720 条声明）：
17 张全红、评测中心 2745→1491px 腰斩、概览 1000→826px、「整体散架」。

**那个结论已经过时。** 控制器在 `5587055` 之后重新探测（legacy 层剩 ~200 条声明）：

| 页面 | 基线高 | 开 preflight 后 | Δ | 差异像素占比 |
| --- | --- | --- | --- | --- |
| `dialog-create-knowledge-base` | 516 | 428 | **−88** | 7.53% |
| `kb-detail-members` | 1023 | 948 | −75 | 2.60% |
| `kb-detail-versions` | 869 | 799 | −70 | 2.92% |
| `dialog-create-member` | 488 | 422 | −66 | 5.15% |
| `kb-detail-parsing` | 999 | 938 | −61 | 2.37% |
| `kb-detail-documents` | 816 | 756 | −60 | 2.73% |
| `evaluation-center` | 2869 | 2909 | **+40** | 2.49% |
| `kb-detail-conversations` | 1025 | 1000 | −25 | 2.48% |
| 其余 9 张 | — | 高度不变 | 0 | 0.54%–2.98% |

**`npm test` 在 preflight 开启下 180/180 全绿。** 17 张仍全红，但多数只是 1–3% 的细节位移，
不再是散架。破坏点集中在**弹层**（`modal-form` 仍在 legacy）与 **kb-detail 的表格类 Tab**
（`governance-table`/`metadata-form` 仍在 legacy）。

**推论：先迁完剩余 13 种活 class，再开 preflight。** 顺序颠倒会让每一处位移都无法归因。

`tailwind.css` 那段过时注释在 Task 6 一并改写。

---

## 剩余 legacy class 归属（控制器实测，2026-09-02）

**仍被引用的 13 种（75 处）：**

| class | 引用 | 文件数 | 处理方向 |
| --- | --- | --- | --- |
| `error-banner` | 23 | 16 | **新建 `ui/ErrorBanner`**（Task 3） |
| `empty-copy` | 15 | 5 | 就地 utility 或并入 `ui/EmptyState`（Task 4） |
| `sr-only` | 11 | 10 | **删规则即可**，Tailwind utilities 层已产出同名类（已验证 dist 命中） |
| `section-kicker` | 7 | 3 | 就地 utility（Task 4） |
| `modal-form` | 4 | 4 | 就地 utility（Task 4，preflight 的主要破坏点） |
| `product-page` | 4 | 4 | 页面容器，就地 utility（Task 4，波及全部基线） |
| `admin-page` | 2 | 2 | 同上 |
| `confirm-copy` | 2 | 2 | 并入 `useConfirm`（Task 4） |
| `metadata-form` | 2 | 2 | 就地 utility（Task 4） |
| `pulse` | 2 | 2 | 搬进 `@theme` 的 animation（Task 6） |
| `governance-table` | 1 | 1 | 就地 utility；注意它用了**未定义**的 `var(--border)`/`var(--text-muted)`（Task 4） |
| `member-avatar` | 1 | 1 | 就地 utility（Task 4） |
| `template-apply-option` | 1 | 1 | 就地 utility（Task 4） |

**约 17 种已死（Task 1 删）：** `base-card` `base-grid` `chat-workspace` `conversation`
`create-card` `detail-heading` `detail-layout` `eyebrow` `hero-copy` `history-turn`
`member-head` `overview-grid` `page-heading` `quality-summary` `stat-grid`
`template-copy-note` `workspace`

> **统计假阳性三个坑（每个任务都要防）：**
> 1. `css` 是假阳性——来自 `import "./tailwind.css"`。
> 2. **`index-loading` 看似零引用实际在用**：通过 `className={syncing ? "index-loading" : …}`
>    传给 `Badge`（`DataSourcesPage.tsx:135`、`KnowledgeBaseDataSourcesPanel.tsx:184`），
>    且 `App.test.tsx:345` 有 `toHaveClass("index-loading")` 断言。
>    **只匹配 `className="..."` 字面量的正则会漏掉传给组件 prop 的动态 class。**
> 3. `page-heading` 在 `App.test.tsx:357` 的命中是 `.toBeNull()` 断言（断言它不存在），
>    是死 class 但测试引用了名字。
> 4. `grep -w brand` 会命中 `tone="brand"`/`text-brand`/`accent-brand` 等 token，不是 class。
>
> **每一条规则删除前都要自己 `grep -rn` 复核，并看清命中的是不是 className。**
> 控制器给的清单在前四个阶段错过 5 次。

---

## Task 1: 清理死规则

**Files:**
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: 无
- Produces: 更小的 legacy 层。Task 4/6 在此基础上继续。

预期 117 行 → 约 60 行。

- [ ] **Step 1: 逐条复核上面 17 种「已死」清单**

对每一个跑：
```bash
cd frontend/src
grep -rn "\bCLASSNAME\b" . --include="*.tsx"
```
命中要逐条打开看是 className、TS 变量名、注释，还是 `.toBeNull()` 断言。
**清单不可靠，以你的 grep 为准。** 发现清单错了就在报告里写明。

- [ ] **Step 2: 删规则**

注意**两种删法**：
- 独立成行的规则 → 删整行
- **藏在 `@media` 一行多选择器里的 → 必须行内编辑**。例如 `styles.css:20` 一行里有
  `.product-page, .workspace`、`.page-heading`、`.stat-grid, .base-grid`、
  `.stat-grid article`、`.overview-grid`、`.create-card, .detail-heading` 六组，
  其中只有 `.product-page` 是活的。**删整行会误删活规则。**

这正是这批死规则一路留到现在的原因：前四个阶段的「grep class 名 → 删整行」流程
结构性地发现不了它们。

- [ ] **Step 3: 验证**

```bash
export PATH="$HOME/.nvm/versions/node/v20.20.2/bin:$PATH"
cd frontend && npm test && npm run lint && npm run typecheck && npm run build
```
预期全绿、lint 仍 16 warnings / 0 errors。

- [ ] **Step 4: 视觉基线**

```bash
SMOKE_ADMIN_USERNAME=demo SMOKE_ADMIN_PASSWORD='DemoBaseline2026!' \
  npx playwright test visual-baseline --project=desktop-chromium
```
**不加 `--update-snapshots`。** 删死规则应当 **17 张全绿**——它们没有使用者，删了不该有任何
像素变化。**任何一张红都说明那条规则其实是活的**，回到 Step 1 找出是哪条。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/styles.css
git commit -m "refactor: 清理 styles.css 中零使用者的死规则"
```

---

## Task 2: `max-[Npx]:` 全仓 1px 订正

**Files:**
- Modify: `frontend/src/components/*.tsx`（全仓扫描后确定）

**Interfaces:**
- Consumes: 无
- Produces: 无（纯修正）

Tailwind v4 把 `max-[767px]:` 编译成 `@media not all and (width >= 767px)`，语义是 **`< 767`**；
原 CSS 的 `@media (max-width: 767px)` 是 **`≤ 767`**。**视口恰为 767px 时 utility 不生效而原
CSS 生效。** 已实测：767px 下 `matchMedia("(max-width: 767px)")` 为 true 但三处
`max-[767px]:` utility 全部未生效。

- [ ] **Step 1: 全仓扫描**

```bash
cd frontend/src
grep -rn 'max-\[[0-9]*px\]:' . --include="*.tsx" -o | sort | uniq -c | sort -rn
```
列出所有断点值与出现次数，写进报告。

- [ ] **Step 2: 逐个判断该改成什么**

规则：`max-[Npx]:` → `max-[(N+1)px]:`。常见值：
- `max-[767px]:` → `max-[768px]:`
- `max-[560px]:` → `max-[561px]:`
- `max-[900px]:` → `max-[901px]:`
- `max-[1024px]:` → `max-[1025px]:`

**但先核对每一处的原 CSS 断点意图**：用 `git show <某个迁移前 commit>:frontend/src/styles.css`
查它对应的 `@media` 是 `max-width: N` 还是别的。**不要机械 +1**——如果某处原本就是
`width < N` 的意图，改了反而错。核对不了的写「未核」。

Task 5 的实现者新写的断点已经是 N+1 正确写法（`561/768/1001/1025/1181`），
**那几个不要再加**。

- [ ] **Step 3: 改**

- [ ] **Step 4: 验证边界**

写临时 spec（`__tmp-` 开头，跑完删）在**边界视口**实测：
```ts
// 767px 下这些 utility 现在应当生效
await page.setViewportSize({ width: 767, height: 720 });
```
**每个视口重新加载页面**，不要循环 resize。
给出至少 3 个改动点在 `N` 与 `N+1` 两个视口下的 computed 值 A/B 对比。

- [ ] **Step 5: 全套验证 + 视觉基线**

基线在 1280px 拍摄，理论上 **17 张全绿**。红了要指认到元素。

- [ ] **Step 6: 提交**

---

## Task 3: 新建 `ui/ErrorBanner` 并收口 23 处

**Files:**
- Create: `frontend/src/components/ui/ErrorBanner.tsx`
- Create: `frontend/src/components/ui/ErrorBanner.test.tsx`
- Modify: 16 个使用 `error-banner` 的组件（自己 grep 确定）
- Modify: `frontend/src/styles.css`（删 `.error-banner` 规则）

**Interfaces:**
- Consumes: `cn` from `./cn`
- Produces: `<ErrorBanner>{message}</ErrorBanner>`——供后续任何需要错误横幅的地方使用。
  props: `children: ReactNode`、`className?: string`。内部带 `role="alert"`。

`error-banner` 是剩余 class 里最大的一块（23 处 / 16 文件），且是**表意色**（错误提示），
正是全局约束点名要走 token 的地方。

- [ ] **Step 1: 读现状**

```bash
cd frontend/src
grep -rn 'error-banner' . --include="*.tsx" | cut -c1-160
```
原规则（`styles.css:15`）：
```css
.error-banner { margin-bottom: 14px; padding: 11px 13px; color: #bd3535;
  border: 1px solid #f0cccc; border-radius: 7px; background: #fff4f4; font-size: 11px; }
```
**注意 `font-size: 11px` 对应 `--text-sm`，`color: #bd3535` 与 token
`--color-danger-text: #c2354a` 不同**——先量出 23 处里有没有哪处依赖了具体色值
（比如测试断言），再决定是原样保留 `#bd3535` 还是收敛到 token。**收敛属于颜色约束要求的，
但要在报告里给出色差数值。**

- [ ] **Step 2: 先写测试（TDD）**

`ErrorBanner.test.tsx`：
```tsx
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { ErrorBanner } from "./ErrorBanner";

afterEach(cleanup);

test("渲染为 role=alert，读屏能立刻播报", () => {
  render(<ErrorBanner>删除失败</ErrorBanner>);
  expect(screen.getByRole("alert")).toHaveTextContent("删除失败");
});

test("外部 className 能追加而不覆盖基类", () => {
  render(<ErrorBanner className="mt-4">出错了</ErrorBanner>);
  const el = screen.getByRole("alert");
  expect(el.className).toContain("mt-4");
  expect(el.className).toMatch(/border/);
});
```

- [ ] **Step 3: 跑测试确认失败**

```bash
npx vitest run src/components/ui/ErrorBanner.test.tsx
```
预期 FAIL：`Failed to resolve import "./ErrorBanner"`。

- [ ] **Step 4: 实现**

```tsx
import type { ReactNode } from "react";
import { cn } from "./cn";

/**
 * 错误横幅。
 *
 * 收敛掉散在 16 个文件里的 23 处 `.error-banner`——它们此前共用一条 legacy 规则，
 * 任何一处想调间距都得改全局。
 *
 * 固定带 `role="alert"`：错误是要打断当前任务的信息，读屏必须立刻播报，
 * 不能等用户 tab 到这里才知道。
 */
export function ErrorBanner({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "mb-3.5 rounded-[7px] border border-danger-line bg-danger-soft px-[13px] py-[11px] text-sm text-danger-text",
        className,
      )}
    >
      {children}
    </div>
  );
}
```
**上面的 token 名（`danger-line`/`danger-soft`）是示意——先读 `tailwind.css` 的 `@theme`
确认实际有哪些 danger 系 token，没有对应的就用 `[#f0cccc]` 这类任意值并在报告里说明。
不要凭名字猜 token 存在**（此前出过「把死变量修成另一个死变量」的问题，
所有 token 必须 `grep tailwind.css` 确认有定义）。

- [ ] **Step 5: 跑测试确认通过**

- [ ] **Step 6: 替换 23 处**

逐个文件替换。注意有些地方可能是 `<div className="error-banner">`，有些可能带额外类。
**替换后原地的 `role="alert"` 若重复要删掉**（`ErrorBanner` 已自带）。

- [ ] **Step 7: 删 `.error-banner` 规则并复核**

```bash
grep -rn 'error-banner' frontend/src --include="*.tsx"
```
应当 0 命中（注释除外）再删 `styles.css:15`。

- [ ] **Step 8: 全套验证 + 视觉基线**

基线会变（色值若收敛）。差异**逐处指认到元素**并附 A/B 数值。

- [ ] **Step 9: 提交**

---

## Task 4: 收口其余 11 种活 class

**Files:**
- Modify: `frontend/src/styles.css`
- Modify: 相关组件（自己 grep 确定）

**Interfaces:**
- Consumes: Task 3 的 `ui/ErrorBanner` 已存在（本任务不再动 error-banner）
- Produces: legacy 层只剩元素级全局规则，供 Task 6 搬迁

处理 `empty-copy`(15) `sr-only`(11) `section-kicker`(7) `modal-form`(4) `product-page`(4)
`admin-page`(2) `confirm-copy`(2) `metadata-form`(2) `governance-table`(1) `member-avatar`(1)
`template-apply-option`(1)。**`pulse` 留给 Task 6**（它要搬进 `@theme`）。

- [ ] **Step 1: `sr-only` 直接删规则**

Tailwind utilities 层已产出 `sr-only`（已验证 dist 命中）。legacy 层在 utilities 之前，
所以删掉 legacy 的那条不会让 11 处失效。

**但要实测验证**，别只信推理：删掉后写临时 spec 量一处 `sr-only` 元素的
`getBoundingClientRect()`（应当是 1×1）与 `clip`/`position`。

- [ ] **Step 2: `product-page` / `admin-page` —— 这两个最危险**

它们是**页面容器**，规则散在 4 处（`styles.css:11,35,71,81` 及 `:20,:75` 的响应式），
级联后的真实值需要逐档算：

| 视口 | `product-page` padding | 来源 |
| --- | --- | --- |
| ≥1025px | `20px 20px 40px` | `:81` |
| 561–1024px | `26px 24px 52px` | `:35` |
| ≤560px | `20px 14px 44px` | `:20` |

`admin-page` 另有 `:75` 的 `≤767px → 20px 14px 36px`。
**`max-width` 也要注意**：`:11` 是 1360px、`:35` 覆盖成 1440px、`:71` 的 admin 是 1440px。

用 `git show <迁移前 commit>:frontend/src/styles.css` 核对，**报告给出每档四方向数字**。
改错会波及**全部 17 张基线**。

- [ ] **Step 3: `governance-table` 的死变量**

`styles.css:97,103,111` 用了 `var(--border)` 和 `var(--text-muted)`，**两者全库从未定义**
（已核实）。所以 `border-bottom: 1px solid var(--border)` 整条失效、
`color: var(--text-muted)` 整条失效。

**Ruling（控制器已定）：让从未生效的声明按作者意图生效算修复，不算夹带设计变更**——
与 `DataTable.tsx:13` 的既有判例一致（那里把「`--border` 从未定义、声明全部失效」
列为它一次性收掉的缺陷）。所以改写时用 `--color-line` / `--color-ink-faint`，
**并在报告里给出改前改后的实测颜色值**。

- [ ] **Step 4: 其余 8 种就地改 utility**

`empty-copy`/`section-kicker` 有**两条同名选择器**（`:14` 与 `:26`、`:13` 与 `:25`），
后者覆盖前者。**级联后的真实值是 `:26`/`:25` 的那组**（`color:#737c90; font-size:13px`
与 `font-size:12px; letter-spacing:.04em`），不是第一条。
阶段 3 有个任务因为只看第一条而量错过 `.empty-copy` 的字号。

- [ ] **Step 5: 全套验证**

- [ ] **Step 6: 视觉基线**

`product-page`/`admin-page` 改动会波及全部 17 张。**逐张指认到元素**并附 A/B 数值。

- [ ] **Step 7: 提交**

---

## Task 5: 新建 `ui/ListItemButton` 并消 9 条 lint

**Files:**
- Create: `frontend/src/components/ui/ListItemButton.tsx`
- Create: `frontend/src/components/ui/ListItemButton.test.tsx`
- Modify: `AppNavigation.tsx`、`ChatPage.tsx`、`KnowledgeBaseDetailPage.tsx`、`OverviewPage.tsx`、`ParsingPanel.tsx`

**Interfaces:**
- Consumes: `cn`
- Produces: `<ListItemButton active={bool} onClick={fn}>` —— 列表项/卡片式可点区域

这是 spec 第一笔债。当前 9 条 `no-restricted-syntax` 按钮警告的位置：
`AppNavigation:41`、`ChatPage:29,107`、`KnowledgeBaseDetailPage:116`、
`OverviewPage:46,59,64`、`ParsingPanel:125,153`（行号会漂，自己 grep）。

它们都是**列表项或卡片式按钮**，不是 `ui/Button` 的场景——`Button` 保证「禁用必有可见
原因」并渲染 ⓘ + Tooltip，套到导航项/卡片上会带来不该有的胶囊样式与 ⓘ 图标。
此前审查已判定「这些不套 `ui/Button`」**成立**（有仓库先例）。

- [ ] **Step 1: 读那 9 处的实际结构**

它们形态不同（导航项带图标+分组、Tab 带计数徽章、历史项带两行文字、卡片带描述）。
**先判断一个组件能不能同时装下这 9 处**。装不下就缩小范围（比如只覆盖「导航项 + Tab +
历史项」这类单行可点项），**在报告里写明哪几处没覆盖及理由**。
不要为了消 lint 硬套出一个四不像组件。

- [ ] **Step 2: ⚠️ 原生 `<button>` 必须显式写 `border-0` 和 `bg-*`**

preflight 尚未启用（Task 6 才开），UA 默认 `border: 2px outset` + `background-color: ButtonFace`
仍生效。Task 4（问答工作台）在这里栽了两个 Critical，实测：
`border-top` `0px none` → **`2px outset rgb(0,0,0)`**、背景 → **`rgb(239,239,239)`**。

新组件的基类**必须包含 `border-0` 和显式 `bg-*`**。
house standard 参考 `KnowledgeBaseDetailPage.tsx:116`、`OverviewPage.tsx:59`。

- [ ] **Step 3: 先写测试**

至少覆盖：`active` 为 true 时带 `aria-current`；基类含 `border-0`；外部 className 可追加。

- [ ] **Step 4: 跑测试确认失败 → 实现 → 跑测试确认通过**

- [ ] **Step 5: 替换那 9 处（或你确定能覆盖的子集）**

- [ ] **Step 6: 验证 lint 条数下降**

```bash
npm run lint 2>&1 | grep -E '^\s+[0-9]+:[0-9]+'
```
**列出新增/消失的具体行号**，不要只报总数（此前出过两次「净数持平掩盖新增」和
「把原有的报成新增」）。

- [ ] **Step 7: 真实浏览器复测 border/background**

读 `borderTopWidth`/`borderTopStyle`/`backgroundColor`。不接受「代码里写了所以没问题」。

- [ ] **Step 8: 视觉基线 + 提交**

---

## Task 6: 搬迁全局规则并启用 preflight

**Files:**
- Modify: `frontend/src/tailwind.css`
- Modify: `frontend/src/styles.css`（清空或删除）
- Modify: 视觉基线快照

**Interfaces:**
- Consumes: Task 1/3/4/5 完成后 legacy 层只剩元素级规则
- Produces: 单一样式来源

**这是最高风险的一步，必须在 Task 1/3/4/5 全部完成后做。**

legacy 层剩余的元素级规则（行号会漂，自己核）：

| 规则 | preflight 是否提供 | 处理 |
| --- | --- | --- |
| `* { box-sizing: border-box }` | **是** | 删 |
| `button, textarea, select, input { font: inherit }` | **是** | 删 |
| `button { cursor: pointer }` | v4 preflight **不**加 `cursor: pointer` | **搬到 `@layer base`** |
| `:root` 字体族 / color / background | 否 | 搬到 `@theme` + `@layer base` |
| `body { margin:0; min-width:320px; min-height:100vh }` | margin 是，其余否 | 部分搬迁 |
| `:focus-visible { outline: 2px solid #6356d9 }` | 否 | 搬到 `@layer base`，颜色走 token |
| `@keyframes spin` / `@keyframes pulse` + `.pulse` | 否 | 搬到 `@theme` 的 `--animate-*` |
| `prefers-reduced-motion` 块 | 否 | 搬到 `@layer base` |
| `:root` 的 8 个 CSS 变量（`--brand` `--ink` `--line` 等） | 否 | 与 `@theme` 已有 token 对齐后删 |

- [ ] **Step 1: 逐条确认 preflight 到底提供什么**

**读 `frontend/node_modules/tailwindcss/preflight.css` 全文**，不要凭记忆。
v4 的 reset 是 `*, ::after, ::before, ::backdrop, ::file-selector-button { box-sizing;
margin: 0; padding: 0; border: 0 solid }` 这种统一写法，**与 v3 的 `blockquote,dd,dl,figure`
逐元素写法不同**（控制器探测时用 v3 选择器验证过一次，得到假的「未生效」结论）。

在报告里逐条列出：哪条 legacy 规则被 preflight 覆盖（可删）、哪条没有（必须搬迁）。

- [ ] **Step 2: 先搬迁，暂不开 preflight**

把上表中「搬迁」的规则写进 `tailwind.css` 的 `@theme` / `@layer base`。
**此时跑一遍全套验证 + 基线，应当 17 张全绿**——搬迁不改变任何计算值，
只是换了声明位置。红了说明搬错了。

**这一步单独提交**，与开 preflight 分开，便于二分定位。

- [ ] **Step 3: 清空 styles.css 并开 preflight**

删掉 `styles.css` 全部内容（或删文件并去掉 `tailwind.css:10` 的 import），
把 `tailwind.css` 里被注释掉的 preflight import 启用：
```css
@import "tailwindcss/preflight.css" layer(base);
```
**同时改写 `tailwind.css:14-26` 那段过时注释**——它记录的 2026-08-30 探测结论
（「17 张全红、评测中心腰斩、整体散架」）已经不成立。用本计划开头那张实测表替换，
并说明当时的前置条件（`.chat-layout`/`.member-row`/`.management-table`/`.evidence-panel`
改写）现已全部满足。

- [ ] **Step 4: 全套验证**

`npm test` 在探测中已验证 preflight 下 180/180 全绿。若这次不绿，说明 Task 1–5 引入了
新的 UA 依赖，**回到对应任务修，不要在这里打补丁**。

- [ ] **Step 5: 视觉基线逐张归因**

探测数据（Task 1–5 完成前）显示 6 张高度变化 >50px，破坏点集中在弹层与 kb-detail 表格类
Tab。Task 3/4 收口 `modal-form`/`governance-table`/`metadata-form` 之后这些应当已经消除。

**逐张指认到元素**。方法（控制器在 `5587055` 用过，可复用）：
解码 diff PNG 统计纯红 `(255,0,0)` 像素的**坐标分布**（侧栏边界 138px），按 y 分 50px 桶
定位，异常张裁剪 actual/expected 对应区域比对内容。

**不接受整体归因。** 每一张的差异都要说清是「预期的质感变化」「数据漂移（附具体数据差异，
如 case id / 会话条数）」还是「回归（必须修）」。

- [ ] **Step 6: 接受基线并提交**

---

## Task 7: 补基线覆盖缺口

**Files:**
- Modify: `frontend/e2e/visual-baseline.spec.ts`
- Create: 新快照

**Interfaces:**
- Consumes: Task 6 完成后的稳定样式
- Produces: 覆盖登录页与移动端的基线

当前基线有**两个结构性缺口**：

**1. 登录页完全不在覆盖内。** `visual-baseline.spec.ts` 的脚本第一步就登录，所以 17 张里
没有一张是登录页。Task 5（外壳）的 Critical 1（登录页背景整条声明失效、径向光晕消失）
**就是因此溜过去的**——没有任何自动化能发现它。

**2. 移动端一张快照都没有。** 只有 `desktop-chromium` project。Task 4（问答工作台）
因此漏了 6 处 ≤900px 的响应式行为丢失。
且 CI 配置矛盾：`pytest.yml` 跑 `npm run test:e2e` 不指定 project，会把 `mobile-chromium`
跑进去，而它无快照可比。

- [ ] **Step 1: 加登录页快照**

未登录首屏 + `bootstrapRequired` 分支（首次建管理员）。
后者需要构造状态——用 `page.route()` 拦截 bootstrap 接口响应，**不要清空真实后端 auth store**。

- [ ] **Step 2: 决定移动端怎么办（二选一，在报告里给理由）**

- **补 mobile 基线**：给 `mobile-chromium` project 拍全套快照。代价：快照数翻倍、
  数据漂移导致的误报也翻倍。
- **CI 显式排除**：`pytest.yml` 里给 `npm run test:e2e` 加 `--project=desktop-chromium`，
  并在 `playwright.config.ts` 或 README 写明 mobile project 是本地手动工具。

**推荐后者**：基线「绑定数据集、不进 CI」是这个仓库既定的定位（CLAUDE.md 第八条），
移动端快照会把维护成本翻倍而收益有限；但**当前「配置说要跑、实际无快照可比」的中间态
必须消除**——它正是 CLAUDE.md 第五条说的「没有 CI 覆盖的东西会静默腐烂」。

- [ ] **Step 3: 更新 CLAUDE.md 第八条**

它现在写「覆盖 11 个页面状态」，实际是 17（Task 7 后更多）。
同时把「登录页此前未覆盖导致 Critical 溜过」这个实例补进去——
CLAUDE.md 开头明确要求「只写这个仓库真实踩过的坑，每条都附实例」。

- [ ] **Step 4: 提交**

---

## Task 8: 结清 B 组零散债

**Files:** 按各项确定

四项独立小债，可在一个 commit 里做完：

- [ ] **Step 1: 边框族颜色收敛**

`ChatPage`/`AnswerPanel`/`SourceCard` 残留 `#eef0f5`/`#eef0f6`（与 `--color-divider: #edf0f5`
差 1）、`#e5e8f0`/`#e3e6ef`/`#e3e6f0`/`#e1e5ef`（与 `--color-line: #e5e9f2` 个位数差内）。

Task 4 以「无对应 token」为由保留，**审查判定该理由不成立**：跨度大得多的 9 个灰
（`#7e879a`…`#a5abba`）都能收敛成一个 `ink-faint`，这批边框以此为由保留自相矛盾。

收敛到 token。**给出色差数值**，基线会有可见但预期的微小变化。

- [ ] **Step 2: `bg-[linear-gradient(...)]` 不重置 `background-color`**

选中态历史项（`ChatPage`）实测 `backgroundColor` 是 `rgb(239,239,239)`（UA buttonface），
靠不透明渐变盖住。原 CSS 的 `background:` shorthand 会把 `background-color` 一并重置为
transparent，而 `bg-[linear-gradient(...)]` 只生成 `background-image`。
**当前无视觉差异，但渐变一旦改半透明就露灰底。**

补显式 `bg-transparent` 或改用 `bg-[image:...]` + 显式底色。
（Task 6 开 preflight 后 UA buttonface 消失，此项可能自动解决——**先验证再决定是否还需改**。）

- [ ] **Step 3: 复核 Radix 的 `hideOthers` 分支**

阶段 1 的债。`aria-hidden/dist/es2015/index.js:133` 的 `hideOthers()` 在调用那一刻做 DOM
快照，而 toast 是按需渲染的——弹层打开时 toast 容器可能空无一物。
阶段 3 已把 `aria-live` 挂到常驻容器上修好了主路径（`Toast.tsx:70`），
**但 Radix 走 `hideOthers` 的其它分支没复核过**。

查 `Dialog.tsx` 用的 Radix 版本在什么条件下调 `hideOthers`，确认常驻容器方案在所有分支下
都成立。**核不了的写「未核」并说明卡在哪。**

- [ ] **Step 4: 核实 6 条 lint 是合理保留还是漏迁**

Task 6（阶段 4 验收）发现剩余 15 条 `no-restricted-syntax` 里，9 条按钮属 ListItemButton
场景（Task 5 处理），但另外 6 条不属于：

- **表格 4 条**：`AcceptancePage:135`、`EvaluationCenterPage:175,229`、
  `KnowledgeBaseDetailPage:114`。此前审查判定过「拒绝套用 `DataTable` 成立」
  （带阈值进度条等结构装不下）——**确认这 4 条都属于那个判定，还是有漏迁的。**
- **复选框 2 条**：`KnowledgeBaseDataSourcesPanel:222`、`KnowledgeBasesPage:191`。
  `ui/Checkbox` 存在且注释说明了 indeterminate 的必要性，**为何这两处仍用原生 checkbox
  从未查证过。**

读 `ui/DataTable.tsx` 和 `ui/Checkbox.tsx` 的全部 props，再看那 6 处的实际结构。
**能套就套，装不下就在报告里给出具体理由**（读完组件源码 + grep 同语义先例之后才下结论——
此前出过两次「没查全就说装不下」）。

- [ ] **Step 5: 全套验证 + 基线 + 提交**

---

## Task 9: 阶段验收

**Files:** 无（只验证与记录）

- [ ] **Step 1: 全套验证**

```bash
export PATH="$HOME/.nvm/versions/node/v20.20.2/bin:$PATH"
cd frontend && npm test && npm run lint && npm run typecheck && npm run build
```

- [ ] **Step 2: 量化收尾指标**

| 指标 | 会话起点 | 阶段 4 末 | 目标 |
| --- | --- | --- | --- |
| `styles.css` | 515 行 / 2629 声明 | 117 行 | **0（文件删除或空）** |
| legacy className 引用 | ~350 处 | 75 处 / 13 种 | **0** |
| preflight | 未启用 | 未启用 | **已启用** |
| `no-restricted-syntax` | — | 15 条 | ≤6 条且每条有书面理由 |
| 视觉基线 | 11 张 | 17 张 | 含登录页 |
| 测试 | — | 180 / 24 files | 不减 |

- [ ] **Step 3: 确认 preflight 真的生效**

不要只看 import 在不在。**读构建产物验证**：
```bash
npm run build && grep -c 'file-selector-button' dist/assets/*.css
```
`::file-selector-button` 与 `::backdrop` 是 v4 preflight 的独有选择器。
（控制器探测时用 v3 的 `blockquote,dd,dl,figure` 验证，得到过假的「未生效」结论。）

- [ ] **Step 4: 确认 `styles.css` 真的没有使用者**

若保留了空文件，确认 `tailwind.css` 的 `@import ... layer(legacy)` 也已删除，
且 `@layer` 声明里的 `legacy` 已清理。

- [ ] **Step 5: 更新 spec 与 CLAUDE.md**

- spec `docs/superpowers/specs/2026-08-31-frontend-ui-overhaul-design.md` 的四笔债标记完成状态
- CLAUDE.md 第七条「前端」小节：preflight 已启用这件事会改变后续所有样式工作的前提，
  **必须记进去**（附 `max-[Npx]:` 那条 1px 陷阱的实例——它符合「只写真实踩过的坑」的要求）

- [ ] **Step 6: 写阶段总结进 ledger**
