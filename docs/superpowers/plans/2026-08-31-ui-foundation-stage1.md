# UI 基座（阶段 1）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 13 个基座组件并把交互规则编码进类型，为后续四个阶段的逐页迁移提供唯一的组件来源。

**Architecture:** 全部写在 `frontend/src/components/ui/`，沿用现有 5 个组件的路子——cva 定义 variant、`@theme` token 提供取值、`cn()` 做冲突消解、规则写进 TypeScript 类型而不是文档。Radix primitive 统一从聚合包 `radix-ui` 导入。本阶段**不修改任何页面组件**，也不改变现有组件的渲染行为，因此视觉基线应当 17 张全绿——那是基座无副作用的唯一证据。

**Tech Stack:** React 19 + TypeScript + Tailwind v4（`@theme` token）+ cva + tailwind-merge + `radix-ui@1.6.7` + vitest/jsdom + @testing-library/react

**Spec:** `docs/superpowers/specs/2026-08-31-frontend-ui-overhaul-design.md`

## Global Constraints

- **Node 版本下限 `^20.19.0 || >=22.12.0`**，这台机器的默认 node 是 **v20.18.0，不满足**。实测后果：`npm test` 输出 `Test Files no tests / Errors 8 errors`，**退出码仍是 0**（`ERR_REQUIRE_ESM`——`html-encoding-sniffer` 用 `require()` 加载 ESM，20.19 以下不支持 `require(esm)`）。

  **因此本计划里每一条 `npm` 命令都必须前置这个 export，一次都不能省：**

  ```bash
  export PATH="$HOME/.nvm/versions/node/v20.20.2/bin:$PATH"
  ```

  实测 v20.20.2 下 8 个测试文件 76 个测试全绿。**每份实现报告必须贴出 `node --version` 的实际输出**——报告里没有 `v20.20.2` 的，其测试结论一律不作数。
- **类型检查只认 `npm run typecheck`（`tsc -b`）**。`npx tsc --noEmit` 是空跑的——根 `tsconfig.json` 是 `files: []` + project references，给它一个赤裸的类型错误退出码仍是 0。
- **`pattern` 属性必须在 `v` flag 下合法**：写 `[A-Za-z0-9._\-]+`，不要写 `[A-Za-z0-9._-]+`。`App.test.tsx` 有一条测试扫描全部 `pattern` 属性。
- **preflight 仍然不能启用**。浏览器给 `<button>` 的默认 `border: 1px outset` 还在，任何渲染 `<button>` 的新组件基础样式必须显式声明 `border-0`；需要边框的 variant 自己声明 `border`，靠 tailwind-merge 让后者胜出。
- **字阶不改**：`xs 10 / sm 11 / base 12 / md 13 / lg 15 / xl 20`，正文基准 12px。
- **颜色只用于表意**。新组件不得引入装饰性色彩；状态用 `success`/`warning`/`danger`，其余一律中性。
- **本阶段不改任何 `components/*.tsx` 页面文件**，也不改现有 `ui/` 组件的渲染输出。唯一例外是 `Button` 的 `blockedReason` 类型放宽（纯新增，单字符串行为逐字节不变）。
- 每个任务结束时 `cd frontend && npm test && npm run lint && npm run typecheck` 必须全绿。
- 提交信息用中文，结尾附 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`。

---

## 文件结构

**新建**（全部在 `frontend/src/components/ui/`）：

| 文件 | 职责 |
| --- | --- |
| `Tooltip.tsx` | Radix Tooltip 包装。全站唯一的悬浮提示来源，`Provider` 由它自己内联，调用方不必包 |
| `Badge.tsx` | 徽章。`shape` 区分状态（胶囊）与类型（方角），`tone` 决定语义色 |
| `Checkbox.tsx` | Radix Checkbox 包装，支持 `indeterminate` |
| `Skeleton.tsx` | 骨架屏占位块 |
| `EmptyState.tsx` | 空态。区分「没数据」与「筛选无结果」 |
| `Pagination.tsx` | 分页。从 `KnowledgeBasesPage.tsx:44` 抽出的形态 |
| `DropdownMenu.tsx` | Radix DropdownMenu 包装 |
| `RowActions.tsx` | 行操作唯一出口。≤2 平铺、≥3 收进 `⋯` |
| `DataTable.tsx` | 泛型表格。`emptyState` 必填 |
| `Toolbar.tsx` | 列表页工具栏骨架 |
| `MetricCard.tsx` | 指标卡。数值 `tabular-nums` |
| `Tabs.tsx` | Radix Tabs 包装 |
| `Toast.tsx` | 自建轻量 toast，含 `ToastProvider` 与 `useToast` |
| `useConfirm.tsx` | 确认弹层 hook，强制传 `consequence` |
| 对应的 `*.test.tsx` | 每个组件一份 |

**修改：**

- `frontend/package.json` — 依赖切换
- `frontend/src/components/ui/Dialog.tsx:1` — import 来源改为聚合包
- `frontend/src/components/ui/Button.tsx:58,81,96` — `blockedReason` 接受 `string | string[]`
- `frontend/src/test-setup.ts` — 补 jsdom 缺失的 `ResizeObserver` 与 pointer capture API
- `frontend/eslint.config.js` — 加裸元素禁令（`warn` 级）

**不新增 `@theme` token。** 现有 token 已覆盖本阶段所需：中性图标底用 `--color-canvas`，分隔线用 `--color-divider`，行高 `h-14`/`h-11` 是 Tailwind 内置刻度。

---

### Task 1: 依赖切换到 radix-ui 聚合包

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/src/components/ui/Dialog.tsx:1`

**Interfaces:**
- Consumes: 无
- Produces: `import { Dialog as RadixDialog, Tooltip, DropdownMenu, Checkbox, Tabs } from "radix-ui"` 这一导入形态，后续所有 Radix 包装组件都用它

- [ ] **Step 1: 装聚合包、卸单包**

```bash
cd frontend
npm install radix-ui@1.6.7
npm uninstall @radix-ui/react-dialog
```

- [ ] **Step 2: 改 Dialog 的 import**

`frontend/src/components/ui/Dialog.tsx` 第 1 行：

```tsx
import { Dialog as RadixDialog } from "radix-ui";
```

替换掉原来的 `import * as RadixDialog from "@radix-ui/react-dialog";`。文件其余部分一律不动——聚合包导出的是同一套 primitive，`RadixDialog.Root` / `.Portal` / `.Overlay` / `.Content` / `.Title` / `.Description` / `.Close` 全部同名。

- [ ] **Step 3: 跑现有测试确认零回归**

```bash
cd frontend && npm test && npm run typecheck && npm run build
```

Expected: 全绿。`Dialog.test.tsx` 的 89 行断言不该有任何变化——这一步只换了 import 来源。

- [ ] **Step 4: 确认 package.json 依赖条目净增 0**

```bash
cd frontend && node -e "const d=require('./package.json').dependencies; console.log(Object.keys(d).filter(k=>k.includes('radix')))"
```

Expected: `[ 'radix-ui' ]` —— 只有一个，`@radix-ui/react-dialog` 已消失。

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/components/ui/Dialog.tsx
git commit -m "$(cat <<'EOF'
refactor: Radix 依赖切换到 radix-ui 聚合包

后续基座要用 Tooltip/DropdownMenu/Checkbox/Tabs 四个 primitive，逐个装包会让依赖条目
从 1 涨到 5。聚合包含 55 个 primitive 且 dist 下逐个分文件，能 tree-shake，依赖条目净增 0。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 补齐 jsdom 缺失的浏览器 API

**Files:**
- Modify: `frontend/src/test-setup.ts`

**Interfaces:**
- Consumes: 无
- Produces: 测试环境具备 `ResizeObserver`、`Element.prototype.hasPointerCapture`、`setPointerCapture`、`releasePointerCapture`、`DOMRect`——Task 3 起的每个 Radix 包装组件测试都依赖它们

**为什么单独成一个任务：** 这不是可选的加固。Radix 的 Popper（Tooltip、DropdownMenu 共用）在挂载时就调 `ResizeObserver`，jsdom 没有这个构造函数，测试会抛 `ReferenceError` 而不是断言失败——失败信息完全指不到真正的原因。先把它补上，后面 5 个 Radix 组件的测试才可能跑起来。

- [ ] **Step 1: 写一个会因缺 API 而失败的探针测试**

新建 `frontend/src/test-setup.test.ts`：

```ts
import { expect, test } from "vitest";

test("jsdom 具备 Radix Popper 依赖的浏览器 API", () => {
  // Radix 的 Tooltip / DropdownMenu 共用 Popper，它在挂载时就构造 ResizeObserver。
  // jsdom 不实现这些，缺了会抛 ReferenceError / TypeError，而不是干净的断言失败——
  // 报错信息指向 Radix 内部，排查要花很久。这条测试让缺失变成一句人话。
  expect(typeof globalThis.ResizeObserver).toBe("function");
  expect(typeof Element.prototype.hasPointerCapture).toBe("function");
  expect(typeof Element.prototype.setPointerCapture).toBe("function");
  expect(typeof Element.prototype.releasePointerCapture).toBe("function");
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/test-setup.test.ts`
Expected: FAIL —— `expected "undefined" to be "function"`

- [ ] **Step 3: 在 test-setup.ts 里补上**

追加到 `frontend/src/test-setup.ts` 末尾：

```ts
// Radix 的 Popper（Tooltip / DropdownMenu 共用）挂载时就构造 ResizeObserver，
// jsdom 不实现它。补一个空实现即可：测试断言的是 DOM 结构和可访问性属性，
// 不是浮层的实际坐标，而坐标计算正是 ResizeObserver 唯一参与的部分。
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// Radix 的菜单类组件用 pointer capture 处理「按下后拖到菜单项再松开」的手势。
// jsdom 的 Element 没有这三个方法，userEvent 触发 pointerdown 时会抛 TypeError。
Element.prototype.hasPointerCapture = () => false;
Element.prototype.setPointerCapture = () => {};
Element.prototype.releasePointerCapture = () => {};
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/test-setup.test.ts && npm test`
Expected: 探针测试 PASS，其余测试无回归。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/test-setup.ts frontend/src/test-setup.test.ts
git commit -m "$(cat <<'EOF'
test: 补齐 jsdom 缺失的 ResizeObserver 与 pointer capture

Radix Popper 挂载即构造 ResizeObserver，菜单类组件用 pointer capture 处理拖拽手势。
缺了它们，Tooltip/DropdownMenu 的测试会抛 ReferenceError 而不是断言失败，
报错指向 Radix 内部、排查成本很高。探针测试让缺失变成一句人话。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Tooltip

**Files:**
- Create: `frontend/src/components/ui/Tooltip.tsx`
- Test: `frontend/src/components/ui/Tooltip.test.tsx`

**Interfaces:**
- Consumes: Task 1 的聚合包导入形态、Task 2 的 jsdom API
- Produces:
  ```tsx
  export function Tooltip(props: {
    content: ReactNode;
    children: ReactNode;
    /** 默认 200ms；0 表示立即显示，用于禁用原因这类必须马上看到的场景 */
    delay?: number;
    side?: "top" | "right" | "bottom" | "left";
  }): JSX.Element
  ```
  阶段 2 的 Button 改造、`RowActions`、`DataTable` 的截断单元格都消费它

- [ ] **Step 1: 写失败的测试**

`frontend/src/components/ui/Tooltip.test.tsx`：

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";
import { Tooltip } from "./Tooltip";

afterEach(cleanup);

test("默认不显示内容，悬停后才出现", async () => {
  render(
    <Tooltip content="默认知识库不能删除">
      <button>删除</button>
    </Tooltip>,
  );

  expect(screen.queryByText("默认知识库不能删除")).toBeNull();

  await userEvent.hover(screen.getByRole("button", { name: "删除" }));
  // Radix 把内容渲染进 portal，且同时存在一份 aria-live 副本，所以用 findAllByText。
  const shown = await screen.findAllByText("默认知识库不能删除");
  expect(shown.length).toBeGreaterThan(0);
});

test("触发元素带 aria-describedby，屏幕阅读器读得到原因", async () => {
  render(
    <Tooltip content="请先选择知识库">
      <button>授权</button>
    </Tooltip>,
  );

  const trigger = screen.getByRole("button", { name: "授权" });
  await userEvent.hover(trigger);
  await screen.findAllByText("请先选择知识库");
  // 这是这个组件存在的核心理由：把原因绑到触发元素上，而不是只画一个浮层。
  // CLAUDE.md 第一条禁止「只有 title」，aria-describedby 是无障碍这一路的补齐。
  expect(trigger).toHaveAttribute("aria-describedby");
});

