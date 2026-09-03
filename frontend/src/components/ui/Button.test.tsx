import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Button } from "./Button";

afterEach(cleanup);

test("六个 variant 各自渲染，且只有 primary 用实底品牌色", () => {
  const variants = ["primary", "secondary", "outline", "ghost", "destructive", "link"] as const;
  render(
    <div>
      {variants.map((v) => (
        <Button key={v} variant={v}>{v}</Button>
      ))}
    </div>,
  );

  // 按 token 切分再比对：`hover:bg-brand-subtle` 含有 "bg-brand" 子串，
  // 用 toContain 做字符串匹配会把它误判成实底品牌色。
  const classOf = (v: string) =>
    screen.getByRole("button", { name: v }).className.split(/\s+/);
  expect(classOf("primary")).toContain("bg-brand");
  expect(classOf("destructive")).toContain("bg-danger");
  // 次要操作不得抢主操作的视觉权重。
  for (const v of ["secondary", "outline", "ghost", "link"]) {
    expect(classOf(v)).not.toContain("bg-brand");
    expect(classOf(v)).not.toContain("bg-danger");
  }
});

test("只有该有边框的 variant 才有边框", () => {
  // Tailwind preflight 还没启用（迁移完成前不能加，见 tailwind.css），所以浏览器给
  // `<button>` 的默认 `border: 1px outset` 仍然生效。以前它被 `.table-actions button
  // { border: 0 }` 之类的遗留 reset 压着，那些 class 清干净之后 ghost / primary 按钮
  // 就冒出了一圈灰边。基础样式必须自己声明 border-0。
  render(
    <div>
      <Button variant="ghost">ghost</Button>
      <Button variant="primary">primary</Button>
      <Button variant="secondary">secondary</Button>
      <Button variant="outline">outline</Button>
    </div>,
  );

  const classOf = (v: string) => screen.getByRole("button", { name: v }).className.split(/\s+/);
  expect(classOf("ghost")).toContain("border-0");
  expect(classOf("primary")).toContain("border-0");
  // 有边框的两个：tailwind-merge 必须让 variant 的 border 覆盖掉基础的 border-0，
  // 而不是两条并存。
  expect(classOf("secondary")).toContain("border");
  expect(classOf("secondary")).not.toContain("border-0");
  expect(classOf("outline")).toContain("border");
  expect(classOf("outline")).not.toContain("border-0");
});

test("给了原因才禁用，原因由 title 与独立的 ⓘ 双重呈现", async () => {
  // CLAUDE.md 第一条的编码化：组件不暴露 disabled，禁用只能通过 blockedReason 表达，
  // 于是「点不动又不解释自己的按钮」在类型层面就不存在。
  render(<Button blockedReason="默认知识库不能删除">删除</Button>);

  // 精确匹配按钮文案：正则 /删除/ 会连带匹配到 ⓘ 的 aria-label（其中含有
  // 「不能删除」），两个可用元素同名会让查询直接抛错。
  const button = screen.getByRole("button", { name: "删除" });
  expect(button).toBeDisabled();
  // 鼠标用户悬停主按钮也能看到，两条路都通。
  expect(button).toHaveAttribute("title", "默认知识库不能删除");
});

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

test("loading 期间禁止重复提交", async () => {
  const onClick = vi.fn();
  const { rerender } = render(<Button onClick={onClick}>保存</Button>);

  await userEvent.click(screen.getByRole("button", { name: "保存" }));
  expect(onClick).toHaveBeenCalledTimes(1);

  rerender(<Button loading onClick={onClick}>保存</Button>);
  const button = screen.getByRole("button", { name: /保存/ });
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("aria-busy", "true");

  await userEvent.click(button, { pointerEventsCheck: 0 });
  expect(onClick).toHaveBeenCalledTimes(1);
});

test("loading 不需要显式原因，组件自己解释", () => {
  render(<Button loading>保存</Button>);

  expect(screen.getByRole("button", { name: /保存/ })).toHaveAttribute("title", "处理中…");
});

test("尺寸档位落在 token 定义的高度上", () => {
  render(
    <div>
      <Button size="sm">sm</Button>
      <Button size="md">md</Button>
      <Button size="lg">lg</Button>
      <Button size="icon" aria-label="仅图标">×</Button>
    </div>,
  );

  expect(screen.getByRole("button", { name: "sm" }).className).toContain("h-7");
  expect(screen.getByRole("button", { name: "md" }).className).toContain("h-9");
  expect(screen.getByRole("button", { name: "lg" }).className).toContain("h-11");
  expect(screen.getByRole("button", { name: "仅图标" }).className).toContain("w-8");
});

test("外部 className 能覆盖内部同类样式而不是叠加冲突", () => {
  // tailwind-merge 的价值就在这里：不做合并的话 `bg-brand bg-transparent` 两条都在，
  // 最终效果取决于 CSS 里的先后顺序，调用方无法预测。
  render(<Button className="bg-transparent">自定义</Button>);

  const cls = screen.getByRole("button", { name: "自定义" }).className.split(/\s+/);
  expect(cls).toContain("bg-transparent");
  expect(cls).not.toContain("bg-brand");
});

test("多个原因全部列出，不再只显示第一个", () => {
  // DocumentPanel 的「应用到 N 份」有两个禁用条件，三元表达式只能显示第一个。
  // 勾了 3 份没选分类时，文案里的 3 已经不解释任何事了。
  render(<Button blockedReason={["请先勾选资料", "请先选择目标分类"]}>应用到 0 份</Button>);

  expect(screen.getByRole("button", { name: /应用到/ })).toBeDisabled();
  const hint = screen.getByRole("button", { name: /为什么不可用/ });
  expect(hint).toHaveAccessibleName(/请先勾选资料/);
  expect(hint).toHaveAccessibleName(/请先选择目标分类/);
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

  const button = screen.getByRole("button", { name: "删除" });
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("title", "默认知识库不能删除");
  expect(screen.getByRole("button", { name: /默认知识库不能删除/ })).toBeEnabled();
});
