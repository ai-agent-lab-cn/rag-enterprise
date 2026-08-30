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

test("给了原因才禁用，且原因可见", async () => {
  // CLAUDE.md 第一条的编码化：组件不暴露 disabled，禁用只能通过 blockedReason 表达，
  // 于是「点不动又不解释自己的按钮」在类型层面就不存在。
  render(<Button blockedReason="默认知识库不能删除">删除</Button>);

  const button = screen.getByRole("button", { name: /删除/ });
  expect(button).toBeDisabled();
  expect(screen.getByText("默认知识库不能删除")).toBeVisible();
  // 同时保留 title：鼠标用户悬停也能看到，两条路都通。
  expect(button).toHaveAttribute("title", "默认知识库不能删除");
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

test("reasonHidden 只藏可见小字，禁用与 title 都还在", () => {
  // 用于「应用到 0 份」这类文案自带原因的按钮：再补一句提示只是重复，还撑开布局。
  render(<Button reasonHidden blockedReason="请先勾选资料">应用到 0 份</Button>);

  const button = screen.getByRole("button", { name: /应用到/ });
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("title", "请先勾选资料");
  expect(screen.queryByText("请先勾选资料")).toBeNull();
});