test("Provider 由组件自己内联，调用方不必包一层", () => {
  // Radix 要求 Tooltip.Root 必须在 Tooltip.Provider 之内，否则运行时报错。
  // 把 Provider 收进组件，调用方就不会因为忘了包而在生产环境炸——那种错误
  // 只在真正悬停时才触发，单元测试和构建都发现不了。
  expect(() =>
    render(
      <Tooltip content="提示">
        <button>触发</button>
      </Tooltip>,
    ),
  ).not.toThrow();
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/Tooltip.test.tsx`
Expected: FAIL —— `Failed to resolve import "./Tooltip"`

- [ ] **Step 3: 实现**

`frontend/src/components/ui/Tooltip.tsx`：

```tsx
import { Tooltip as RadixTooltip } from "radix-ui";
import type { ReactNode } from "react";

/**
 * 统一悬浮提示。
 *
 * **Provider 内联在组件里，不要求调用方包一层。** Radix 的规范是把 Provider 放在应用
 * 根部共享，但那样每个调用点都要记得「根部已经有了」——忘了包的后果是运行时报错，
 * 且只在真正悬停时才触发，单元测试和构建都发现不了。内联的代价是多几个 Provider 实例，
 * 它们无状态，代价可以忽略。
 *
 * 它承载两类内容：禁用原因（`delay={0}`，必须马上看到）和截断文本的全名（默认延迟）。
 */
export function Tooltip({
  content,
  children,
  delay = 200,
  side = "top",
}: {
  content: ReactNode;
  children: ReactNode;
  /** 默认 200ms；0 表示立即显示，用于禁用原因这类必须马上看到的场景。 */
  delay?: number;
  side?: "top" | "right" | "bottom" | "left";
}) {
  return (
    <RadixTooltip.Provider delayDuration={delay}>
      <RadixTooltip.Root>
        {/* asChild：把触发行为合并到子元素上，不额外套一层 span——套了会破坏
            flex/grid 布局，也会让 Button 的 w-fit 之类的 className 失效。 */}
        <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content
            side={side}
            sideOffset={6}
            className="z-50 max-w-64 rounded-sm bg-ink px-2 py-1 text-sm text-white shadow-pop"
          >
            {content}
            <RadixTooltip.Arrow className="fill-ink" />
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  );
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/Tooltip.test.tsx && npm test && npm run typecheck`
Expected: 3 条全 PASS，其余测试无回归。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Tooltip.tsx frontend/src/components/ui/Tooltip.test.tsx
git commit -m "$(cat <<'EOF'
feat: 基座新增 Tooltip

承载禁用原因与截断文本全名。Provider 内联在组件内，避免调用方忘记包一层——
那种错误只在真正悬停时触发，单测和构建都发现不了。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Badge

**Files:**
- Create: `frontend/src/components/ui/Badge.tsx`
- Test: `frontend/src/components/ui/Badge.test.tsx`

**Interfaces:**
- Consumes: `cn` from `./cn`
- Produces:
  ```tsx
  export function Badge(props: {
    children: ReactNode;
    tone?: "neutral" | "success" | "warning" | "danger" | "brand";
    shape?: "status" | "type";
    className?: string;
  }): JSX.Element
  ```
  `DataTable` 的状态列、知识库/成员/文档三张表的徽章都消费它

**背景：** 现在有三套徽章样式——`.status-tag`（状态，浅底胶囊）、`.base-type-tag`（类型，另一套）、`.status-pill`（成员状态，又一套），外加成员页「未授权」的白底描边按钮。收敛成一个组件，用 **shape 区分语义类别**：状态是会变的（可用→处理中→失败），画成圆角胶囊；类型是固有属性（默认知识库/独立知识库），画成方角标签。形状携带信息，颜色只表意。

- [ ] **Step 1: 写失败的测试**

`frontend/src/components/ui/Badge.test.tsx`：

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { Badge } from "./Badge";

afterEach(cleanup);

test("状态与类型用不同形状，从轮廓就能区分语义", () => {
  render(
    <div>
      <Badge shape="status" tone="success">可用</Badge>
      <Badge shape="type">独立知识库</Badge>
    </div>,
  );

  // 状态会变（可用→处理中→失败），画成胶囊；类型是固有属性，画成方角标签。
  // 这是这个组件唯一的结构性决定：形状携带信息，不只是好看。
  expect(screen.getByText("可用").className).toContain("rounded-full");
  expect(screen.getByText("独立知识库").className.split(/\s+/)).toContain("rounded-sm");
});

test("五个 tone 各有底色，neutral 不带任何语义色", () => {
  render(
    <div>
      <Badge tone="neutral">中性</Badge>
      <Badge tone="success">成功</Badge>
      <Badge tone="warning">警告</Badge>
      <Badge tone="danger">危险</Badge>
      <Badge tone="brand">品牌</Badge>
    </div>,
  );

  // 查的是渲染出来的中文，不是 tone 的英文名——getByText 匹配的是文本节点。
  const classOf = (text: string) => screen.getByText(text).className.split(/\s+/);
  expect(classOf("成功")).toContain("bg-success-subtle");
  expect(classOf("危险")).toContain("bg-danger-subtle");
  expect(classOf("品牌")).toContain("bg-brand-subtle");
  // 中性不得带语义色——大多数徽章都是中性的，让它去抢语义色会稀释真正的告警。
  for (const cls of classOf("中性")) {
    expect(cls).not.toMatch(/^bg-(success|danger|brand|warning)/);
  }
});

test("默认是 neutral 状态胶囊", () => {
  render(<Badge>默认</Badge>);

  const cls = screen.getByText("默认").className.split(/\s+/);
  expect(cls).toContain("rounded-full");
  expect(cls).toContain("bg-canvas");
});

test("外部 className 能覆盖内部同类样式", () => {
  render(<Badge className="bg-transparent">自定义</Badge>);

  const cls = screen.getByText("自定义").className.split(/\s+/);
  expect(cls).toContain("bg-transparent");
  expect(cls).not.toContain("bg-canvas");
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/Badge.test.tsx`
Expected: FAIL —— `Failed to resolve import "./Badge"`

- [ ] **Step 3: 实现**

`frontend/src/components/ui/Badge.tsx`：

```tsx
import { cva, type VariantProps } from "class-variance-authority";
import type { ReactNode } from "react";
import { cn } from "./cn";

/**
 * 统一徽章。收敛掉三套并行实现：`.status-tag`、`.base-type-tag`、`.status-pill`，
 * 外加成员页那个用按钮冒充徽章的「未授权」。
 *
 * **shape 区分语义类别，不是装饰选项。** 状态是会变的（可用→处理中→失败），画成
 * 圆角胶囊；类型是固有属性（默认知识库/独立知识库），画成方角标签。用户不需要读文字
 * 就能知道哪个是「现在怎么样」、哪个是「它是什么」。
 */
const badge = cva(
  "inline-flex items-center gap-1 whitespace-nowrap px-1.5 py-0.5 text-sm font-medium",
  {
    variants: {
      tone: {
        neutral: "bg-canvas text-ink-muted",
        success: "bg-success-subtle text-success",
        warning: "bg-warning/10 text-warning",
        danger: "bg-danger-subtle text-danger-text",
        brand: "bg-brand-subtle text-brand",
      },
      shape: {
        status: "rounded-full",
        type: "rounded-sm",
      },
    },
    defaultVariants: { tone: "neutral", shape: "status" },
  },
);

export type BadgeProps = VariantProps<typeof badge> & {
  children: ReactNode;
  className?: string;
};

export function Badge({ tone, shape, className, children }: BadgeProps) {
  return <span className={cn(badge({ tone, shape }), className)}>{children}</span>;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/Badge.test.tsx && npm test && npm run typecheck`
Expected: 4 条全 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Badge.tsx frontend/src/components/ui/Badge.test.tsx
git commit -m "$(cat <<'EOF'
feat: 基座新增 Badge

收敛三套并行徽章样式。shape 区分语义类别而非装饰：状态是会变的画胶囊，
类型是固有属性画方角标签，形状本身携带信息。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Checkbox

**Files:**
- Create: `frontend/src/components/ui/Checkbox.tsx`
- Test: `frontend/src/components/ui/Checkbox.test.tsx`

**Interfaces:**
- Consumes: Task 1 的聚合包、Task 2 的 pointer capture polyfill
- Produces:
  ```tsx
  export function Checkbox(props: {
    checked: boolean | "indeterminate";
    onCheckedChange: (checked: boolean) => void;
    label: string;   // 必填，作为 aria-label
    className?: string;
  }): JSX.Element
  ```
  `DataTable` 的行选择列消费它

**背景：** `DocumentPanel.tsx:116,120` 现在用裸 `<input type="checkbox">`，且和文件名塞在同一个 `<td>` 里。表头那个「选择全部」在部分选中时应当是 indeterminate 状态，原生 checkbox 只能靠命令式设 `el.indeterminate = true`，React 里做不到声明式——这是换 Radix 的实际理由，不是为了好看。

- [ ] **Step 1: 写失败的测试**

`frontend/src/components/ui/Checkbox.test.tsx`：

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Checkbox } from "./Checkbox";

afterEach(cleanup);

test("点击时把新状态回传给调用方", async () => {
  const onCheckedChange = vi.fn();
  render(<Checkbox checked={false} onCheckedChange={onCheckedChange} label="选择 报销制度.md" />);

  await userEvent.click(screen.getByRole("checkbox", { name: "选择 报销制度.md" }));
  expect(onCheckedChange).toHaveBeenCalledWith(true);
});

test("indeterminate 是声明式的第三态", () => {
  // 换 Radix 的实际理由就是这个：原生 checkbox 的 indeterminate 只能命令式设
  // el.indeterminate = true，React 里没法声明式表达。表头「选择全部」在部分选中时
  // 必须是这个态，否则用户看到的是「未选中」，点一下反而全选——与预期相反。
  render(<Checkbox checked="indeterminate" onCheckedChange={() => {}} label="选择全部资料" />);

  expect(screen.getByRole("checkbox", { name: "选择全部资料" })).toHaveAttribute(
    "data-state",
    "indeterminate",
  );
});

test("indeterminate 点击后回传 true，即全选而非全不选", async () => {
  const onCheckedChange = vi.fn();
  render(<Checkbox checked="indeterminate" onCheckedChange={onCheckedChange} label="选择全部资料" />);

  await userEvent.click(screen.getByRole("checkbox", { name: "选择全部资料" }));
  expect(onCheckedChange).toHaveBeenCalledWith(true);
});

test("label 是必填的，它就是可访问名", () => {
  render(<Checkbox checked onCheckedChange={() => {}} label="选择 常见问题解答.md" />);

  // 表格里几十个 checkbox 长得完全一样，没有可访问名的话屏幕阅读器读出来
  // 是几十个「复选框」。label 做成必填，从类型上就不可能漏。
  expect(screen.getByRole("checkbox", { name: "选择 常见问题解答.md" })).toBeInTheDocument();
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/Checkbox.test.tsx`
Expected: FAIL —— `Failed to resolve import "./Checkbox"`

- [ ] **Step 3: 实现**

`frontend/src/components/ui/Checkbox.tsx`：

```tsx
import { Checkbox as RadixCheckbox } from "radix-ui";
import { Check, Minus } from "lucide-react";
import { cn } from "./cn";

/**
 * 统一复选框。
 *
 * **换 Radix 的理由是 indeterminate，不是外观。** 原生 checkbox 的第三态只能命令式设置
 * （`el.indeterminate = true`），React 里没有声明式表达——而表格头部的「选择全部」在部分
 * 选中时必须是这个态，否则用户看到「未选中」，点一下却变成全选，与预期相反。
 *
 * `label` 做成必填：一张表里几十个 checkbox 长得一模一样，缺了可访问名，屏幕阅读器
 * 读出来是几十个「复选框」。做成必填就不可能漏。
 */
export function Checkbox({
  checked,
  onCheckedChange,
  label,
  className,
}: {
  checked: boolean | "indeterminate";
  onCheckedChange: (checked: boolean) => void;
  /** 可访问名。必填。 */
  label: string;
  className?: string;
}) {
  return (
    <RadixCheckbox.Root
      checked={checked}
      // indeterminate 点击后 Radix 回传 true（全选），这符合用户预期：
      // 部分选中时点一下是「补齐」，不是「清空」。
      onCheckedChange={(next) => onCheckedChange(next === true)}
      aria-label={label}
      className={cn(
        // border-0 不适用：这个组件的边框就是它的形状。但 preflight 未启用，
        // 需要显式声明 border 的粗细与颜色，不能依赖 UA 默认值。
        "grid h-4 w-4 shrink-0 place-items-center rounded-sm border border-line-firm bg-surface",
        "data-[state=checked]:border-brand data-[state=checked]:bg-brand",
        "data-[state=indeterminate]:border-brand data-[state=indeterminate]:bg-brand",
        "focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand/20",
        className,
      )}
    >
      <RadixCheckbox.Indicator className="text-white">
        {checked === "indeterminate" ? <Minus size={11} strokeWidth={3} /> : <Check size={11} strokeWidth={3} />}
      </RadixCheckbox.Indicator>
    </RadixCheckbox.Root>
  );
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/Checkbox.test.tsx && npm test && npm run typecheck`
Expected: 4 条全 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Checkbox.tsx frontend/src/components/ui/Checkbox.test.tsx
git commit -m "$(cat <<'EOF'
feat: 基座新增 Checkbox

换 Radix 的理由是 indeterminate 需要声明式表达，原生 checkbox 只能命令式设置。
label 做成必填，避免一张表几十个无名复选框。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Skeleton

**Files:**
- Create: `frontend/src/components/ui/Skeleton.tsx`
- Test: `frontend/src/components/ui/Skeleton.test.tsx`

**Interfaces:**
- Consumes: `cn` from `./cn`
- Produces:
  ```tsx
  export function Skeleton(props: { className?: string }): JSX.Element
  export function SkeletonRows(props: { rows: number; columns: number }): JSX.Element
  ```
  `DataTable` 的 `loading` 态消费 `SkeletonRows`

**背景：** 现在的加载态是一句「正在读取知识库…」（`.evaluation-state.pulse`），文字高度和表格高度差得远，数据一到页面就跳一下。骨架屏按真实行数占位，布局不动。

- [ ] **Step 1: 写失败的测试**

`frontend/src/components/ui/Skeleton.test.tsx`：

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { Skeleton, SkeletonRows } from "./Skeleton";

afterEach(cleanup);

test("骨架块对辅助技术不可见", () => {
  render(<Skeleton className="h-4 w-20" />);

  // 骨架屏是视觉占位，不承载信息。让屏幕阅读器去读一堆空盒子只会制造噪音；
  // 加载状态由外层的 role="status" 承担。
  expect(screen.queryByRole("presentation")).toBeNull();
  expect(document.querySelector("[aria-hidden='true']")).not.toBeNull();
});

test("外部 className 决定尺寸，组件只提供质感", () => {
  render(<Skeleton className="h-8 w-40" />);

  const cls = document.querySelector("[aria-hidden='true']")!.className.split(/\s+/);
  expect(cls).toContain("h-8");
  expect(cls).toContain("w-40");
  expect(cls).toContain("animate-pulse");
});

test("SkeletonRows 按给定行列数占位，行高与真实表格一致", () => {
  render(
    <table>
      <tbody>
        <SkeletonRows rows={3} columns={4} />
      </tbody>
    </table>,
  );

  const rows = document.querySelectorAll("tr");
  expect(rows).toHaveLength(3);
  expect(rows[0].querySelectorAll("td")).toHaveLength(4);
  // h-14 必须与 DataTable 的行高一致，否则数据到达时页面会跳——
  // 而消除这个跳动正是引入骨架屏的全部理由。
  expect(rows[0].className).toContain("h-14");
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/Skeleton.test.tsx`
Expected: FAIL —— `Failed to resolve import "./Skeleton"`

- [ ] **Step 3: 实现**

`frontend/src/components/ui/Skeleton.tsx`：

```tsx
import { cn } from "./cn";

/**
 * 骨架占位。
 *
 * 替代「正在读取知识库…」这类文案：文字高度与表格高度差得远，数据一到页面就跳一下。
 * 骨架按真实行高占位，布局全程不动。
 *
 * 对辅助技术不可见——它是视觉占位，不承载信息，加载状态由外层的 role="status" 承担。
 */
export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden="true" className={cn("block animate-pulse rounded-sm bg-divider", className)} />;
}

/** 表格骨架行。行高必须与 DataTable 一致，否则数据到达时页面仍会跳。 */
export function SkeletonRows({ rows, columns }: { rows: number; columns: number }) {
  return (
    <>
      {Array.from({ length: rows }, (_, row) => (
        <tr key={row} className="h-14 border-b border-divider">
          {Array.from({ length: columns }, (_, column) => (
            <td key={column} className="px-3">
              {/* 宽度交错，避免一列列等宽的骨架看起来像真实数据。 */}
              <Skeleton className={column === 0 ? "h-3 w-32" : "h-3 w-16"} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/Skeleton.test.tsx && npm test && npm run typecheck`
Expected: 3 条全 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Skeleton.tsx frontend/src/components/ui/Skeleton.test.tsx
git commit -m "$(cat <<'EOF'
feat: 基座新增 Skeleton

替代「正在读取…」文案。文字高度与表格高度差得远，数据到达时页面会跳；
骨架按真实行高占位，布局全程不动。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: EmptyState

**Files:**
- Create: `frontend/src/components/ui/EmptyState.tsx`
- Test: `frontend/src/components/ui/EmptyState.test.tsx`

**Interfaces:**
- Consumes: `Button` from `./Button`
- Produces:
  ```tsx
  export type EmptyStateProps = {
    kind: "empty" | "filtered";
    title: string;
    description: string;
    action?: { label: string; onClick: () => void };
  };
  export function EmptyState(props: EmptyStateProps): JSX.Element
  ```
  `DataTable` 的 `emptyState` 必填 prop 接收的正是 `EmptyStateProps`

**背景：** `KnowledgeBasesPage.tsx:42` 已经区分了「还没有知识库」和「没有符合条件的知识库」两种文案，做得对；但 `DocumentPanel.tsx:113` 只有一句「还没有资料」，筛选后无结果也显示它——用户会以为资料被删了。把区分做成类型上的必选项，抄不错。

- [ ] **Step 1: 写失败的测试**

`frontend/src/components/ui/EmptyState.test.tsx`：

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { EmptyState } from "./EmptyState";

afterEach(cleanup);

test("两种空态渲染不同图标，用户能分辨「没有」和「没找到」", () => {
  const { rerender } = render(
    <EmptyState kind="empty" title="还没有资料" description="上传一份文档后即可检索。" />,
  );
  const emptyIcon = document.querySelector("svg")?.getAttribute("class");

  rerender(
    <EmptyState kind="filtered" title="没有符合条件的资料" description="调整筛选条件后重试。" />,
  );
  const filteredIcon = document.querySelector("svg")?.getAttribute("class");

  // DocumentPanel.tsx:113 现在筛选无结果也显示「还没有资料」，用户会以为资料被删了。
  // 两种态必须长得不一样。
  expect(emptyIcon).not.toBe(filteredIcon);
});

test("标题用 heading 角色，能被导航到", () => {
  render(<EmptyState kind="empty" title="还没有知识库" description="创建一个后即可上传资料。" />);

  expect(screen.getByRole("heading", { name: "还没有知识库" })).toBeInTheDocument();
});

test("给了 action 才渲染按钮", async () => {
  const onClick = vi.fn();
  const { rerender } = render(
    <EmptyState kind="empty" title="还没有知识库" description="创建一个后即可上传资料。" />,
  );
  expect(screen.queryByRole("button")).toBeNull();

  rerender(
    <EmptyState
      kind="empty"
      title="还没有知识库"
      description="创建一个后即可上传资料。"
      action={{ label: "新建知识库", onClick }}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "新建知识库" }));
  expect(onClick).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/EmptyState.test.tsx`
Expected: FAIL —— `Failed to resolve import "./EmptyState"`

- [ ] **Step 3: 实现**

`frontend/src/components/ui/EmptyState.tsx`：

```tsx
import { Inbox, SearchX } from "lucide-react";
import { Button } from "./Button";

/**
 * 空态。
 *
 * **kind 是必填的。** 「一条都没有」和「筛选后没找到」是两回事：前者要引导创建，
 * 后者要引导放宽条件。DocumentPanel.tsx:113 现在两种情况都显示「还没有资料」，
 * 用户筛完看到它会以为资料被删了。做成必填，抄的时候不可能漏。
 */
export type EmptyStateProps = {
  kind: "empty" | "filtered";
  title: string;
  description: string;
  action?: { label: string; onClick: () => void };
};

export function EmptyState({ kind, title, description, action }: EmptyStateProps) {
  const Icon = kind === "empty" ? Inbox : SearchX;
  return (
    <div className="grid justify-items-center gap-2 px-4 py-14 text-center">
      <Icon size={28} className="text-ink-faint" aria-hidden />
      <h2 className="text-md font-semibold text-ink">{title}</h2>
      <p className="max-w-80 text-base text-ink-muted">{description}</p>
      {action ? (
        <Button className="mt-1" onClick={action.onClick}>
          {action.label}
        </Button>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/EmptyState.test.tsx && npm test && npm run typecheck`
Expected: 3 条全 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/EmptyState.tsx frontend/src/components/ui/EmptyState.test.tsx
git commit -m "$(cat <<'EOF'
feat: 基座新增 EmptyState

kind 必填，强制区分「一条都没有」与「筛选后没找到」——前者引导创建，后者引导放宽条件。
DocumentPanel 现在两种情况共用一句文案，筛完会让用户以为资料被删了。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Pagination

**Files:**
- Create: `frontend/src/components/ui/Pagination.tsx`
- Test: `frontend/src/components/ui/Pagination.test.tsx`

**Interfaces:**
- Consumes: `Button` from `./Button`
- Produces:
  ```tsx
  export function Pagination(props: {
    page: number;          // 0-based
    hasNext: boolean;
    onChange: (page: number) => void;
    label: string;         // aria-label，如「知识库分页」
  }): JSX.Element | null
  ```
  知识库页、数据源页、成员页、文档页、审计页共用

**背景：** `KnowledgeBasesPage.tsx:44` 和 `DataSourcesPage.tsx:38` 已经各写了一遍一模一样的分页，两处都用 `reasonHidden` + 「已经是第一页」。抽出来，顺便让它在只有一页时自己消失。

- [ ] **Step 1: 写失败的测试**

`frontend/src/components/ui/Pagination.test.tsx`：

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Pagination } from "./Pagination";

afterEach(cleanup);

test("只有一页时整个组件不渲染", () => {
  const { container } = render(
    <Pagination page={0} hasNext={false} onChange={() => {}} label="知识库分页" />,
  );

  // 三行数据下面挂一个「第 1 页」+ 两个灰按钮，是纯噪音。
  expect(container).toBeEmptyDOMElement();
});

test("第一页时上一页禁用且说得出原因", () => {
  render(<Pagination page={0} hasNext onChange={() => {}} label="知识库分页" />);

  const prev = screen.getByRole("button", { name: "上一页" });
  expect(prev).toBeDisabled();
  expect(prev).toHaveAttribute("title", "已经是第一页");
});

test("翻页回传 0-based 页码", async () => {
  const onChange = vi.fn();
  render(<Pagination page={2} hasNext onChange={onChange} label="知识库分页" />);

  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  expect(onChange).toHaveBeenCalledWith(3);

  await userEvent.click(screen.getByRole("button", { name: "上一页" }));
  expect(onChange).toHaveBeenCalledWith(1);
});

test("页码按 1-based 显示给用户", () => {
  render(<Pagination page={2} hasNext onChange={() => {}} label="知识库分页" />);

  // 内部 0-based、显示 1-based。两处旧实现都这么做，保持一致。
  expect(screen.getByText("第 3 页")).toBeInTheDocument();
});

test("label 成为导航的可访问名", () => {
  render(<Pagination page={0} hasNext onChange={() => {}} label="审计记录分页" />);

  expect(screen.getByRole("navigation", { name: "审计记录分页" })).toBeInTheDocument();
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/Pagination.test.tsx`
Expected: FAIL —— `Failed to resolve import "./Pagination"`

- [ ] **Step 3: 实现**

`frontend/src/components/ui/Pagination.tsx`：

```tsx
import { Button } from "./Button";

/**
 * 分页。抽自 KnowledgeBasesPage.tsx:44 与 DataSourcesPage.tsx:38——两处逐字相同。
 *
 * **只有一页时整个组件不渲染。** 三行数据下面挂一个「第 1 页」加两个灰按钮是纯噪音。
 * 旧实现把这个判断留在调用方（`{items && (page > 0 || hasNext) ? ... : null}`），
 * 于是每个新页面都要记得抄一遍；收进组件里就不会漏。
 *
 * 页码内部 0-based、显示 1-based，与两处旧实现一致。
 */
export function Pagination({
  page,
  hasNext,
  onChange,
  label,
}: {
  /** 0-based。 */
  page: number;
  hasNext: boolean;
  onChange: (page: number) => void;
  /** 导航的可访问名，如「知识库分页」。 */
  label: string;
}) {
  if (page === 0 && !hasNext) return null;
  return (
    <nav className="mt-3 flex items-center justify-end gap-2 text-base text-ink-muted" aria-label={label}>
      <Button
        variant="outline"
        size="sm"
        reasonHidden
        blockedReason={page === 0 ? "已经是第一页" : undefined}
        onClick={() => onChange(Math.max(0, page - 1))}
      >
        上一页
      </Button>
      <span>第 {page + 1} 页</span>
      <Button
        variant="outline"
        size="sm"
        reasonHidden
        blockedReason={hasNext ? undefined : "没有下一页"}
        onClick={() => onChange(page + 1)}
      >
        下一页
      </Button>
    </nav>
  );
}
```

**注意：** 这里仍然用 `reasonHidden`。阶段 2 的第一个任务会废掉这个 prop，届时这两处改为 Tooltip 承载原因。本阶段不改 `Button` 的渲染行为，保持基线全绿。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/Pagination.test.tsx && npm test && npm run typecheck`
Expected: 5 条全 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Pagination.tsx frontend/src/components/ui/Pagination.test.tsx
git commit -m "$(cat <<'EOF'
feat: 基座新增 Pagination

抽自知识库页与数据源页的两份逐字相同实现。「只有一页就不渲染」的判断收进组件，
不再留给每个调用方各抄一遍。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: RowActions（含 DropdownMenu 包装）

**Files:**
- Create: `frontend/src/components/ui/RowActions.tsx`
- Test: `frontend/src/components/ui/RowActions.test.tsx`

**Interfaces:**
- Consumes: Task 1 的聚合包、Task 2 的 pointer capture polyfill、`Button`
  （**不消费 `Tooltip`**：平铺形态的禁用原因走 `Button` 的 `title`，菜单项的原因内联在标签右侧。
  菜单里的浮层在触屏上要长按才出得来，内联文字更可靠；平铺形态的 Tooltip 化是阶段 2
  Button 改造的统一动作，不在这里各做一遍。）
- Produces:
  ```tsx
  export type RowAction = {
    label: string;
    onSelect: () => void;
    tone?: "default" | "destructive";
    blockedReason?: string;
  };
  export function RowActions(props: { actions: RowAction[]; rowLabel: string }): JSX.Element | null
  ```
  `DataTable` 的操作列消费它

**为什么 DropdownMenu 不单独成任务：** 它没有独立的消费者。全仓库唯一需要下拉菜单的地方就是行操作，把 Radix 包装和 `RowActions` 拆成两个文件只会多一层没人直接用的间接层。（`docs/design/ui-foundation-tokens.md` 曾记「不做 DropdownMenu，全仓库零个自定义 dropdown，没有治理对象」——现在有了治理对象：行操作。）

**背景：** 三张表的操作列现在都是一排 ghost 按钮（`KnowledgeBasesPage.tsx:43` 三个、`DocumentPanel.tsx:127` 二到三个、`MembersPage.tsx:217` 两个）。文档表第 3 行有「重新分类/编辑/删除」三个而其他行只有两个，列宽随行变化。规则：≤2 个平铺，≥3 个收进 `⋯`——列宽从此恒定。

- [ ] **Step 1: 写失败的测试**

`frontend/src/components/ui/RowActions.test.tsx`：

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
// 不 import vi：下面六条测试的回调全是 `() => {}` 占位，没有一条需要 spy，
// 留着它 ESLint 会报未使用变量。
import { afterEach, expect, test } from "vitest";
import { RowActions } from "./RowActions";

afterEach(cleanup);

test("两个操作平铺，不套菜单", () => {
  render(
    <RowActions
      rowLabel="企业知识库"
      actions={[
        { label: "详情", onSelect: () => {} },
        { label: "编辑", onSelect: () => {} },
      ]}
    />,
  );

  expect(screen.getByRole("button", { name: "详情" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "编辑" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /更多操作/ })).toBeNull();
});

test("三个及以上收进菜单，列宽从此恒定", async () => {
  render(
    <RowActions
      rowLabel="新员工入职指引.md"
      actions={[
        { label: "重新分类", onSelect: () => {} },
        { label: "编辑", onSelect: () => {} },
        { label: "删除", onSelect: () => {}, tone: "destructive" },
      ]}
    />,
  );

  // 文档表第 3 行有三个按钮而其他行只有两个，操作列宽度逐行变化。
  // 阈值定在 3 就是为了消灭这个抖动。
  expect(screen.queryByRole("button", { name: "重新分类" })).toBeNull();
  const trigger = screen.getByRole("button", { name: "新员工入职指引.md 的更多操作" });

  await userEvent.click(trigger);
  expect(await screen.findByRole("menuitem", { name: "重新分类" })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: "删除" })).toBeInTheDocument();
});

test("destructive 项排在最后，与其余项之间有分隔", async () => {
  render(
    <RowActions
      rowLabel="报销制度.md"
      actions={[
        { label: "删除", onSelect: () => {}, tone: "destructive" },
        { label: "重新分类", onSelect: () => {} },
        { label: "编辑", onSelect: () => {} },
      ]}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "报销制度.md 的更多操作" }));
  const items = await screen.findAllByRole("menuitem");
  // 调用方把删除写在第一个，组件也必须把它放到最后——不能指望每个调用点都记得顺序。
  expect(items.map((item) => item.textContent)).toEqual(["重新分类", "编辑", "删除"]);
  expect(screen.getByRole("separator")).toBeInTheDocument();
});

test("菜单里的禁用项说得出原因", async () => {
  render(
    <RowActions
      rowLabel="默认知识库"
      actions={[
        { label: "详情", onSelect: () => {} },
        { label: "编辑", onSelect: () => {} },
        { label: "删除", onSelect: () => {}, tone: "destructive", blockedReason: "默认知识库不能删除" },
      ]}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "默认知识库 的更多操作" }));
  const remove = await screen.findByRole("menuitem", { name: /删除/ });
  expect(remove).toHaveAttribute("data-disabled");
  // CLAUDE.md 第一条：禁用必须说得出为什么。菜单项里没有空间放小字，
  // 原因直接排在标签后面。
  expect(remove.textContent).toContain("默认知识库不能删除");
});

test("平铺形态下的禁用项同样说得出原因", () => {
  render(
    <RowActions
      rowLabel="默认知识库"
      actions={[
        { label: "详情", onSelect: () => {} },
        { label: "删除", onSelect: () => {}, tone: "destructive", blockedReason: "默认知识库不能删除" },
      ]}
    />,
  );

  const remove = screen.getByRole("button", { name: "删除" });
  expect(remove).toBeDisabled();
  expect(remove).toHaveAttribute("title", "默认知识库不能删除");
});

test("没有可用操作时整个组件不渲染", () => {
  const { container } = render(<RowActions rowLabel="企业知识库" actions={[]} />);

  expect(container).toBeEmptyDOMElement();
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/RowActions.test.tsx`
Expected: FAIL —— `Failed to resolve import "./RowActions"`

- [ ] **Step 3: 实现**

`frontend/src/components/ui/RowActions.tsx`：

```tsx
import { DropdownMenu } from "radix-ui";
import { MoreHorizontal } from "lucide-react";
import { Button } from "./Button";
import { cn } from "./cn";

/**
 * 行操作的唯一出口。
 *
 * **阈值 3 不是审美选择，是为了消灭列宽抖动。** 文档表第 3 行有「重新分类/编辑/删除」
 * 三个按钮，其他行只有两个，于是操作列的宽度逐行变化，右对齐也救不回来。
 * ≤2 平铺、≥3 收进 `⋯`，列宽从此恒定。
 *
 * **destructive 项由组件强制排到最后并加分隔线**，不依赖调用方的书写顺序——
 * 「删除」紧挨着「编辑」是误点的主要来源，而每个调用点都记得把它写在最后是不现实的。
 */
export type RowAction = {
  label: string;
  onSelect: () => void;
  tone?: "default" | "destructive";
  /** 给了就禁用。见 CLAUDE.md 第一条。 */
  blockedReason?: string;
};

export function RowActions({ actions, rowLabel }: { actions: RowAction[]; rowLabel: string }) {
  if (actions.length === 0) return null;

  // 排序在组件内完成：destructive 一律沉底。
  const ordered = [...actions].sort((a, b) => Number(a.tone === "destructive") - Number(b.tone === "destructive"));
  const destructiveCount = ordered.filter((item) => item.tone === "destructive").length;

  if (ordered.length <= 2) {
    return (
      <div className="flex items-center justify-end gap-1">
        {ordered.map((action) => (
          <Button
            key={action.label}
            variant="ghost"
            size="sm"
            reasonHidden
            blockedReason={action.blockedReason}
            className={action.tone === "destructive" ? "text-danger-text hover:bg-danger-subtle" : undefined}
            onClick={action.onSelect}
          >
            {action.label}
          </Button>
        ))}
      </div>
    );
  }

  return (
    <div className="flex items-center justify-end">
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          {/* rowLabel 进可访问名：一张表几十个 ⋯ 按钮，没有它屏幕阅读器读出来
              是几十个「更多操作」，用户不知道自己在操作哪一行。 */}
          <Button variant="ghost" size="icon" aria-label={`${rowLabel} 的更多操作`}>
            <MoreHorizontal size={15} />
          </Button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            align="end"
            sideOffset={4}
            className="z-50 min-w-36 rounded-md border border-line bg-surface p-1 shadow-pop"
          >
            {ordered.map((action, index) => (
              <div key={action.label}>
                {/* 分隔线画在第一个 destructive 项之前。 */}
                {destructiveCount > 0 && action.tone === "destructive" && index === ordered.length - destructiveCount ? (
                  <DropdownMenu.Separator className="my-1 h-px bg-divider" />
                ) : null}
                <DropdownMenu.Item
                  disabled={Boolean(action.blockedReason)}
                  onSelect={action.onSelect}
                  className={cn(
                    "flex cursor-pointer items-center justify-between gap-3 rounded-sm px-2 py-1.5 text-md outline-none",
                    "data-[highlighted]:bg-canvas",
                    "data-[disabled]:cursor-not-allowed data-[disabled]:opacity-55",
                    action.tone === "destructive" ? "text-danger-text data-[highlighted]:bg-danger-subtle" : "text-ink",
                  )}
                >
                  {action.label}
                  {/* 菜单项里没有空间放下方小字，原因直接排在标签右侧。 */}
                  {action.blockedReason ? (
                    <span className="text-sm text-ink-faint">{action.blockedReason}</span>
                  ) : null}
                </DropdownMenu.Item>
              </div>
            ))}
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/RowActions.test.tsx && npm test && npm run typecheck`
Expected: 6 条全 PASS。若「三个及以上收进菜单」这条报 `TypeError: target.hasPointerCapture is not a function`，说明 Task 2 的 polyfill 没生效，回去检查 `test-setup.ts`。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/RowActions.tsx frontend/src/components/ui/RowActions.test.tsx
git commit -m "$(cat <<'EOF'
feat: 基座新增 RowActions

行操作唯一出口。≤2 平铺、≥3 收进 ⋯，消灭文档表逐行变化的操作列宽度。
destructive 项由组件强制沉底并加分隔线，不依赖调用方的书写顺序。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: DataTable

**Files:**
- Create: `frontend/src/components/ui/DataTable.tsx`
- Test: `frontend/src/components/ui/DataTable.test.tsx`

**Interfaces:**
- Consumes: `Checkbox`、`EmptyState`（含 `EmptyStateProps`）、`SkeletonRows`、`cn`
  （**不消费 `Tooltip`**：截断单元格靠 `truncate` + 原生 `title` 即可，给每个单元格套一层
  Tooltip 会在一张几十行的表里挂出几百个 Provider。截断内容的 Tooltip 化留到阶段 2，
  由各页面按列决定哪些列真的需要。）
- Produces:
  ```tsx
  export type Column<T> = {
    key: string;
    header: string;
    align?: "left" | "right";
    width?: string;              // CSS 宽度，如 "180px" / "20%"
    numeric?: boolean;           // 等宽数字 + 右对齐
    render: (row: T) => ReactNode;
  };
  export function DataTable<T>(props: {
    rows: T[] | null;            // null = 加载中
    columns: Column<T>[];
    rowKey: (row: T) => string;
    emptyState: EmptyStateProps; // 必填
    label: string;               // 表格可访问名
    density?: "default" | "compact";
    selection?: {
      selected: string[];
      onChange: (selected: string[]) => void;
      rowLabel: (row: T) => string;
    };
  }): JSX.Element
  ```
  阶段 2 起，五张列表全部消费它

**这是基座的核心。** 它一次性解决：两套列表实现、三种行高、缺分隔线、列宽漂移、数字不对齐、checkbox 与文件名挤在一格、空态不分类、加载态跳动。

- [ ] **Step 1: 写失败的测试**

`frontend/src/components/ui/DataTable.test.tsx`：

```tsx
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { DataTable, type Column } from "./DataTable";

afterEach(cleanup);

type Row = { id: string; name: string; count: number };

const ROWS: Row[] = [
  { id: "a", name: "企业知识库", count: 5 },
  { id: "b", name: "默认知识库", count: 0 },
];

const COLUMNS: Column<Row>[] = [
  { key: "name", header: "知识库名称", render: (row) => row.name },
  { key: "count", header: "文档数量", numeric: true, render: (row) => row.count },
];

const EMPTY = { kind: "empty", title: "还没有知识库", description: "创建一个后即可上传资料。" } as const;

test("渲染表头与数据行", () => {
  render(
    <DataTable rows={ROWS} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  const table = screen.getByRole("table", { name: "知识库列表" });
  expect(within(table).getByRole("columnheader", { name: "知识库名称" })).toBeInTheDocument();
  expect(within(table).getByText("企业知识库")).toBeInTheDocument();
  expect(within(table).getAllByRole("row")).toHaveLength(3); // 表头 + 2 行
});

test("每一行都有下边框，最后一行没有", () => {
  render(
    <DataTable rows={ROWS} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  // CLAUDE.md 记过这个真实缺陷：--border 等 5 个变量从未定义，
  // 相关 CSS 声明全部失效，三张表都没有分隔线，靠 hover 底色区分行。
  const bodyRows = screen.getAllByRole("row").slice(1);
  expect(bodyRows[0].className).toContain("border-b");
  expect(bodyRows[bodyRows.length - 1].className).toContain("border-b-0");
});

test("行高统一，compact 只用于超长列表", () => {
  const { rerender } = render(
    <DataTable rows={ROWS} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );
  expect(screen.getAllByRole("row")[1].className).toContain("h-14");

  rerender(
    <DataTable rows={ROWS} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" density="compact" />,
  );
  expect(screen.getAllByRole("row")[1].className).toContain("h-11");
});

test("numeric 列等宽右对齐", () => {
  render(
    <DataTable rows={ROWS} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  // 文档数、切片数逐行宽度不同，整列看着是歪的。tabular-nums 让每个数字等宽。
  const cell = screen.getByText("5").closest("td")!;
  expect(cell.className).toContain("tabular-nums");
  expect(cell.className).toContain("text-right");
});

test("width 落到 col 元素上，列宽不随内容变化", () => {
  const columns: Column<Row>[] = [
    { key: "name", header: "知识库名称", width: "200px", render: (row) => row.name },
    { key: "count", header: "文档数量", render: (row) => row.count },
  ];
  render(
    <DataTable rows={ROWS} columns={columns} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  // 用 <col> 而不是给 th 加 className：th 上的宽度会被内容撑开，
  // <col> 配 table-fixed 才是硬约束。列宽漂移就是这么来的。
  const col = document.querySelector("col");
  expect(col).toHaveStyle({ width: "200px" });
  expect(screen.getByRole("table").className).toContain("table-fixed");
});

test("rows 为 null 时显示骨架而不是文案", () => {
  render(
    <DataTable rows={null} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  expect(screen.queryByText("还没有知识库")).toBeNull();
  expect(screen.getByRole("status")).toBeInTheDocument();
  // 骨架行数固定为 3，行高与真实行一致，数据到达时布局不跳。
  expect(screen.getAllByRole("row").slice(1)).toHaveLength(3);
});

test("空数组时渲染 emptyState 而不是空表格", () => {
  render(
    <DataTable rows={[]} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  expect(screen.getByRole("heading", { name: "还没有知识库" })).toBeInTheDocument();
  expect(screen.queryByRole("table")).toBeNull();
});

test("选择列独立成列，不与首列内容挤在一起", async () => {
  const onChange = vi.fn();
  render(
    <DataTable
      rows={ROWS}
      columns={COLUMNS}
      rowKey={(row) => row.id}
      emptyState={EMPTY}
      label="知识库列表"
      selection={{ selected: [], onChange, rowLabel: (row) => row.name }}
    />,
  );

  // DocumentPanel.tsx:120 把 checkbox 和文件名塞进同一个 <td>，
  // 截图上 checkbox 浮在文件名上方。这里它必须是自己的一列。
  const firstBodyRow = screen.getAllByRole("row")[1];
  const cells = within(firstBodyRow).getAllByRole("cell");
  expect(within(cells[0]).getByRole("checkbox")).toBeInTheDocument();
  expect(cells[0].textContent).toBe("");

  await userEvent.click(screen.getByRole("checkbox", { name: "选择 企业知识库" }));
  expect(onChange).toHaveBeenCalledWith(["a"]);
});

test("部分选中时表头是 indeterminate，点击后补齐为全选", async () => {
  const onChange = vi.fn();
  render(
    <DataTable
      rows={ROWS}
      columns={COLUMNS}
      rowKey={(row) => row.id}
      emptyState={EMPTY}
      label="知识库列表"
      selection={{ selected: ["a"], onChange, rowLabel: (row) => row.name }}
    />,
  );

  const all = screen.getByRole("checkbox", { name: "选择全部" });
  expect(all).toHaveAttribute("data-state", "indeterminate");

  await userEvent.click(all);
  expect(onChange).toHaveBeenCalledWith(["a", "b"]);
});

test("全选状态下再点击表头即清空", async () => {
  const onChange = vi.fn();
  render(
    <DataTable
      rows={ROWS}
      columns={COLUMNS}
      rowKey={(row) => row.id}
      emptyState={EMPTY}
      label="知识库列表"
      selection={{ selected: ["a", "b"], onChange, rowLabel: (row) => row.name }}
    />,
  );

  await userEvent.click(screen.getByRole("checkbox", { name: "选择全部" }));
  expect(onChange).toHaveBeenCalledWith([]);
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/DataTable.test.tsx`
Expected: FAIL —— `Failed to resolve import "./DataTable"`

- [ ] **Step 3: 实现**

`frontend/src/components/ui/DataTable.tsx`：

```tsx
import type { ReactNode } from "react";
import { Checkbox } from "./Checkbox";
import { EmptyState, type EmptyStateProps } from "./EmptyState";
import { SkeletonRows } from "./Skeleton";
import { cn } from "./cn";

/**
 * 全站唯一的表格。
 *
 * 它一次性收掉这些实际存在的问题：
 * - 两套列表实现（`<table class="management-table">` 与 `div[role=table]` + grid）
 * - 三种行高（成员 72px、知识库 59px、文档 52px）
 * - 缺行分隔线（`--border` 等变量从未定义，声明全部失效）
 * - 列宽随内容漂移（宽度写在 th 上，会被内容撑开）
 * - 数字不等宽，整列看着是歪的
 * - checkbox 与首列内容挤在同一个 td 里
 * - 空态不区分「没有」与「没找到」
 * - 加载态是一行文字，数据到达时页面跳一下
 *
 * **`emptyState` 是必填 prop。** 没有空态的表格在类型层面就不存在。
 */
export type Column<T> = {
  key: string;
  header: string;
  align?: "left" | "right";
  /** CSS 宽度，落到 <col> 上。配 table-fixed 才是硬约束。 */
  width?: string;
  /** 等宽数字 + 右对齐。文档数、切片数这类必须开。 */
  numeric?: boolean;
  render: (row: T) => ReactNode;
};

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  emptyState,
  label,
  density = "default",
  selection,
}: {
  /** null 表示加载中，[] 表示确实没有数据——两者渲染完全不同的东西。 */
  rows: T[] | null;
  columns: Column<T>[];
  rowKey: (row: T) => string;
  emptyState: EmptyStateProps;
  label: string;
  /** compact 仅用于行数可达数千的审计记录页。 */
  density?: "default" | "compact";
  selection?: {
    selected: string[];
    onChange: (selected: string[]) => void;
    /** 每行 checkbox 的可访问名来源，如 (row) => row.filename。 */
    rowLabel: (row: T) => string;
  };
}) {
  const rowHeight = density === "compact" ? "h-11" : "h-14";
  const columnCount = columns.length + (selection ? 1 : 0);

  if (rows !== null && rows.length === 0) return <EmptyState {...emptyState} />;

  const keys = rows?.map(rowKey) ?? [];
  const allSelected = Boolean(selection && keys.length > 0 && keys.every((key) => selection.selected.includes(key)));
  const someSelected = Boolean(selection && keys.some((key) => selection.selected.includes(key)));

  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface">
      <table
        aria-label={label}
        aria-busy={rows === null || undefined}
        role="table"
        // table-fixed 是 width 生效的前提：auto 布局下浏览器会按内容重算列宽，
        // <col width> 只被当作建议。
        className="w-full table-fixed border-collapse text-base"
      >
        <colgroup>
          {selection ? <col style={{ width: "44px" }} /> : null}
          {columns.map((column) => (
            <col key={column.key} style={column.width ? { width: column.width } : undefined} />
          ))}
        </colgroup>
        <thead>
          <tr className={cn("border-b border-line bg-canvas", density === "compact" ? "h-9" : "h-11")}>
            {selection ? (
              <th className="px-3">
                <Checkbox
                  checked={allSelected ? true : someSelected ? "indeterminate" : false}
                  onCheckedChange={(next) => selection.onChange(next ? keys : [])}
                  label="选择全部"
                />
              </th>
            ) : null}
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={cn(
                  "px-3 text-sm font-medium text-ink-muted",
                  column.numeric || column.align === "right" ? "text-right" : "text-left",
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows === null ? (
            <SkeletonRows rows={3} columns={columnCount} />
          ) : (
            rows.map((row, index) => {
              const key = rowKey(row);
              return (
                <tr
                  key={key}
                  className={cn(
                    rowHeight,
                    "border-b border-divider hover:bg-canvas",
                    // 最后一行不画线：容器自己有边框，再画一条就是双线。
                    index === rows.length - 1 && "border-b-0",
                  )}
                >
                  {selection ? (
                    <td className="px-3">
                      <Checkbox
                        checked={selection.selected.includes(key)}
                        onCheckedChange={(next) =>
                          selection.onChange(
                            next
                              ? [...selection.selected, key]
                              : selection.selected.filter((item) => item !== key),
                          )
                        }
                        label={`选择 ${selection.rowLabel(row)}`}
                      />
                    </td>
                  ) : null}
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      className={cn(
                        "truncate px-3 text-ink",
                        column.numeric && "tabular-nums text-right",
                        !column.numeric && column.align === "right" && "text-right",
                      )}
                    >
                      {column.render(row)}
                    </td>
                  ))}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
      {/* 加载状态由这里承担，骨架本身对辅助技术不可见。 */}
      {rows === null ? (
        <span role="status" className="sr-only">
          正在读取{label}
        </span>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/DataTable.test.tsx && npm test && npm run typecheck`
Expected: 10 条全 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/DataTable.tsx frontend/src/components/ui/DataTable.test.tsx
git commit -m "$(cat <<'EOF'
feat: 基座新增 DataTable

全站唯一表格。emptyState 做成必填，没有空态的表格在类型层面不存在；
宽度落到 col + table-fixed 消灭列宽漂移；选择列独立成列；null 与空数组
渲染截然不同的东西。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Toolbar

**Files:**
- Create: `frontend/src/components/ui/Toolbar.tsx`
- Test: `frontend/src/components/ui/Toolbar.test.tsx`

**Interfaces:**
- Consumes: `cn`
- Produces:
  ```tsx
  export function Toolbar(props: {
    filters?: ReactNode;
    actions?: ReactNode;
    /** 有选中项时才出现的批量操作区 */
    batch?: { count: number; children: ReactNode };
  }): JSX.Element
  ```
  五张列表页共用

**背景：** `DocumentPanel.tsx:103-109` 的工具栏把「分类筛选/状态筛选/批量归类下拉/应用到 N 份/重新分类 N 份」五个控件常驻一排，没勾选时后三个是死的。批量操作区改成有选中才出现——没选中时它们本来就无事可做，占着位置只会让人以为功能坏了。

- [ ] **Step 1: 写失败的测试**

`frontend/src/components/ui/Toolbar.test.tsx`：

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { Toolbar } from "./Toolbar";

afterEach(cleanup);

test("筛选区在左、操作区在右", () => {
  render(
    <Toolbar
      filters={<input aria-label="搜索知识库名称" />}
      actions={<button>知识库分类模板</button>}
    />,
  );

  expect(screen.getByLabelText("搜索知识库名称")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "知识库分类模板" })).toBeInTheDocument();
});

test("没有选中项时批量区不渲染", () => {
  render(
    <Toolbar
      filters={<input aria-label="搜索" />}
      batch={{ count: 0, children: <button>应用到 0 份</button> }}
    />,
  );

  // 五个控件常驻一排、后三个是死的，用户看到的是「功能坏了」。
  // 没选中时批量操作本来就无事可做，不占位置。
  expect(screen.queryByRole("button", { name: /应用到/ })).toBeNull();
});

test("有选中项时批量区出现并报出数量", () => {
  render(
    <Toolbar
      filters={<input aria-label="搜索" />}
      batch={{ count: 3, children: <button>应用到 3 份</button> }}
    />,
  );

  expect(screen.getByText("已选 3 项")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "应用到 3 份" })).toBeInTheDocument();
});

test("批量区用 role=status，选中数变化会被读出来", () => {
  render(<Toolbar batch={{ count: 2, children: <button>删除 2 项</button> }} />);

  // 勾选是鼠标操作，屏幕阅读器用户需要知道当前选了几项。
  expect(screen.getByRole("status")).toHaveTextContent("已选 2 项");
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/Toolbar.test.tsx`
Expected: FAIL —— `Failed to resolve import "./Toolbar"`

- [ ] **Step 3: 实现**

`frontend/src/components/ui/Toolbar.tsx`：

```tsx
import type { ReactNode } from "react";

/**
 * 列表页工具栏。
 *
 * **批量操作区有选中项才出现。** DocumentPanel 现在把「批量归类/应用到 N 份/重新分类
 * N 份」三个控件常驻一排，没勾选时全是死的——用户看到的不是「条件没满足」，
 * 是「功能坏了」。没选中时它们本来无事可做，不该占位置。
 */
export function Toolbar({
  filters,
  actions,
  batch,
}: {
  filters?: ReactNode;
  actions?: ReactNode;
  batch?: { count: number; children: ReactNode };
}) {
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap items-center gap-2">{filters}</div>
        <div className="ml-auto flex items-center gap-2">{actions}</div>
      </div>
      {batch && batch.count > 0 ? (
        <div
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-md border border-brand/25 bg-brand-subtle px-3 py-2"
        >
          <span className="text-base font-medium text-brand">已选 {batch.count} 项</span>
          {batch.children}
        </div>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/Toolbar.test.tsx && npm test && npm run typecheck`
Expected: 4 条全 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Toolbar.tsx frontend/src/components/ui/Toolbar.test.tsx
git commit -m "$(cat <<'EOF'
feat: 基座新增 Toolbar

批量操作区有选中项才出现。常驻一排死控件让用户以为功能坏了，
而没选中时它们本来就无事可做。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: MetricCard

**Files:**
- Create: `frontend/src/components/ui/MetricCard.tsx`
- Test: `frontend/src/components/ui/MetricCard.test.tsx`

**Interfaces:**
- Consumes: `cn`
- Produces:
  ```tsx
  export function MetricCard(props: {
    icon: ReactNode;
    label: string;
    value: ReactNode;
    note?: string;
    tone?: "neutral" | "success" | "danger";
  }): JSX.Element
  ```
  概览页 4 张指标卡、系统状态页、评测中心的指标条消费它

**背景：** `OverviewPage.tsx:7` 的 `MetricCard` 接受一个 `tone` 字符串直接拼进 className（`overview-icon ${tone}`），产生 6 套装饰底色；`valueClass` 又让「通过」这类文字值用了和数字不同的字重字号。截图上「3」和「通过」的视觉重量完全不同，四张卡读起来不像一组。

- [ ] **Step 1: 写失败的测试**

`frontend/src/components/ui/MetricCard.test.tsx`：

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { MetricCard } from "./MetricCard";

afterEach(cleanup);

test("数字与文字数值共用同一字阶和字重", () => {
  render(
    <div>
      <MetricCard icon={<svg />} label="知识库" value={3} note="规范隔离的独立空间" />
      <MetricCard icon={<svg />} label="回答质量门" value="通过" note="v3-grounded-answer-1" />
    </div>,
  );

  // 截图上「3」和「通过」的视觉重量完全不同，四张卡读起来不像一组。
  const numeric = screen.getByText("3").className.split(/\s+/);
  const textual = screen.getByText("通过").className.split(/\s+/);
  expect(numeric).toEqual(textual);
});

test("数值用等宽数字", () => {
  render(<MetricCard icon={<svg />} label="已索引资料" value={128} note="27 个可检索片段" />);

  // 指标卡并排时，非等宽数字会让基线看着参差。
  expect(screen.getByText("128").className).toContain("tabular-nums");
});

test("图标底色恒为中性，tone 只作用于数值", () => {
  render(<MetricCard icon={<svg data-testid="icon" />} label="回答质量门" value="未通过" tone="danger" />);

  // 6 套装饰底色不携带任何信息。颜色只留给数值本身表意。
  const iconBox = screen.getByTestId("icon").parentElement!;
  expect(iconBox.className).toContain("bg-canvas");
  expect(iconBox.className).not.toMatch(/bg-(danger|success|brand|warning)/);
  expect(screen.getByText("未通过").className).toContain("text-danger-text");
});

test("note 可省略", () => {
  render(<MetricCard icon={<svg />} label="历史会话" value={2} />);

  expect(screen.getByText("历史会话")).toBeInTheDocument();
  expect(screen.getByText("2")).toBeInTheDocument();
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/MetricCard.test.tsx`
Expected: FAIL —— `Failed to resolve import "./MetricCard"`

- [ ] **Step 3: 实现**

`frontend/src/components/ui/MetricCard.tsx`：

```tsx
import type { ReactNode } from "react";
import { cn } from "./cn";

/**
 * 指标卡。
 *
 * **数值的字阶和字重与内容类型无关。** 旧实现给文字值加了 `valueClass`，于是「3」和
 * 「通过」的视觉重量完全不同，四张卡并排读起来不像一组。这里数值样式是固定的，
 * `tone` 只改颜色。
 *
 * **图标底色恒为中性。** 旧实现有 6 套装饰底色（is-purple/green/blue/amber/slate/gray），
 * 它们不携带任何信息——颜色只留给数值本身表意。
 */
export function MetricCard({
  icon,
  label,
  value,
  note,
  tone = "neutral",
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  note?: string;
  tone?: "neutral" | "success" | "danger";
}) {
  return (
    <article className="grid gap-1.5 rounded-lg border border-line bg-surface p-4">
      <div className="flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded-md bg-canvas text-ink-muted">{icon}</span>
        <span className="text-base text-ink-muted">{label}</span>
      </div>
      <strong
        className={cn(
          "text-xl font-semibold tabular-nums",
          tone === "success" && "text-success",
          tone === "danger" && "text-danger-text",
          tone === "neutral" && "text-ink",
        )}
      >
        {value}
      </strong>
      {note ? <small className="text-sm text-ink-faint">{note}</small> : null}
    </article>
  );
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/MetricCard.test.tsx && npm test && npm run typecheck`
Expected: 4 条全 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/MetricCard.tsx frontend/src/components/ui/MetricCard.test.tsx
git commit -m "$(cat <<'EOF'
feat: 基座新增 MetricCard

数值字阶字重与内容类型无关，消灭「3」和「通过」两种视觉重量。
图标底色恒为中性——6 套装饰色不携带任何信息。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Tabs

**Files:**
- Create: `frontend/src/components/ui/Tabs.tsx`
- Test: `frontend/src/components/ui/Tabs.test.tsx`

**Interfaces:**
- Consumes: Task 1 的聚合包、`cn`
- Produces:
  ```tsx
  export type TabItem = { value: string; label: string; count?: number };
  export function Tabs(props: {
    items: TabItem[];
    value: string;
    onChange: (value: string) => void;
    children: ReactNode;   // 内容由调用方按 value 自行渲染
    label: string;         // tablist 可访问名
  }): JSX.Element
  ```
  知识库详情页 7 个 tab、评测中心消费它

**背景：** 详情页的 tab 现在是自定义实现，键盘只能 Tab 逐个走，方向键无效——原生 tablist 的规范是方向键切换、Tab 键跳出整组。7 个 tab 每个都要按一下 Tab 才能到达内容区。

- [ ] **Step 1: 写失败的测试**

`frontend/src/components/ui/Tabs.test.tsx`：

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Tabs } from "./Tabs";

afterEach(cleanup);

const ITEMS = [
  { value: "documents", label: "资料", count: 5 },
  { value: "categories", label: "分类管理", count: 6 },
  { value: "members", label: "权限边界", count: 1 },
];

test("渲染 tablist，选中项带 aria-selected", () => {
  render(
    <Tabs items={ITEMS} value="documents" onChange={() => {}} label="知识库详情">
      <div>内容</div>
    </Tabs>,
  );

  expect(screen.getByRole("tablist", { name: "知识库详情" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /资料/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: /分类管理/ })).toHaveAttribute("aria-selected", "false");
});

test("count 渲染在标签旁", () => {
  render(
    <Tabs items={ITEMS} value="documents" onChange={() => {}} label="知识库详情">
      <div>内容</div>
    </Tabs>,
  );

  expect(screen.getByRole("tab", { name: /资料/ }).textContent).toContain("5");
});

test("方向键切换，符合 tablist 键盘规范", async () => {
  const onChange = vi.fn();
  render(
    <Tabs items={ITEMS} value="documents" onChange={onChange} label="知识库详情">
      <div>内容</div>
    </Tabs>,
  );

  // 自定义实现的 tab 只能靠 Tab 键逐个走，7 个 tab 要按 7 下才能到内容区。
  // 规范是方向键在组内切换、Tab 键跳出整组——这是换 Radix 的实际理由。
  screen.getByRole("tab", { name: /资料/ }).focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(onChange).toHaveBeenCalledWith("categories");
});

test("点击切换回传新值", async () => {
  const onChange = vi.fn();
  render(
    <Tabs items={ITEMS} value="documents" onChange={onChange} label="知识库详情">
      <div>内容</div>
    </Tabs>,
  );

  await userEvent.click(screen.getByRole("tab", { name: /权限边界/ }));
  expect(onChange).toHaveBeenCalledWith("members");
});

test("内容区带 tabpanel 角色", () => {
  render(
    <Tabs items={ITEMS} value="documents" onChange={() => {}} label="知识库详情">
      <div>资料列表</div>
    </Tabs>,
  );

  expect(screen.getByRole("tabpanel")).toHaveTextContent("资料列表");
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/Tabs.test.tsx`
Expected: FAIL —— `Failed to resolve import "./Tabs"`

- [ ] **Step 3: 实现**

`frontend/src/components/ui/Tabs.tsx`：

```tsx
import { Tabs as RadixTabs } from "radix-ui";
import type { ReactNode } from "react";
import { cn } from "./cn";

/**
 * 统一分页签。
 *
 * **换 Radix 的理由是键盘规范。** 自定义实现只能靠 Tab 键逐个走，详情页 7 个 tab
 * 要按 7 下才能到达内容区；tablist 的规范是方向键在组内切换、Tab 键跳出整组。
 *
 * 内容由调用方按 value 自行渲染，不做 TabsContent 的多份挂载——详情页每个 tab 各自
 * 拉数据，全部挂载会在进页面时打七个请求。
 */
export type TabItem = { value: string; label: string; count?: number };

export function Tabs({
  items,
  value,
  onChange,
  children,
  label,
}: {
  items: TabItem[];
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  label: string;
}) {
  return (
    <RadixTabs.Root value={value} onValueChange={onChange}>
      <RadixTabs.List aria-label={label} className="flex gap-1 border-b border-line">
        {items.map((item) => (
          <RadixTabs.Trigger
            key={item.value}
            value={item.value}
            className={cn(
              "border-0 border-b-2 border-transparent bg-transparent px-3 py-2 text-md text-ink-muted",
              "cursor-pointer hover:text-ink",
              "focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand/20",
              "data-[state=active]:border-brand data-[state=active]:font-semibold data-[state=active]:text-ink",
            )}
          >
            {item.label}
            {item.count === undefined ? null : (
              <span className="ml-1.5 tabular-nums text-sm text-ink-faint">{item.count}</span>
            )}
          </RadixTabs.Trigger>
        ))}
      </RadixTabs.List>
      {/* 只挂载当前 tab 的内容：详情页每个 tab 各自拉数据，全挂载会一次打七个请求。 */}
      <RadixTabs.Content value={value} className="pt-4 focus-visible:outline-none">
        {children}
      </RadixTabs.Content>
    </RadixTabs.Root>
  );
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/Tabs.test.tsx && npm test && npm run typecheck`
Expected: 5 条全 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Tabs.tsx frontend/src/components/ui/Tabs.test.tsx
git commit -m "$(cat <<'EOF'
feat: 基座新增 Tabs

换 Radix 的理由是键盘规范：自定义实现只能 Tab 逐个走，详情页 7 个 tab
要按 7 下才到内容区。只挂载当前 tab 内容，避免进页面就打七个请求。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Toast

**Files:**
- Create: `frontend/src/components/ui/Toast.tsx`
- Test: `frontend/src/components/ui/Toast.test.tsx`

**Interfaces:**
- Consumes: `cn`
- Produces:
  ```tsx
  export function ToastProvider(props: { children: ReactNode }): JSX.Element
  export function useToast(): {
    success: (message: string) => void;
    error: (message: string) => void;
  }
  ```
  阶段 2 起，所有写操作完成后调用它。`ToastProvider` 在阶段 2 挂到 `App.tsx`——本阶段不挂。

**背景：** 现在删一份资料、改一个成员角色，成功后页面静默刷新，没有任何确认。用户不确定操作是否生效，往往再点一次。自建而不引第三方：需求就是「一句话 + 自动消失」，第三方库带来的是动画系统、位置策略、队列管理，全部用不上。

- [ ] **Step 1: 写失败的测试**

`frontend/src/components/ui/Toast.test.tsx`：

```tsx
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ToastProvider, useToast } from "./Toast";

afterEach(cleanup);
beforeEach(() => vi.useRealTimers());

function Harness() {
  const toast = useToast();
  return (
    <div>
      <button onClick={() => toast.success("已删除 报销制度.md")}>删除</button>
      <button onClick={() => toast.error("删除失败：资料正在索引")}>失败</button>
    </div>
  );
}

test("成功提示出现在 role=status 里", async () => {
  render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  expect(screen.getByRole("status")).toHaveTextContent("已删除 报销制度.md");
});

test("失败提示用 role=alert，会打断屏幕阅读器", async () => {
  render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );

  await userEvent.click(screen.getByRole("button", { name: "失败" }));
  // 成功是可以慢慢读的，失败必须立刻打断——两者的 ARIA 角色不同不是细节。
  expect(screen.getByRole("alert")).toHaveTextContent("删除失败：资料正在索引");
});

test("成功提示 4 秒后自动消失", async () => {
  vi.useFakeTimers();
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );

  await user.click(screen.getByRole("button", { name: "删除" }));
  expect(screen.getByRole("status")).toBeInTheDocument();

  act(() => void vi.advanceTimersByTime(4000));
  expect(screen.queryByRole("status")).toBeNull();
});

test("失败提示不自动消失，必须手动关闭", async () => {
  vi.useFakeTimers();
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );

  await user.click(screen.getByRole("button", { name: "失败" }));
  act(() => void vi.advanceTimersByTime(10_000));
  // 错误信息自动消失等于没说过——用户可能正在别处看，回头什么都没有。
  expect(screen.getByRole("alert")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "关闭提示" }));
  expect(screen.queryByRole("alert")).toBeNull();
});

test("Provider 之外调用 useToast 直接报错，而不是静默无效", () => {
  function Orphan() {
    useToast();
    return null;
  }
  // 静默无效意味着「写操作成功了但没提示」这种 bug 要靠人眼发现。
  expect(() => render(<Orphan />)).toThrow(/ToastProvider/);
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/Toast.test.tsx`
Expected: FAIL —— `Failed to resolve import "./Toast"`

- [ ] **Step 3: 实现**

`frontend/src/components/ui/Toast.tsx`：

```tsx
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "./cn";

/**
 * 写操作反馈。
 *
 * **自建而不引第三方。** 需求是「一句话 + 自动消失」；第三方 toast 库带来的是动画系统、
 * 位置策略、队列管理、promise 集成，全部用不上。
 *
 * **成功与失败的 ARIA 角色不同，这不是细节。** 成功用 `role="status"`（礼貌播报，
 * 不打断当前朗读），失败用 `role="alert"`（立刻打断）。失败还不自动消失——错误信息
 * 自动消失等于没说过，用户可能正在别处看，回头什么都没有。
 */
type ToastItem = { id: number; message: string; tone: "success" | "error" };

const ToastContext = createContext<{ success: (message: string) => void; error: (message: string) => void } | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const remove = useCallback((id: number) => setItems((current) => current.filter((item) => item.id !== id)), []);

  const push = useCallback(
    (message: string, tone: ToastItem["tone"]) => {
      // Date.now() 在同一毫秒内可能重复，用递增计数器保证 key 唯一。
      const id = nextId++;
      setItems((current) => [...current, { id, message, tone }]);
      if (tone === "success") window.setTimeout(() => remove(id), 4000);
    },
    [remove],
  );

  const value = useMemo(
    () => ({
      success: (message: string) => push(message, "success"),
      error: (message: string) => push(message, "error"),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="fixed bottom-5 right-5 z-50 grid gap-2">
        {items.map((item) => (
          <div
            key={item.id}
            role={item.tone === "success" ? "status" : "alert"}
            className={cn(
              "flex max-w-96 items-start gap-3 rounded-md border px-3 py-2 text-md shadow-pop",
              item.tone === "success"
                ? "border-success/25 bg-success-subtle text-success"
                : "border-danger/25 bg-danger-subtle text-danger-text",
            )}
          >
            <span className="flex-1">{item.message}</span>
            <button
              type="button"
              aria-label="关闭提示"
              onClick={() => remove(item.id)}
              // preflight 未启用，UA 的默认按钮边框还在。
              className="border-0 bg-transparent p-0.5 text-current opacity-70 hover:opacity-100"
            >
              <X size={13} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

let nextId = 1;

export function useToast() {
  const value = useContext(ToastContext);
  // 静默无效意味着「写操作成功了但没提示」这种 bug 只能靠人眼发现。
  if (!value) throw new Error("useToast 必须在 ToastProvider 之内使用");
  return value;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/Toast.test.tsx && npm test && npm run typecheck && npm run lint`
Expected: 5 条全 PASS。`npm run lint` 可能对 `ToastProvider` 与 `useToast` 同文件导出报 `react-refresh/only-export-components` 警告——它是 `warn` 级不阻塞，但如果想消掉，把 `useToast` 拆到 `useToast.ts` 并从 `Toast.tsx` 导入 context。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Toast.tsx frontend/src/components/ui/Toast.test.tsx
git commit -m "$(cat <<'EOF'
feat: 基座新增 Toast

写操作现在成功后页面静默刷新，用户不确定是否生效常会再点一次。
成功用 role=status 且 4 秒自动消失，失败用 role=alert 且必须手动关闭——
错误信息自动消失等于没说过。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: useConfirm

**Files:**
- Create: `frontend/src/components/ui/useConfirm.tsx`
- Test: `frontend/src/components/ui/useConfirm.test.tsx`

**Interfaces:**
- Consumes: `Dialog`、`DialogActions`、`Button`
- Produces:
  ```tsx
  export type ConfirmRequest = {
    title: string;
    /** 后果描述。必填。 */
    consequence: string;
    confirmLabel: string;
    tone?: "default" | "destructive";
    onConfirm: () => Promise<void>;
  };
  export function useConfirm(): {
    confirm: (request: ConfirmRequest) => void;
    dialog: ReactNode;
  }
  ```
  阶段 2 起，所有删除/停用/撤权操作消费它

**背景：** 现在有 5 处各写各的确认弹层（`KnowledgeBasesPage.tsx:47`、`DocumentPanel.tsx:133`、`MembersPage.tsx:262`、`KnowledgeBaseDetailPage.tsx:71` 等），文案质量参差：知识库删除写清了「会连带删除全部资料、索引与会话」，成员停用只说「会话可能随之失效」。把「后果」做成必填字段，抄的时候不可能漏。另外 `DocumentPanel.tsx:133` 的确认按钮带 `autoFocus`，回车直接删——统一去掉。

- [ ] **Step 1: 写失败的测试**

`frontend/src/components/ui/useConfirm.test.tsx`：

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { useConfirm } from "./useConfirm";

afterEach(cleanup);

function Harness({ onConfirm }: { onConfirm: () => Promise<void> }) {
  const { confirm, dialog } = useConfirm();
  return (
    <div>
      <button
        onClick={() =>
          confirm({
            title: "删除资料",
            consequence: "会同时删除原始文件和对应向量索引，删除后无法在当前知识库中检索。",
            confirmLabel: "确认删除",
            tone: "destructive",
            onConfirm,
          })
        }
      >
        删除
      </button>
      {dialog}
    </div>
  );
}

test("确认前后果文案必须出现在弹层里", async () => {
  render(<Harness onConfirm={async () => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  expect(await screen.findByRole("dialog")).toHaveTextContent(
    "会同时删除原始文件和对应向量索引",
  );
});

test("确认按钮不抢焦点，回车不会直接执行", async () => {
  const onConfirm = vi.fn(async () => {});
  render(<Harness onConfirm={onConfirm} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  await screen.findByRole("dialog");
  // DocumentPanel.tsx:133 现在给确认按钮加了 autoFocus，弹层一开回车就删。
  // 破坏性操作不该是一个回车的距离。
  expect(screen.getByRole("button", { name: "确认删除" })).not.toHaveFocus();

  // 初始焦点实际落在 Dialog 头部的关闭按钮（X）上——它在 DOM 顺序里先于
  // DialogActions，Radix 聚焦 Content 内第一个可聚焦元素。所以这里的回车
  // 是「关闭弹层」，不是「什么都没发生」。两条断言一起才说清了真实行为；
  // 只断言 onConfirm 未被调用会让下一个人以为焦点在取消按钮上。
  await userEvent.keyboard("{Enter}");
  expect(onConfirm).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("点确认才执行，执行期间两个按钮都锁住", async () => {
  let release: () => void = () => {};
  const onConfirm = vi.fn(() => new Promise<void>((resolve) => { release = resolve; }));
  render(<Harness onConfirm={onConfirm} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  await userEvent.click(await screen.findByRole("button", { name: "确认删除" }));

  expect(onConfirm).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: /确认删除/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /取消/ })).toBeDisabled();

  release();
});

test("取消不执行且关闭弹层", async () => {
  const onConfirm = vi.fn(async () => {});
  render(<Harness onConfirm={onConfirm} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  await userEvent.click(await screen.findByRole("button", { name: "取消" }));

  expect(onConfirm).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("destructive 的确认按钮用红底", async () => {
  render(<Harness onConfirm={async () => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  const confirmButton = await screen.findByRole("button", { name: "确认删除" });
  expect(confirmButton.className.split(/\s+/)).toContain("bg-danger");
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/useConfirm.test.tsx`
Expected: FAIL —— `Failed to resolve import "./useConfirm"`

- [ ] **Step 3: 实现**

`frontend/src/components/ui/useConfirm.tsx`：

```tsx
// 不 import ReactNode：`dialog` 的类型由 JSX 表达式推导，没有显式类型标注需要它。
import { useCallback, useState } from "react";
import { Button } from "./Button";
import { Dialog, DialogActions } from "./Dialog";

/**
 * 统一确认弹层。
 *
 * **`consequence` 是必填的。** 现在有 5 处各写各的确认弹层，文案质量参差：知识库删除
 * 写清了「会连带删除全部资料、索引与会话」，成员停用只说「会话可能随之失效」。
 * 做成必填字段，抄的时候不可能漏。
 *
 * **确认按钮不 autoFocus。** DocumentPanel 现在给它加了，弹层一开回车就删——
 * 破坏性操作不该是一个回车的距离。
 */
export type ConfirmRequest = {
  title: string;
  /** 后果描述。必填：用户要知道点下去会发生什么，而不只是「确认吗」。 */
  consequence: string;
  confirmLabel: string;
  tone?: "default" | "destructive";
  onConfirm: () => Promise<void>;
};

export function useConfirm() {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const [busy, setBusy] = useState(false);

  const confirm = useCallback((next: ConfirmRequest) => setRequest(next), []);

  const close = useCallback(() => {
    if (!busy) setRequest(null);
  }, [busy]);

  const run = useCallback(async () => {
    if (!request) return;
    setBusy(true);
    try {
      await request.onConfirm();
      setRequest(null);
    } finally {
      setBusy(false);
    }
  }, [request]);

  const dialog = request ? (
    <Dialog open title={request.title} onClose={close}>
      <p className="text-md text-ink-muted">{request.consequence}</p>
      <DialogActions>
        <Button variant="secondary" loading={busy} onClick={close}>
          取消
        </Button>
        <Button
          variant={request.tone === "destructive" ? "destructive" : "primary"}
          loading={busy}
          onClick={() => void run()}
        >
          {request.confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  ) : null;

  return { confirm, dialog };
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/useConfirm.test.tsx && npm test && npm run typecheck`
Expected: 5 条全 PASS。若「回车不会直接执行」这条失败，检查 `Dialog` 的初始焦点——Radix 默认把焦点给 Content 内第一个可聚焦元素，即「取消」按钮，这正是想要的顺序（取消在左、确认在右）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/useConfirm.tsx frontend/src/components/ui/useConfirm.test.tsx
git commit -m "$(cat <<'EOF'
feat: 基座新增 useConfirm

consequence 做成必填字段，收敛 5 处文案质量参差的确认弹层。
确认按钮去掉 autoFocus——破坏性操作不该是一个回车的距离。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: Button 的 blockedReason 接受多个原因

**Files:**
- Modify: `frontend/src/components/ui/Button.tsx:58,81,89,91,96`
- Modify: `frontend/src/components/ui/Button.test.tsx`（追加，不改动现有 8 条）

**Interfaces:**
- Consumes: 无
- Produces: `blockedReason?: string | string[]`。单字符串行为逐字节不变；数组时全部列出

**背景：** `DocumentPanel.tsx:107` 的「应用到 N 份」有两个禁用原因（没勾资料 / 没选分类），现在用三元表达式只能显示第一个。勾了 3 份没选分类时，用户看到「应用到 3 份」是灰的，`title` 里写着「请先选择目标分类」——但 N 已经变成 3 了，文案自带的解释失效。

**本任务是纯新增**：不改渲染方式（Tooltip + `ⓘ` 是阶段 2 的事），只放宽类型。因此视觉基线不受影响。

- [ ] **Step 1: 追加失败的测试**

在 `frontend/src/components/ui/Button.test.tsx` 末尾追加：

```tsx
test("多个原因全部列出，不再只显示第一个", () => {
  // DocumentPanel 的「应用到 N 份」有两个禁用条件，三元表达式只能显示第一个。
  // 勾了 3 份没选分类时，文案里的 3 已经不解释任何事了。
  render(<Button blockedReason={["请先勾选资料", "请先选择目标分类"]}>应用到 0 份</Button>);

  expect(screen.getByRole("button", { name: /应用到/ })).toBeDisabled();
  expect(screen.getByText("请先勾选资料")).toBeVisible();
  expect(screen.getByText("请先选择目标分类")).toBeVisible();
});

test("多个原因在 title 里用顿号连接", () => {
  render(<Button blockedReason={["请先勾选资料", "请先选择目标分类"]}>应用到 0 份</Button>);

  expect(screen.getByRole("button", { name: /应用到/ })).toHaveAttribute(
    "title",
    "请先勾选资料、请先选择目标分类",
  );
});

test("空数组等于没有原因，按钮可用", () => {
  // 调用方常写 `blockedReason={reasons}`，而 reasons 是 filter 出来的。
  // 空数组必须等价于 undefined，否则「条件都满足了按钮还是灰的」。
  render(<Button blockedReason={[]}>提交</Button>);

  expect(screen.getByRole("button", { name: "提交" })).toBeEnabled();
});

test("单字符串行为完全不变", () => {
  render(<Button blockedReason="默认知识库不能删除">删除</Button>);

  const button = screen.getByRole("button", { name: /删除/ });
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("title", "默认知识库不能删除");
  expect(screen.getByText("默认知识库不能删除")).toBeVisible();
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && npx vitest run src/components/ui/Button.test.tsx`
Expected: 新增 4 条中至少「多个原因全部列出」和「空数组等于没有原因」FAIL；现有 8 条仍 PASS。

- [ ] **Step 3: 修改 Button**

`frontend/src/components/ui/Button.tsx`，三处改动：

类型（原第 58 行）：

```tsx
    /**
     * 为什么点不了。**给了就禁用，没给就可用**——不存在「禁用但没有原因」这种状态。
     *
     * 真实代码里的禁用条件几乎都是动态的（`count > 0 ? "请先迁移资料" : undefined`），
     * 所以做成可选字符串而不是 `disabled` + `reason` 的联合类型：后者只接受字面量
     * `true`，遇到 `disabled={a || b}` 这种表达式直接编译不过。
     *
     * 接受数组是因为一个按钮可能同时被多个条件挡住——「应用到 N 份」既要求勾选资料
     * 又要求选中目标分类，三元表达式只能说出第一个。**空数组等价于 undefined**：
     * 调用方常写 `blockedReason={reasons}` 而 reasons 是 filter 出来的，
     * 不这么定义就会出现「条件都满足了按钮还是灰的」。
     */
    blockedReason?: string | string[];
```

函数体（原第 81 行起）：

```tsx
  const reasons = blockedReason === undefined ? [] : Array.isArray(blockedReason) ? blockedReason : [blockedReason];
  const blocked = reasons.length > 0;
  const title = loading ? "处理中…" : blocked ? reasons.join("、") : undefined;
```

渲染部分（原第 89、91、96 行）：

```tsx
        disabled={blocked || loading}
        aria-busy={loading || undefined}
        title={title}
      >
        {children}
      </button>
      {/* 原因要看得见，不能只躺在 title 里。loading 是短暂状态，不额外占位。 */}
      {!loading && blocked && !reasonHidden
        ? reasons.map((reason) => (
            <small key={reason} className="block text-xs text-ink-faint">
              {reason}
            </small>
          ))
        : null}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/ui/Button.test.tsx && npm test && npm run typecheck && npm run build`
Expected: 12 条全 PASS（原 8 条 + 新 4 条）。原 8 条一条都不能改——单字符串行为必须逐字节不变。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Button.tsx frontend/src/components/ui/Button.test.tsx
git commit -m "$(cat <<'EOF'
feat: blockedReason 接受多个原因

「应用到 N 份」同时被「没勾资料」和「没选分类」两个条件挡住，
三元表达式只能说出第一个。空数组等价于 undefined，避免调用方
filter 出空列表后按钮仍然是灰的。单字符串行为不变。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: ESLint 禁止裸元素（warn 级）

**Files:**
- Modify: `frontend/eslint.config.js`

**Interfaces:**
- Consumes: 无
- Produces: 一条会在阶段 2-4 期间持续提示、阶段 5 提为 `error` 的约束

**背景：** `CLAUDE.md` 第五条——加了新的一致性约束，就顺手加上能自动发现它被破坏的检查。基座建好之后，判断「有没有人绕过基座」不能靠人眼 review。

**为什么是 warn 而不是 error：** 阶段 2-4 期间页面还没迁完，设成 `error` 会让 `npm run lint` 一直红。红色警报久了就没人看，等于没有。阶段 5 全部迁完后提为 `error`。

- [ ] **Step 1: 先确认规则会命中现有代码**

```bash
cd frontend && grep -c "<table" src/components/KnowledgeBasesPage.tsx src/components/DocumentPanel.tsx
```

Expected: 两个文件各至少 1 处——这些正是规则该报出来的。

- [ ] **Step 2: 加规则**

`frontend/eslint.config.js`，在现有 config 数组末尾追加一段：

```js
  {
    // 基座建好之后，「有没有人绕过基座」不能靠人眼 review。
    // 阶段 2-4 期间页面还没迁完，设成 error 会让 lint 一直红——红久了就没人看，
    // 等于没有。阶段 5 全部迁完后把 warn 改成 error。
    files: ["src/components/**/*.tsx"],
    ignores: ["src/components/ui/**"], // 基座内部必须写裸元素
    rules: {
      "no-restricted-syntax": [
        "warn",
        {
          selector: "JSXOpeningElement[name.name='table']",
          message: "表格请用 components/ui/DataTable，它保证行高、分隔线、列宽和空态一致。",
        },
        {
          // 用 :has 而不是 attributes.0——把 type 硬编码成第 0 个属性，
          // 换个书写顺序规则就静默失效。Step 3 要求实际看到 warning 输出，
          // 若这版 esquery 不支持 :has，那一步会当场暴露。
          selector:
            "JSXOpeningElement[name.name='input']:has(JSXAttribute[name.name='type'][value.value='checkbox'])",
          message: "复选框请用 components/ui/Checkbox，原生 checkbox 无法声明式表达 indeterminate。",
        },
        {
          selector: "JSXOpeningElement[name.name='button']",
          message: "按钮请用 components/ui/Button，它保证禁用必有可见原因（CLAUDE.md 第一条）。",
        },
      ],
    },
  },
```

- [ ] **Step 3: 跑 lint 确认规则生效且只是警告**

```bash
cd frontend && npm run lint
```

Expected: 输出若干 `warning` 指向 `KnowledgeBasesPage.tsx`、`DocumentPanel.tsx`、`MembersPage.tsx`、`OverviewPage.tsx` 等文件的裸元素；**退出码 0**（warn 不阻塞）。若退出码非 0，检查是不是写成了 `"error"`。

- [ ] **Step 4: 确认 ui/ 目录被豁免**

```bash
cd frontend && npx eslint src/components/ui/Button.tsx
```

Expected: 无输出。`Button.tsx` 内部渲染裸 `<button>`，被 `ignores` 排除掉。

- [ ] **Step 5: Commit**

```bash
git add frontend/eslint.config.js
git commit -m "$(cat <<'EOF'
chore: 加裸元素禁令，先设为 warn

基座建好后「有没有人绕过基座」不能靠人眼 review。阶段 2-4 页面还没迁完，
设成 error 会让 lint 一直红、红久了没人看，阶段 5 迁完再提为 error。
ui/ 目录豁免——基座内部必须写裸元素。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 18: 阶段验收

**Files:** 无改动。这是一道门，不是一次修改。

**Interfaces:**
- Consumes: Task 1-17 的全部产出
- Produces: 阶段 1 无副作用的证据，以及阶段 2 的准入

- [ ] **Step 1: 全量验证**

```bash
cd frontend && npm test && npm run lint && npm run typecheck && npm run build
```

Expected: 测试全绿、lint 退出码 0（有 warn）、typecheck 无错、build 成功。把实际输出贴出来，不要用「应该能过」代替。

- [ ] **Step 2: 确认基座覆盖了 13 个组件**

```bash
cd frontend && ls src/components/ui/*.tsx | grep -v test | wc -l
```

Expected: 18（原有 5 个 + 新增 13 个：Tooltip、Badge、Checkbox、Skeleton、EmptyState、Pagination、RowActions、DataTable、Toolbar、MetricCard、Tabs、Toast、useConfirm）。

- [ ] **Step 3: 确认没有碰过页面文件**

```bash
cd /Users/ls/Desktop/demo/rag-enterprise && git diff --stat <阶段1第一个commit>^..HEAD -- frontend/src/components/*.tsx
```

Expected: 空输出。阶段 1 只改 `ui/` 目录、`test-setup.ts`、`eslint.config.js`、`package.json`。任何页面文件出现在这里都说明范围失控。

- [ ] **Step 4: 跑视觉基线，确认 17 张全绿**

先补脚本的写风险防护——`visual-baseline.spec.ts` 曾在 2026-08-30 把 reader 误改成管理员，成因是 topbar 在 portal 挂载时重排导致误点。在点击「新建成员」之前已有 `settled(page)`，确认它还在；如果 review 时发现哪一处点击前没有等待，补上。

```bash
cd frontend
SMOKE_ADMIN_USERNAME=... SMOKE_ADMIN_PASSWORD=... \
  npx playwright test visual-baseline --project=desktop-chromium
```

Expected: 17 张截图全部匹配，0 diff。

**这是阶段 1 唯一能证明基座无副作用的证据。** 有任何一张红，先查是不是碰了不该碰的东西，而不是直接 `--update-snapshots`。

- [ ] **Step 5: 汇报并请示进入阶段 2**

把上面四步的实际输出汇总，说明阶段 1 完成。阶段 2 的计划另行编写——它依赖基座的实际 API，现在写只能靠猜。

---

## 自检记录

**Spec 覆盖：** spec「组件基座」表里的 13 个组件对应 Task 3-15；「交互规则的编码化」第 2 条（多原因）对应 Task 16、第 6 条（ESLint）对应 Task 17；第 1、3、4、5 条落在阶段 2（spec 已注明）。「质感参数」表中的行高、分隔线、`tabular-nums` 编码进 Task 10 的 `DataTable`，装饰色收敛编码进 Task 12 的 `MetricCard`——它们在阶段 2 页面迁移时才会体现在页面上。依赖切换对应 Task 1。

**未覆盖且有意为之：** spec 提到的 `@theme` token 调整——核查后现有 token 已覆盖本阶段所需，不新增。已在「文件结构」小节记录。

**类型一致性核对：** `EmptyStateProps`（Task 7 定义）被 Task 10 的 `DataTable.emptyState` 消费，字段名 `kind`/`title`/`description`/`action` 一致；`Column<T>`（Task 10）的 `numeric` 与测试断言的 `tabular-nums`/`text-right` 一致；`RowAction`（Task 9）的 `blockedReason` 是 `string`，与 Task 16 放宽后的 `string | string[]` 兼容（`string` 是其子类型）；`SkeletonRows(rows, columns)`（Task 6）被 Task 10 以 `rows={3} columns={columnCount}` 调用，参数名一致。
