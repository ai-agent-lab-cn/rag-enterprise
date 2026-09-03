# 列表页收口（阶段 2）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把五张列表页迁到阶段 1 建成的基座上，让「增删改查交互、页面展示、列表样式」三层问题第一次真正落到用户看得见的页面。

**Architecture:** 先补两块阶段 1 缺的基础设施（组合测试、`Button` 的 Tooltip 化、`RowActions` 的文件操作），再逐页收口。每迁完一个页面，删掉它专属的 legacy CSS 规则，跑一次视觉基线看差异——差异必须能被逐条解释，不能直接 `--update-snapshots` 糊过去。

**Tech Stack:** React 19 + TypeScript + Tailwind v4 + cva + `radix-ui@1.6.7` + vitest/jsdom + @testing-library/react + Playwright

**Spec:** `docs/superpowers/specs/2026-08-31-frontend-ui-overhaul-design.md`

**前序阶段:** `docs/superpowers/plans/2026-08-31-ui-foundation-stage1.md`（已完成，28 commit，基座 18 个组件）

## Global Constraints

- **Node 版本下限 `^20.19.0 || >=22.12.0`**，本机默认 node 是 **v20.18.0，不满足**。实测后果：`npm test` 输出 `Test Files no tests / Errors 8 errors` 而**退出码仍是 0**。**每一条 npm/npx 命令都必须前置：**
  ```bash
  export PATH="$HOME/.nvm/versions/node/v20.20.2/bin:$PATH"
  ```
  **每份实现报告必须贴出 `node --version` 的实际输出**，没有 `v20.20.2` 的，测试结论一律不作数。
- **提交后必须先跑 `git log --oneline -1` 和 `git status --porcelain` 读到真实 hash 再写进报告**。阶段 1 出过两次实现者填了 git 里不存在的 hash、改动其实还在工作区。
- **类型检查只认 `npm run typecheck`（`tsc -b`）**，`npx tsc --noEmit` 在这个仓库是空跑的。
- **preflight 仍然不能启用**，浏览器给 `<button>` 的默认 `border: 1px outset` 还在；渲染 `<button>` 的地方必须显式 `border-0`。
- **字阶不改**：`xs 10 / sm 11 / base 12 / md 13 / lg 15 / xl 20`，正文基准 12px（等于左侧菜单项字号）。
- **颜色只用于表意**。装饰色收敛为中性，表格主链接 `text-ink font-medium`、hover 才变紫。
- **`pattern` 属性必须在 `v` flag 下合法**：写 `[A-Za-z0-9._\-]+`，不要写 `[A-Za-z0-9._-]+`。`App.test.tsx` 有一条测试扫描全部 `pattern`。
- **视觉基线的凭据**：`SMOKE_ADMIN_USERNAME=demo`、`SMOKE_ADMIN_PASSWORD=DemoBaseline2026!`（阶段 1 重置过）。基线已在本机数据集上重拍，17 张为当前参照系。
- **基线脚本会向真实环境写数据**（曾把 reader 误改成管理员）。跑之前确认环境可以被写。
- **真实浏览器验证用 `npx playwright test`，不要用 Playwright MCP 工具。** 这台机器没装
  Google Chrome，而 MCP 工具固定 `channel: "chrome"`，会报
  `Chromium distribution 'chrome' is not found at /Applications/Google Chrome.app`。
  项目自带的 `@playwright/test` 用的是 `~/Library/Caches/ms-playwright` 里的 chromium，
  一直可用（视觉基线就是这么跑的）。

  做法：在 `frontend/e2e/` 下写一个临时 spec（文件名以 `__tmp-` 开头便于识别），跑
  ```bash
  export PATH="$HOME/.nvm/versions/node/v20.20.2/bin:$PATH"
  cd frontend && SMOKE_ADMIN_USERNAME=demo SMOKE_ADMIN_PASSWORD='DemoBaseline2026!' \
    npx playwright test __tmp-你的文件名 --project=desktop-chromium --reporter=list
  ```
  **跑完删掉临时 spec 和 `test-results/`。**

  两个已验证的技巧：
  - 用 `page.route()` 拦截请求来构造失败场景（`route.fulfill({ status: 500, ... })`），
    比真的搞坏后端干净得多。
  - **令牌只存页面内存，刷新即失效**，所以页面间跳转必须走页面内导航（点菜单），
    不能用 `page.goto()`。这是 `CLAUDE.md` 第七条记的坑，已经绊倒过一个 agent 的验证脚本。
