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

test("showLabel 把 label 渲染成可见文案，可访问名与它同源", () => {
  render(<Checkbox checked={false} onCheckedChange={() => {}} label="使用 HTTPS" showLabel />);

  // 同一个字符串既是可见文案也是可访问名，不可能分叉（CLAUDE.md 第一条同源原则）。
  expect(screen.getByText("使用 HTTPS")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "使用 HTTPS" })).toBeInTheDocument();
});

test("showLabel 下点击可见文案也能切换", async () => {
  const onCheckedChange = vi.fn();
  render(<Checkbox checked={false} onCheckedChange={onCheckedChange} label="应用默认分类模板" showLabel />);

  // 原生 <input type=checkbox> 包在 <label> 里时点文字能切换，替换成 Radix（渲染成
  // <button>）后必须保住这个行为——button 也是 labelable element，隐式关联仍成立。
  await userEvent.click(screen.getByText("应用默认分类模板"));
  expect(onCheckedChange).toHaveBeenCalledWith(true);
});

test("不传 showLabel 时不产生可见文案", () => {
  render(<Checkbox checked={false} onCheckedChange={() => {}} label="选择全部资料" />);

  // 表格里的 checkbox 靠 aria-label，多渲染一段文字会把列宽撑开。
  expect(screen.queryByText("选择全部资料")).toBeNull();
});
