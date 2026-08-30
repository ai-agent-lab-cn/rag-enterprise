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