- 每个任务结束时 `npm test && npm run lint && npm run typecheck && npm run build` 必须全绿。
- 提交信息用中文，结尾附 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`。

---

## 阶段 1 遗留、本阶段必须处理的项

这些是阶段 1 终审记录在 ledger 里的延后项，本阶段轮到它们：

| 项 | 落在哪个任务 |
| --- | --- |
| `Button` 的 `blockedReason` 从下方小字改为 Tooltip + `ⓘ`，删掉 `reasonHidden` | Task 2 |
| `RowActions` 平铺形态的可见小字撑破 `DataTable` 行高（Task 2 的强制前置关系） | Task 2 |
| 零组合测试——23 个测试文件全是单组件隔离渲染 | Task 1 |
| `DataTable` 给每个 `<td>` 无条件加 `truncate`，操作列与「名称+徽章」列会被裁掉 | Task 1 发现、Task 3 修 |
| `RowActions` 阈值 3 未兑现「列宽恒定」（知识库表 1/2/3 个操作都有） | Task 4 用真实数据验证 |
| `className` 逃生口只开给一半组件 | 各迁移任务按需补 |

## 本计划的两处设计裁定

**裁定 A：`AuditPage` 从卡片列表改为 `DataTable` + `density="compact"`。**
它现在是 `<article>` + `<dl>` 的卡片列表，展示 5 个固定字段（发生时间、操作者、资源、请求 ID、事件哈希）。固定字段的卡片列表本来就该是表格；`density="compact"` 当初就是为这一页设计的（行数可达数千，实测页面高度 11866px）。「保留信息架构」指页面呈现什么内容不变，不是 DOM 形态不能动。
代价：这一页的视觉变化最大，且它**有意不在视觉基线覆盖范围内**（审计事件只增不减），迁完需要人工过目。

**裁定 B：`RowActions` 增加文件操作能力。**
`DataSourcesPage` 的操作列有「更新文件」，它是 `FileButton`（要触发文件选择器），而 `RowAction` 只有 `onSelect: () => void`，表达不了。给 `RowAction` 加可选的 `file?: { accept: string; onSelect: (files: File[]) => void }`，有它就渲染 `FileButton` 而非 `Button`。
代价：`RowActions` 的 API 变宽；替代方案（文件操作不走 `RowActions`）会破坏「行操作唯一出口」这个约束，那个代价更大。

---

## 文件结构

**新建：**

| 文件 | 职责 |
| --- | --- |
| `frontend/src/components/ui/ListPage.test.tsx` | 组合测试：把 Toolbar + DataTable + RowActions + Pagination 拼成真实列表页形状，锁住组件间的契约 |

**修改：**

| 文件 | 改动 |
| --- | --- |
| `ui/Button.tsx` + `.test.tsx` | `blockedReason` 渲染改为 Tooltip + `ⓘ`；删 `reasonHidden` |
| `ui/Select.tsx` + `.test.tsx` | 同步删 `reasonHidden` 语义（它没有这个 prop，但可见小字要与 Button 对齐） |
| `ui/FileButton.tsx` + `.test.tsx` | 同步 Button 的渲染方式 |
| `ui/Pagination.tsx` | 去掉 `reasonHidden` 用法 |
| `ui/RowActions.tsx` + `.test.tsx` | 支持文件操作（裁定 B）；去掉平铺形态的 `reasonHidden` |
| `ui/DataTable.tsx` + `.test.tsx` | `Column<T>` 增加 `truncate?: boolean` 逃生口，默认 true |
| `components/KnowledgeBasesPage.tsx` | 迁移 |
| `components/MembersPage.tsx` | 迁移（最大，`div[role=table]` 整个删掉） |
| `components/DocumentPanel.tsx` | 迁移 |
| `components/AuditPage.tsx` | 迁移（裁定 A） |
| `components/DataSourcesPage.tsx` | 迁移 |
| `App.tsx` | 挂 `ToastProvider`；删掉硬编码的「新建知识库」按钮分支，统一走 `TopbarPortal` |
| `src/styles.css` | 每迁完一页删它专属的 legacy 规则 |

---

### Task 1: 组合测试——把基座拼成真实列表页

**Files:**
- Create: `frontend/src/components/ui/ListPage.test.tsx`

**Interfaces:**
- Consumes: `Toolbar`、`DataTable`、`RowActions`、`Pagination`、`EmptyState`、`Badge`
- Produces: 一组锁住组件间契约的断言。后续每个迁移任务出问题时，先看这里有没有对应的断言；没有就补。

**为什么它必须是第一个任务：** 阶段 1 的 23 个测试文件全是单组件隔离渲染，**零组合测试**。终审因此漏掉了两处错配（`DataTable` 空态丢容器——已修；`truncate` 把操作列裁掉——本任务发现、Task 3 修）。不先补，阶段 2 会继续漏，而那时问题会混在页面改动里，更难定位。这是 `CLAUDE.md` 第三条「写入路径和读取路径必须成对验证」的同类：组件单测是「写入」，拼起来能不能用是「读取」。

- [ ] **Step 1: 写组合测试**

`frontend/src/components/ui/ListPage.test.tsx`：

```tsx
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Badge } from "./Badge";
import { DataTable, type Column } from "./DataTable";
import { Pagination } from "./Pagination";
import { RowActions } from "./RowActions";
import { Toolbar } from "./Toolbar";

