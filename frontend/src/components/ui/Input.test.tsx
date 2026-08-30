import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";
import { Input, Textarea } from "./Input";

afterEach(cleanup);

test("高度与 Button 对齐，同一行不会高低不齐", () => {
  // 这正是提前做 Input 的原因：解析面板里「重新处理」(h-7) 挨着两个旧输入框
  // (min-height 40px)，一眼能看出错位。
  render(
    <div>
      <Input aria-label="小" size="sm" />
      <Input aria-label="默认" />
    </div>,
  );

  expect(screen.getByLabelText("小").className.split(/\s+/)).toContain("h-7");
  expect(screen.getByLabelText("默认").className.split(/\s+/)).toContain("h-9");
});

test("保持原生输入行为", async () => {
  render(<Input aria-label="名称" defaultValue="" />);

  await userEvent.type(screen.getByLabelText("名称"), "运维文档");

  expect(screen.getByLabelText("名称")).toHaveValue("运维文档");
});

test("出错时给出可辨识边框与无障碍标记", () => {
  render(<Input aria-label="必填" invalid />);

  const input = screen.getByLabelText("必填");
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input.className.split(/\s+/)).toContain("border-danger");
});

test("多行输入高度由 rows 决定，不套固定高度", () => {
  render(<Textarea aria-label="描述" rows={5} />);

  const textarea = screen.getByLabelText("描述");
  expect(textarea).toHaveAttribute("rows", "5");
  expect(textarea.className.split(/\s+/)).not.toContain("h-9");
});

test("外部 className 能覆盖内部同类样式", () => {
  render(<Input aria-label="宽" className="h-11" />);

  const cls = screen.getByLabelText("宽").className.split(/\s+/);
  expect(cls).toContain("h-11");
  expect(cls).not.toContain("h-9");
});
