import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
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

test("三个及以上操作直接平铺，不展示更多操作菜单", () => {
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

  expect(screen.getByRole("button", { name: "重新分类" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "编辑" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "删除" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /更多操作/ })).toBeNull();
});

test("destructive 项直接展示并固定排在最后", () => {
  const { container } = render(
    <RowActions
      rowLabel="报销制度.md"
      actions={[
        { label: "删除", onSelect: () => {}, tone: "destructive" },
        { label: "重新分类", onSelect: () => {} },
        { label: "编辑", onSelect: () => {} },
      ]}
    />,
  );

  const items = Array.from(container.querySelectorAll("button")).map((item) => item.textContent);
  expect(items).toEqual(["编辑", "重新分类", "删除"]);
  expect(screen.queryByRole("separator")).toBeNull();
});

test("三个操作平铺时禁用项仍说得出原因", async () => {
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

  const remove = screen.getByRole("button", { name: "删除" });
  expect(remove).toBeDisabled();
  const hint = screen.getByRole("button", { name: /默认知识库不能删除/ });
  await userEvent.hover(hint);
  expect((await screen.findAllByText("默认知识库不能删除")).length).toBeGreaterThan(0);
});

test("平铺形态下的禁用原因由独立的 ⓘ 承载，不是只躺在 title 里", async () => {
  // CLAUDE.md 第一条：禁用原因必须可见，原生 title 要悬停约一秒才出现，
  // 触屏上根本看不到。菜单形态（上一条测试）原因内联可见，平铺形态不能是例外。
  //
  // 原因不再是按钮下方的块级小字——那会把表格行撑高，违背 DataTable 行等高的前提。
  // ⓘ 必须是独立的、自己可用的元素：真实浏览器里 disabled 的 button 不派发
  // pointerenter，把 Tooltip 包在禁用按钮外面在 jsdom 里会绿，浏览器里永远弹不出来。
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
  // title 可以保留，但不能是唯一断言。
  expect(remove).toHaveAttribute("title", "默认知识库不能删除");
  expect(screen.queryByText("默认知识库不能删除")).toBeNull();

  const hint = screen.getByRole("button", { name: /默认知识库不能删除/ });
  expect(hint).not.toBe(remove);
  expect(hint).toBeEnabled();

  await userEvent.hover(hint);
  expect((await screen.findAllByText("默认知识库不能删除")).length).toBeGreaterThan(0);
});

const file = (name: string) => new File(["x"], name, { type: "text/plain" });

test("平铺形态下，file 类型的操作渲染 FileButton 并能选出文件", async () => {
  const onSelect = vi.fn();
  render(
    <RowActions
      rowLabel="报销制度.md"
      actions={[
        { label: "更新文件", file: { accept: ".md", onSelect } },
        { label: "详情", onSelect: () => {} },
      ]}
    />,
  );

  // ≤2 个操作走平铺分支，file 类型渲染的是 FileButton 而不是普通 Button。
  expect(screen.getByRole("button", { name: "更新文件" })).toBeInTheDocument();
  const input = screen.getByLabelText("报销制度.md 的更新文件") as HTMLInputElement;
  await userEvent.upload(input, file("a.md"));

  expect(onSelect).toHaveBeenCalledWith([expect.objectContaining({ name: "a.md" })]);
});

// 之前这里是 test.fails：file 类型的隐藏 input 曾经渲染在 DropdownMenu.Item 里，
// Radix 的 Item 默认点击即关闭菜单（Portal 卸载），选文件时 input 已经从文档树
// 摘除，change 事件没有委托监听能收到，onSelect 回调永远不会触发——用
// console.log(input.isConnected) 实测过（点击后立刻变 false），也用真实 Chromium
// + Playwright 复现过（filechooser 正常弹出，但 setFiles 后回调没有执行）。
//
// 修复方式：把 file 类型的隐藏 input 挪到 DropdownMenu.Root 外面、菜单开关管不到
// 的根容器里，菜单项点击时只是把已经挂好的 input 点开（input.click()）。这样
// input 的生命周期跟菜单开关脱钩，菜单关不关都不影响它。
//
// 这个改法也用真实 Chromium 验证过用户手势会不会因为经过 Radix 的
// dispatchDiscreteCustomEvent（内部用 ReactDOM.flushSync 同步派发）而丢失：
// filechooser 正常弹出，setFiles 后回调执行，关闭菜单重开后二次选择依旧正常。
test("三个操作平铺时，file 类型操作可直接选择文件", async () => {
  const onSelect = vi.fn();
  render(
    <RowActions
      rowLabel="报销制度.md"
      actions={[
        { label: "更新文件", file: { accept: ".md", onSelect } },
        { label: "编辑", onSelect: () => {} },
        { label: "删除", onSelect: () => {}, tone: "destructive" },
      ]}
    />,
  );

  const input = screen.getByLabelText("报销制度.md 的更新文件") as HTMLInputElement;
  expect(screen.getByRole("button", { name: "更新文件" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /更多操作/ })).toBeNull();
  await userEvent.upload(input, file("a.md"));

  expect(onSelect).toHaveBeenCalledWith([expect.objectContaining({ name: "a.md" })]);
});

test("没有可用操作时整个组件不渲染", () => {
  const { container } = render(<RowActions rowLabel="企业知识库" actions={[]} />);

  expect(container).toBeEmptyDOMElement();
});