afterEach(cleanup);

/**
 * 组合测试。
 *
 * 阶段 1 的每个组件都单独测过且全绿，但它们拼起来是否还成立，此前没有任何测试覆盖——
 * 终审因此漏掉了两处错配。这个文件的断言全部指向「A 组件产出喂给 B 组件」的接缝，
 * 不重复单组件已经测过的东西。
 */

type Row = { id: string; name: string; kind: string; count: number; blocked?: string };

const ROWS: Row[] = [
  { id: "a", name: "企业知识库", kind: "独立知识库", count: 5 },
  { id: "b", name: "默认知识库", kind: "默认知识库", count: 0, blocked: "默认知识库不能删除" },
];

function listPageColumns(onOpen: (id: string) => void): Column<Row>[] {
  return [
    {
      key: "name",
      header: "知识库名称",
      width: "240px",
      render: (row) => (
        <span className="flex items-center gap-2">
          <button onClick={() => onOpen(row.id)}>{row.name}</button>
          <Badge shape="type">{row.kind}</Badge>
        </span>
      ),
    },
    { key: "count", header: "文档数量", numeric: true, render: (row) => row.count },
    {
      key: "actions",
      header: "操作",
      align: "right",
      width: "160px",
      render: (row) => (
        <RowActions
          rowLabel={row.name}
          actions={[
            { label: "详情", onSelect: () => onOpen(row.id) },
            { label: "编辑", onSelect: () => {} },
            { label: "删除", onSelect: () => {}, tone: "destructive", blockedReason: row.blocked },
          ]}
        />
      ),
    },
  ];
}

const EMPTY = { kind: "empty", title: "还没有知识库", description: "创建一个后即可上传资料。" } as const;

