import { cleanup, render, screen, within } from "@testing-library/react";
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

test("批量区的按钮不在 live region 内", () => {
  render(<Toolbar batch={{ count: 2, children: <button>删除 2 项</button> }} />);

  // live region 里放 focusable 元素是反模式，更要命的是：批量操作完成后 count 归 0，
  // 整个区域会被卸载——如果按钮在 live region 里，它连同焦点一起消失，焦点掉回 body。
  const status = screen.getByRole("status");
  expect(status).toHaveTextContent("已选 2 项");
  expect(within(status).queryByRole("button")).toBeNull();
});
