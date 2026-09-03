import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { FileButton } from "./FileButton";

afterEach(cleanup);

const file = (name: string) => new File(["x"], name, { type: "text/plain" });

test("和 Button 走同一套尺寸，不再是自己一套高度", () => {
  // 迁移前文件选择器是 `<label className="primary-action">`（min-height 36px）和
  // `.table-file-action`（padding 3px/font 11px），跟旁边 28px 的 Select、Button 高低不齐。
  render(
    <div>
      <FileButton size="sm" onSelect={() => {}}>小</FileButton>
      <FileButton size="md" onSelect={() => {}}>中</FileButton>
    </div>,
  );

  expect(screen.getByRole("button", { name: "小" }).className).toContain("h-7");
  expect(screen.getByRole("button", { name: "中" }).className).toContain("h-9");
});

test("点按钮就打开文件选择", async () => {
  render(<FileButton onSelect={() => {}}>上传</FileButton>);

  const input = screen.getByLabelText("上传") as HTMLInputElement;
  const opened = vi.fn();
  input.addEventListener("click", opened);

  await userEvent.click(screen.getByRole("button", { name: "上传" }));
  expect(opened).toHaveBeenCalledTimes(1);
});

test("选中文件后交出 File 列表，并清空输入以便重选同名文件", async () => {
  const onSelect = vi.fn();
  render(<FileButton onSelect={onSelect}>上传</FileButton>);

  const input = screen.getByLabelText("上传") as HTMLInputElement;
  await userEvent.upload(input, file("a.md"));

  expect(onSelect).toHaveBeenCalledWith([expect.objectContaining({ name: "a.md" })]);
  // 不清空的话，连续两次选同一个文件不会触发 change，用户会以为按钮坏了。
  expect(input.value).toBe("");
});

test("multiple 才收多个文件", async () => {
  const onSelect = vi.fn();
  render(<FileButton multiple onSelect={onSelect}>批量上传</FileButton>);

  const input = screen.getByLabelText("批量上传") as HTMLInputElement;
  expect(input.multiple).toBe(true);
  await userEvent.upload(input, [file("a.md"), file("b.md")]);

  expect(onSelect.mock.calls[0][0]).toHaveLength(2);
});

test("blockedReason 同时挡住按钮和隐藏输入", async () => {
  const onSelect = vi.fn();
  render(<FileButton blockedReason="索引进行中" onSelect={onSelect}>更新文件</FileButton>);

  expect(screen.getByRole("button", { name: /更新文件/ })).toBeDisabled();
  // 输入框也必须禁用：它虽然不可见，但仍在无障碍树里，只禁按钮等于留了一条后门。
  expect(screen.getByLabelText("更新文件")).toBeDisabled();
});

test("表格行内可以覆盖无障碍名称，区分操作对象", () => {
  // 一张表里十行「更新文件」，读屏听到的十个名字必须不一样。
  render(<FileButton inputLabel="更新 报销制度.md" onSelect={() => {}}>更新文件</FileButton>);

  expect(screen.getByLabelText("更新 报销制度.md")).toBeInstanceOf(HTMLInputElement);
});

test("空数组等于没有原因，按钮可用", () => {
  // 与 Button 同一套语义（normalizeBlockedReason）。两个控件的禁用逻辑一旦漂移，
  // 页面上就会出现「同样的条件，一个点得动一个点不动」。
  render(<FileButton blockedReason={[]} onSelect={() => {}}>上传</FileButton>);

  expect(screen.getByRole("button", { name: /上传/ })).toBeEnabled();
});

test("多个原因全部列出，且隐藏输入也一并禁用", () => {
  render(
    <FileButton blockedReason={["请先选择知识库", "请先选择分类"]} onSelect={() => {}}>
      上传
    </FileButton>,
  );

  const button = screen.getByRole("button", { name: "上传" });
  expect(button).toBeDisabled();
  const hint = screen.getByRole("button", { name: /为什么不可用/ });
  expect(hint).toHaveAccessibleName(/请先选择知识库/);
  expect(hint).toHaveAccessibleName(/请先选择分类/);
  // 输入框也必须禁用：它虽然不可见，但仍在无障碍树里，只禁按钮等于留了一条后门。
  expect(screen.getByLabelText("上传")).toBeDisabled();
});

test("禁用原因由独立的 ⓘ 承载，不占据行高", () => {
  render(<FileButton blockedReason="索引进行中" onSelect={() => {}}>更新文件</FileButton>);

  const action = screen.getByRole("button", { name: "更新文件" });
  expect(action).toBeDisabled();

  // 原因不再是按钮下方的块级小字——那会把表格行撑高。
  expect(screen.queryByText("索引进行中")).toBeNull();

  // 取而代之的是一个独立的、**可用的** ⓘ：真实浏览器里 disabled 的 button 不派发
  // pointerenter，把 Tooltip 包在禁用按钮外面在 jsdom 里会绿，浏览器里永远弹不出来。
  const hint = screen.getByRole("button", { name: /索引进行中/ });
  expect(hint).not.toBe(action);
  expect(hint).toBeEnabled();
});

test("ⓘ 悬停后弹出原因", async () => {
  render(<FileButton blockedReason="索引进行中" onSelect={() => {}}>更新文件</FileButton>);

  await userEvent.hover(screen.getByRole("button", { name: /索引进行中/ }));
  expect((await screen.findAllByText("索引进行中")).length).toBeGreaterThan(0);
});

test("没有原因时不渲染 ⓘ", () => {
  render(<FileButton onSelect={() => {}}>上传</FileButton>);

  expect(screen.getAllByRole("button")).toHaveLength(1);
});