test("名称列里的徽章不被 truncate 裁掉", () => {
  // DataTable 给每个 td 无条件加了 truncate（overflow:hidden + nowrap）。
  // 「名称 + 类型徽章」这种组合列因此会被裁——而修复这个截断正是 spec 点名的问题之一。
  // 这条断言锁住：名称列必须能同时容纳两者，不能靠调用方自己想办法。
  render(
    <DataTable rows={ROWS} columns={listPageColumns(() => {})} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  const cell = screen.getByText("企业知识库").closest("td")!;
  expect(within(cell).getByText("独立知识库")).toBeVisible();
  expect(cell.className.split(/\s+/)).not.toContain("truncate");
});

test("操作列不被 truncate 裁掉", () => {
  render(
    <DataTable rows={ROWS} columns={listPageColumns(() => {})} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  const trigger = screen.getByRole("button", { name: "企业知识库 的更多操作" });
  expect(trigger.closest("td")!.className.split(/\s+/)).not.toContain("truncate");
});

test("行内 RowActions 不撑破统一行高", () => {
  // 阶段 1 把平铺形态的禁用原因改成了可见小字，那会多占一行、撑破 h-14。
  // Task 2 改成 Tooltip + ⓘ 之后这条才成立，它是那个改动的守卫。
  render(
    <DataTable rows={ROWS} columns={listPageColumns(() => {})} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  const rows = screen.getAllByRole("row").slice(1);
  for (const row of rows) {
    expect(row.className).toMatch(/\bh-14\b/);
  }
  // 有禁用原因的那一行也不例外——原因必须以不占据行高的方式呈现。
  const blockedRow = screen.getByText("默认知识库").closest("tr")!;
  expect(blockedRow.className).toMatch(/\bh-14\b/);
});

test("工具栏的批量选中数与表格的选择状态是同一个来源", () => {
  const onChange = vi.fn();
  const selected = ["a"];
  render(
    <div>
      <Toolbar batch={{ count: selected.length, children: <button>删除 {selected.length} 项</button> }} />
      <DataTable
        rows={ROWS}
        columns={listPageColumns(() => {})}
        rowKey={(row) => row.id}
        emptyState={EMPTY}
        label="知识库列表"
        selection={{ selected, onChange, rowLabel: (row) => row.name }}
      />
    </div>,
  );

  // 页面把同一个 selected 分别喂给 Toolbar 和 DataTable，两者必须显示一致。
  expect(screen.getByRole("status")).toHaveTextContent("已选 1 项");
  expect(screen.getByRole("checkbox", { name: "选择 企业知识库" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "选择全部" })).toHaveAttribute("data-state", "indeterminate");
});

test("空态时分页不出现", () => {
  // 列表页的惯常写法是三个组件并排渲染。空态下还挂一个「第 1 页」是噪音，
  // Pagination 自己会在只有一页时返回 null——这条锁住那个行为在组合场景下也成立。
  render(
    <div>
      <DataTable rows={[]} columns={listPageColumns(() => {})} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />
      <Pagination page={0} hasNext={false} onChange={() => {}} label="知识库分页" />
    </div>,
  );

  expect(screen.getByRole("heading", { name: "还没有知识库" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation")).toBeNull();
});

test("加载态与空态的外框一致", () => {
  // 阶段 1 修过一次「空态丢容器」。这条防止它复发，并把断言放在组合层面：
  // 用户看到的是同一张卡片在三种状态间切换，边框不该忽有忽无。
  const { rerender, container } = render(
    <DataTable rows={null} columns={listPageColumns(() => {})} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );
  const loadingBox = container.firstElementChild!.className;

  rerender(
    <DataTable rows={[]} columns={listPageColumns(() => {})} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );
  const emptyBox = container.firstElementChild!.className;

  rerender(
    <DataTable rows={ROWS} columns={listPageColumns(() => {})} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );
  const dataBox = container.firstElementChild!.className;

  expect(emptyBox).toBe(loadingBox);
  expect(dataBox).toBe(loadingBox);
});
```

- [ ] **Step 2: 跑它，记录哪些失败**

```bash
export PATH="$HOME/.nvm/versions/node/v20.20.2/bin:$PATH"
cd frontend && npx vitest run src/components/ui/ListPage.test.tsx
```

**预期不是全绿。** 「名称列里的徽章不被 truncate 裁掉」「操作列不被 truncate 裁掉」两条应当失败——`DataTable.tsx` 目前给每个 `<td>` 无条件加 `truncate`。「行内 RowActions 不撑破统一行高」也可能失败（取决于 jsdom 是否反映行高，若它在 jsdom 下恒绿，在报告里注明这条是「真实浏览器才验证得了」）。

**把每条的实际结果原样贴进报告**，不要在本任务里修 `DataTable`——那是 Task 3 的事。失败的测试用 `test.fail` 或 `test.skip` 标记并注明「Task 3 修复后取消标记」，让本任务能提交。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/ui/ListPage.test.tsx
git commit -m "$(cat <<'EOF'
test: 补组合测试，锁住基座组件之间的接缝

阶段 1 的 23 个测试文件全是单组件隔离渲染，零组合测试，终审因此漏掉两处
错配。这个文件的断言全部指向「A 组件产出喂给 B 组件」的接缝：truncate 与
组合列/操作列的冲突、RowActions 与固定行高的冲突、Toolbar 与 DataTable
的选中状态一致性、三种状态的外框一致性。

其中两条当前是失败的（truncate 裁掉组合列与操作列），已标记待 Task 3 修复。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 禁用原因改为 Tooltip + `ⓘ`，废除 `reasonHidden`

**Files:**
- Modify: `ui/Button.tsx` + `ui/Button.test.tsx`
- Modify: `ui/FileButton.tsx` + `ui/FileButton.test.tsx`
- Modify: `ui/Select.tsx` + `ui/Select.test.tsx`
- Modify: `ui/Pagination.tsx`（去掉 `reasonHidden` 用法）
- Modify: `ui/RowActions.tsx`（平铺形态跟随新渲染方式）
- Modify: 受影响的 8 个页面调用点（**仅删除 `reasonHidden` 这个 prop，不做其它改动**）

**Interfaces:**
- Consumes: `ui/Tooltip.tsx`（阶段 1 建好但至今零消费者）、`normalizeBlockedReason`
- Produces: 禁用原因的统一呈现方式。后续所有迁移任务依赖它不再占据行高。

**这是阶段 2 的第一个实质任务，也是 `RowActions` 进表格的强制前置。** 阶段 1 把 `RowActions` 平铺形态的原因从「只在 `title`」改成了「可见小字」（修掉了违反 `CLAUDE.md` 第一条的问题），但可见小字多占一行，会撑破 `DataTable` 的 `h-14`。两个约束的共同解就是本任务。

**`ⓘ` 必须是独立的可聚焦元素，不能把 `Tooltip` 直接包在禁用的 `Button` 外面。** 真实浏览器里 `disabled` 的 `<button>` 不派发 `pointerenter`，而 jsdom 会——在 jsdom 里测「包住禁用按钮的 Tooltip」会绿着骗人，浏览器里永远弹不出来。这一条是本任务最容易出错的地方。

- [ ] **Step 1: 先写测试**

在 `Button.test.tsx` 中，**删掉**「reasonHidden 只藏可见小字」那条（该 prop 即将不存在），**改写**「给了原因才禁用，且原因可见」，并新增：

```tsx
test("禁用原因由独立的 ⓘ 承载，不占据行高", async () => {
  render(<Button blockedReason="默认知识库不能删除">删除</Button>);

  const action = screen.getByRole("button", { name: "删除" });
  expect(action).toBeDisabled();

  // 原因不再是按钮下方的块级小字——那会把表格行撑高。
  expect(screen.queryByText("默认知识库不能删除")).toBeNull();

  // 取而代之的是一个独立的、**可用的** ⓘ。它必须自己可聚焦：
  // 真实浏览器里 disabled 的 button 不派发 pointerenter，把 Tooltip 包在
  // 禁用按钮外面在 jsdom 里会绿，在浏览器里永远弹不出来。
  const hint = screen.getByRole("button", { name: /默认知识库不能删除/ });
  expect(hint).not.toBe(action);
  expect(hint).toBeEnabled();
});

test("ⓘ 悬停后弹出原因", async () => {
  render(<Button blockedReason="默认知识库不能删除">删除</Button>);

  await userEvent.hover(screen.getByRole("button", { name: /默认知识库不能删除/ }));
  const shown = await screen.findAllByText("默认知识库不能删除");
  expect(shown.length).toBeGreaterThan(0);
});

test("多个原因合并进同一个 ⓘ", async () => {
  render(<Button blockedReason={["请先勾选资料", "请先选择目标分类"]}>应用到 0 份</Button>);

  const hint = screen.getByRole("button", { name: /请先勾选资料、请先选择目标分类/ });
  await userEvent.hover(hint);
  expect((await screen.findAllByText(/请先勾选资料、请先选择目标分类/)).length).toBeGreaterThan(0);
});

test("没有原因时不渲染 ⓘ", () => {
  render(<Button>删除</Button>);

  expect(screen.getAllByRole("button")).toHaveLength(1);
});

test("loading 期间不渲染 ⓘ", () => {
  // loading 是短暂状态，组件自己用 title 解释，不必多挂一个图标。
  render(<Button loading>保存</Button>);

  expect(screen.getAllByRole("button")).toHaveLength(1);
});
```

`Select.test.tsx` 与 `FileButton.test.tsx` 做同样的改写——三者必须行为一致，否则又出现「同样的条件，一个控件这样解释、另一个那样解释」。

- [ ] **Step 2: 跑测试确认失败**

```bash
export PATH="$HOME/.nvm/versions/node/v20.20.2/bin:$PATH"
cd frontend && npx vitest run src/components/ui/Button.test.tsx
```

- [ ] **Step 3: 改 Button**

`Button.tsx` 的返回部分改为：

```tsx
  return (
    <>
      <button
        type="button"
        {...rest}
        className={cn(button({ variant, size }), className)}
        disabled={blocked || loading}
        aria-busy={loading || undefined}
        title={title}
      >
        {children}
      </button>
      {/* 原因由独立的 ⓘ 承载，不再是块级小字——小字会把表格行撑高，
          而消灭行高不一致正是 DataTable 存在的理由之一。
          ⓘ 自己是可用的按钮：真实浏览器里 disabled 的 button 不派发
          pointerenter，把 Tooltip 包在禁用按钮外面在 jsdom 里会绿，
          在浏览器里永远弹不出来。 */}
      {!loading && blocked ? (
        <Tooltip content={reasons.join("、")} delay={0}>
          <button
            type="button"
            aria-label={`为什么不可用：${reasons.join("、")}`}
            className="inline-grid h-4 w-4 shrink-0 place-items-center rounded-full border-0 bg-transparent p-0 text-ink-faint hover:text-ink-muted focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand/20"
          >
            <Info size={13} />
          </button>
        </Tooltip>
      ) : null}
    </>
  );
```

顶部 import 加 `import { Info } from "lucide-react";` 和 `import { Tooltip } from "./Tooltip";`。

**删掉 `reasonHidden` 这个 prop 及其类型定义和相关注释。**

`FileButton.tsx` 与 `Select.tsx` 做同样处理。

- [ ] **Step 4: 删掉 8 个调用点的 `reasonHidden`**

```bash
grep -rn "reasonHidden" frontend/src --include="*.tsx"
```

逐个删除该 prop。**只删这个 prop，不要顺手改别的**——这些页面的迁移是后续任务，本任务碰它们只是为了让编译通过。

- [ ] **Step 5: 验证并跑基线**

```bash
export PATH="$HOME/.nvm/versions/node/v20.20.2/bin:$PATH"
cd frontend && npm test && npm run lint && npm run typecheck && npm run build
SMOKE_ADMIN_USERNAME=demo SMOKE_ADMIN_PASSWORD='DemoBaseline2026!' \
  npx playwright test visual-baseline --project=desktop-chromium
```

**基线会红，这是预期的**——禁用原因从块级小字变成同行的 `ⓘ`，凡是有禁用按钮的页面布局都会变。**逐张确认变化符合预期后再 `--update-snapshots`**，并在报告里逐张说明「这张变了什么、为什么符合预期」。有任何一张的变化解释不了，停下来报告，不要更新基线。

- [ ] **Step 6: 提交**（含更新后的基线）

---

### Task 3: 补齐基座的两处缺口

**Files:**
- Modify: `ui/DataTable.tsx` + `ui/DataTable.test.tsx`
- Modify: `ui/RowActions.tsx` + `ui/RowActions.test.tsx`
- Modify: `ui/ListPage.test.tsx`（取消 Task 1 标记的失败测试）

**Interfaces:**
- Produces:
  ```tsx
  // DataTable
  export type Column<T> = {
    // ...原有字段
    /** 是否单行截断。默认 true；组合内容（名称+徽章）与操作列必须设为 false。 */
    truncate?: boolean;
  };

  // RowActions
  export type RowAction = {
    label: string;
    onSelect?: () => void;
    /** 给了就渲染 FileButton 而非 Button。与 onSelect 二选一。 */
    file?: { accept: string; onSelect: (files: File[]) => void };
    tone?: "default" | "destructive";
    blockedReason?: string | string[];
  };
  ```

**缺口 1（Task 1 会实测暴露）：** `DataTable.tsx` 给每个 `<td>` 无条件加 `truncate`。「名称 + 类型徽章」这种组合列会被裁掉，而修复这个截断正是 spec 点名的问题；操作列里的按钮也会被裁。

**缺口 2（裁定 B）：** `DataSourcesPage` 的操作列有「更新文件」，是 `FileButton`，而 `RowAction` 只有 `onSelect: () => void`，表达不了文件操作。

- [ ] **Step 1: 写测试**（`truncate` 默认 true 但可关；`file` 类型的 action 在平铺与菜单两种形态下都能触发文件选择）
- [ ] **Step 2: 跑它确认失败**
- [ ] **Step 3: 实现两处改动**
- [ ] **Step 4: 取消 `ListPage.test.tsx` 里 Task 1 标记的失败测试，确认它们现在真的绿**
- [ ] **Step 5: 全量验证 + 提交**

---

### 页面迁移任务的共同要求（Task 4-8 都适用）

顺序固定：**Task 4 `KnowledgeBasesPage`**（已完成）→ **Task 5 `MembersPage`** → **Task 6 `DocumentPanel`** → **Task 7 `AuditPage`** → **Task 8 `DataSourcesPage`**。

先做 `KnowledgeBasesPage` 是因为它已经是最接近目标形态的一个（有真 `<table>`、有搜索筛选排序分页），迁移路径最短，能最快暴露基座的问题。`MembersPage` 排第二是因为它最大且形态差最远（`div[role=table]` + CSS grid 要整个删掉）。

**每个迁移任务的固定动作：**

1. **迁移前先记录当前行为**：把这个页面现有的每一个交互（按什么按钮、发生什么、什么条件下禁用、禁用原因是什么、成功后如何反馈）列成清单写进报告。迁移后逐条对照——**这是防止「迁移顺手丢功能」的唯一手段**。
2. 用 `DataTable` 替换表格/伪表格，列定义写清 `width`、`numeric`、`align`、`truncate`。
3. 行操作全部走 `RowActions`。
4. 工具栏走 `Toolbar`，分页走 `Pagination`，空态走 `DataTable` 的 `emptyState`（区分 `empty` 与 `filtered`）。
5. 状态/类型徽章走 `Badge`（状态用 `shape="status"`，类型用 `shape="type"`）。
6. 删除确认走 `useConfirm`，**`consequence` 必须写清后果**，不能只说「确认吗」。
7. 写操作成功/失败走 `useToast`——现在全站写操作完成后是静默刷新。
8. **清理 legacy CSS——但先查清楚它是不是独占的。**

   实测结果（Task 4 派发前调查）：列表页用到的 8 个主要 class **没有一个是单页独占的**：

   | class | 被这些文件用 |
   | --- | --- |
   | `management-toolbar` | DataSourcesPage、KnowledgeBasesPage |
   | `management-table` | DataSourcesPage、KnowledgeBaseDataSourcesPanel、KnowledgeBasesPage |
   | `management-table-wrap` | 同上三个 |
   | `management-pagination` | DataSourcesPage、KnowledgeBasesPage |
   | `table-primary-link` | KnowledgeBasesPage、DataSourcesPage |
   | `truncate-cell` | CategoryTemplateModal、DataSourcesPage、KnowledgeBasesPage、KnowledgeBaseDataSourcesPanel |
   | `base-type-tag` | DataSourcesPage、KnowledgeBasesPage、KnowledgeBaseDetailPage、KnowledgeBaseDataSourcesPanel |
   | `status-tag` | DocumentPanel、DataSourcesPage、KnowledgeBasesPage、KnowledgeBaseDetailPage、KnowledgeBaseDataSourcesPanel、ParsingPanel |

   其中 `KnowledgeBaseDataSourcesPanel` 和 `KnowledgeBaseDetailPage` 属于**阶段 3**，
   `ParsingPanel` 也是。所以这些规则要到阶段 3 结束才可能全部删净。

   **每个迁移任务的正确做法：** 迁完后对该页用过的每个 class 跑一次
   `grep -rn "class-name" frontend/src --include="*.tsx"`，**只有返回空**才删；
   否则在报告里列出「还有谁在用」，留给最后一个使用者删。
   **删之前必须 grep，凭印象删会让别的页面静默失去样式**——而那种失去是渐进的
   （只在特定状态下才可见），视觉基线未必覆盖得到。

   另外注意 `styles.css:273` 的 `.management-pagination button { height: 32px; ... }`
   是标签选择器，会咬迁移后 `Pagination` 组件里的 `Button`——和 Task 2 遇到的
   `.question-footer button` 同类。迁移后该页不再有 `.management-pagination` 容器，
   所以不会中招，但**如果你保留了那个容器 class 就会**。
9. 跑视觉基线，**逐张解释差异**后再更新。
10. 在真实浏览器里走一遍该页的增删改查——`CLAUDE.md` 第九条：组件测试通过不等于页面能用，这个仓库出现过「reclassify 把状态改回 pending 却不入队，用户点了按钮什么也不会发生」。

**每个任务必须验证的具体行为**（迁移时最容易丢的）：

| 页面 | 不能丢的行为 |
| --- | --- |
| `KnowledgeBasesPage` | 搜索防抖 250ms；`deleteBlockReason()` 的兜底文案；删除弹层区分「默认库不能删」「有资料需先清空」「可以删」三态；「去清空资料」跳转 |
| `MembersPage` | 「不能修改自己的账号」；管理员不显示授权开关（不是禁用，是不渲染）；撤销授权走确认、授予不走；弹层打开时错误必须显示在弹层内（否则躺在 Radix 的 `aria-hidden` 背景里） |
| `DocumentPanel` | 拖拽上传；上传进度；批量归类的两个禁用条件；`reclassify` 的行级 loading（不能整页 loading）；分类列只放真实分类名，状态单独一行 |
| `AuditPage` | 筛选即时生效（改筛选先清空再拉）；`pattern="[a-z][a-z0-9_.\-]+"` 的 `v` flag 合法性；哈希与请求 ID 的 `title` 全文 |
| `DataSourcesPage` | **索引进行中的 1 秒轮询**（`setInterval`，`hasActiveIndexing` 驱动）；更新文件必须同名校验；`allowed_actions` 决定按钮显隐 |

**Task 8 额外的强制项（Task 3 结转，不做就是留了个会静默腐烂的洞）：**

`RowActions` 菜单形态里的文件操作，依赖「Radix 的 `onSelect` 经 `flushSync` 同步派发，使
`input.click()` 与原生 click 处于同一调用栈，用户手势不丢失」。这个依赖**当前没有任何
可重跑的检查覆盖**：jsdom 不模拟用户激活限制，`input.click()` 无论在哪调用都会成功，
所以现有 vitest 用例只能验证「input 没被卸载」，验证不了「手势没丢」。Task 3 当时是用
一次性 Playwright 探针验证的，脚本已删除。

后果具体化：若某次 Radix 升级把派发改成微任务/宏任务，vitest 仍然全绿，而真实浏览器里
点「更新文件」会**静默无反应**——正是 `CLAUDE.md` 第九条记的那种「点了按钮什么也不会发生」。

所以 Task 8 迁完 `DataSourcesPage` 后必须在 `frontend/e2e/` 下补一条真实浏览器测试：
点开某个数据源的 `⋯` 菜单 → 点「更新文件」→ 断言 `filechooser` 事件真的触发。
这时页面上已有真实的菜单形态文件操作，不必再造探针。

---

---

### Task 5: MembersPage 迁移

**Files:** `components/MembersPage.tsx`（280 行，五个页面里最大）、`src/styles.css`

**这一页形态差得最远，也是唯一能真正删掉 legacy CSS 的一页。**

它的「表格」是 `div[role="table"]` + CSS grid 假冒的（`MembersPage.tsx:168`），要**整个删掉**换 `DataTable`。这正是「列表有两套实现」这个根因的另一半。

**legacy class 归属（派发前实测）：**

| 独占，迁完可删 | 共享，不能删 |
| --- | --- |
| `member-table`、`member-row`、`member-identity`、`member-avatar`、`role-badge`、`row-actions`、`permission-toolbar` | `status-pill`（AuditPage、SystemPage 也用）、`admin-page`（AuditPage、PermissionDeniedPage、SystemPage）、`admin-state`（AuditPage、PermissionDeniedPage）、`admin-loading`（AuditPage、SystemPage） |

删之前仍要各自 `grep -rn "class-name" frontend/src --include="*.tsx"` 复核一遍，只有返回空才删。

**不能丢的行为：**

- **「不能修改自己的账号」** —— `isSelf` 时两个操作按钮禁用。阶段 2 Task 2 已把禁用原因改成 Tooltip + `ⓘ`，这页迁完后要确认 `ⓘ` 真的出现且说得出原因（这是 `CLAUDE.md` 第一条点名的原始案例之一）。
- **管理员不显示授权开关，而不是禁用它** —— `MembersPage.tsx:200` 的注释写明了理由：「管理员天然有全部权限，给一个永远点不动的开关只是噪音」。迁移后**不要**把它变成禁用的开关。
- **撤销授权走确认弹层，授予不走** —— 不对称是有意的（撤销是破坏性的）。
- **弹层打开时错误必须显示在弹层内**（`MembersPage.tsx:131` 的 `error && !creating && !confirm`）—— 否则错误躺在 Radix 加了 `aria-hidden` 的背景里，屏幕阅读器读不到。
- 新建成员表单四个字段的校验：用户名 `pattern="[A-Za-z0-9._\-]+"`（**注意 `v` flag 下 `-` 必须转义**，`App.test.tsx` 有测试扫描全部 pattern）、密码 `minLength={12}`。
- 授权知识库下拉在没有知识库时的 `blockedReason`。

**额外要求：** 这页的写操作（创建成员、改角色、停用/启用、授权/撤权）现在**全部是静默的**，迁移后每个都要有 toast。

---

### Task 6: DocumentPanel 迁移

**Files:** `components/DocumentPanel.tsx`、`src/styles.css`

**不能丢的行为：**

- 拖拽上传（`onDragOver`/`onDragLeave`/`onDrop`）与上传进度（`uploadProgress`）
- **批量归类的两个禁用条件**：没勾资料 / 没选目标分类。Task 2 已让 `blockedReason` 支持数组，这里要**两个原因都列出来**——迁移前它只说得出第一个，这是最初点名要修的问题之一
- **`reclassify` 的行级 loading**（`retrying` 数组），不能退化成整页 loading：「重新分类只影响这一份资料，整页 Loading 会让其他行也变得不可用」
- **分类列只放真实分类名，状态单独一行**（`DocumentPanel.tsx:122` 的注释：「把「待分类」显示成分类，正是这次要消灭的那种混淆」）
- checkbox 必须独立成列（`DataTable` 的 `selection` 已保证），不能再塞进文件名那格
- 删除确认**不要 `autoFocus`**（原 `:133` 有，回车直接删）
- 编辑元数据弹层里分类下拉的 `option` 用 name 作 value 的既有行为

**批量操作区** 要用 `Toolbar` 的 `batch`——**有选中项才出现**。迁移前那三个控件常驻一排、没勾选时全是死的。

---

### Task 7: AuditPage 迁移

**Files:** `components/AuditPage.tsx`、`src/styles.css`

**这一页要改形态**（裁定 A）：从 `<article>` + `<dl>` 卡片列表改为 `DataTable` + `density="compact"`。

**它有意不在视觉基线覆盖范围内**（审计事件只增不减，实测页面高度已到 11866px），所以**没有自动化守护，迁完必须人工过目截图**。

**不能丢的行为：**

- 筛选即时生效：改筛选先 `setEvents(null)` 再拉（当前的 `changeAction`/`changeResult`）
- `pattern="[a-z][a-z0-9_.\-]+"` —— `-` 的转义不能丢
- 请求 ID 与事件哈希的 `title` 全文（列里只显示前 10/16 字符）
- 放大镜图标的绝对定位（`AuditPage.tsx:58-60` 的注释解释了为什么这么做）

**列定义建议**：操作（含 `ACTION_LABELS` 映射 + 原始 action code）、结果（`Badge`）、发生时间、操作者、资源、请求 ID、事件哈希。**这页没有分页**，行数可能很多——`density="compact"` 就是为它准备的。

---

### Task 8: DataSourcesPage 迁移

**Files:** `components/DataSourcesPage.tsx`、`src/styles.css`

**不能丢的行为：**

- **索引进行中的 1 秒轮询**（`setInterval`，由 `hasActiveIndexing` 驱动）—— 丢了它索引状态就不会自动刷新
- 更新文件的**同名校验**（`file.name !== item.name` 时报错，避免意外创建新数据源）
- `allowed_actions` 决定四个操作（更新文件/停用/启用/删除）的显隐
- `index-loading` 的旋转动画与 `aria-busy`

**这页是 `RowAction.file` 的第一个真实消费者**（Task 3 为它加的）。「更新文件」要走 `RowActions` 的 `file` 分支。

**Task 3 结转的强制项：** 迁完后在 `frontend/e2e/` 补一条真实浏览器测试——点开某数据源的 `⋯` → 点「更新文件」→ 断言 `filechooser` 事件真的触发。理由见本文件前面「Task 8 额外的强制项」那节：这个行为依赖 Radix 同步派发 `onSelect`，而 jsdom 测不出手势丢失，Task 3 用的一次性探针已删除。

---

### Task 9: 阶段验收

- [ ] 全量 `npm test && npm run lint && npm run typecheck && npm run build`
- [ ] `grep -c "reasonHidden" frontend/src` 为 0
- [ ] `npm run lint` 的裸元素 warning 数量应显著下降（阶段 1 是 35 个），把实际数字与阶段 1 对比写进报告
- [ ] 五个页面的 legacy CSS 已删，`styles.css` 行数与阶段 1 对比
- [ ] 视觉基线全部更新且每张变化都有书面解释
- [ ] 在真实浏览器里把五个页面的增删改查各走一遍，截图存档
