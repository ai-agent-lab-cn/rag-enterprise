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
