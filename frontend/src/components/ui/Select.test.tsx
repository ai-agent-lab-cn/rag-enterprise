import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Select } from "./Select";

afterEach(cleanup);

test("保持原生 select 的行为，迁移只是换样式", async () => {
  // 有意包装原生 <select> 而不是用 Radix 重写：它语义正确、键盘可用、移动端还能唤起
  // 系统选择器。保持原生 API 也让 21 处迁移变成纯样式替换，不必改调用方逻辑。
  const onChange = vi.fn();
  render(
    <Select aria-label="分类筛选" value="" onChange={onChange}>
      <option value="">全部分类</option>
      <option value="cat_1">制度规范</option>
    </Select>,
  );

  const select = screen.getByLabelText("分类筛选");
  expect(select.tagName).toBe("SELECT");
  await userEvent.selectOptions(select, "cat_1");
  expect(onChange).toHaveBeenCalled();
});

test("尺寸与按钮对齐，放在同一行工具栏里不会高低不齐", () => {
  render(
    <div>
      <Select aria-label="小" size="sm"><option>a</option></Select>
      <Select aria-label="默认"><option>a</option></Select>
    </div>,
  );

  expect(screen.getByLabelText("小").className.split(/\s+/)).toContain("h-7");
  expect(screen.getByLabelText("默认").className.split(/\s+/)).toContain("h-9");
});

test("出错时给出可辨识的边框与无障碍标记", () => {
  render(<Select aria-label="必填项" invalid><option>a</option></Select>);

  const select = screen.getByLabelText("必填项");
  expect(select).toHaveAttribute("aria-invalid", "true");
  expect(select.className.split(/\s+/)).toContain("border-danger");
});

test("禁用时必须给出可见原因", () => {
  // 与 Button 同一条规则：点不动的控件必须解释自己。
  render(
    <Select aria-label="目标分类" blockedReason="请先建立分类">
      <option>a</option>
    </Select>,
  );

  expect(screen.getByLabelText("目标分类")).toBeDisabled();
  expect(screen.getByText("请先建立分类")).toBeVisible();
});

test("外部 className 能覆盖内部同类样式", () => {
  render(<Select aria-label="宽" className="h-11"><option>a</option></Select>);

  const cls = screen.getByLabelText("宽").className.split(/\s+/);
  expect(cls).toContain("h-11");
  expect(cls).not.toContain("h-9");
});

test("appearance-none 之后必须给回一个下拉箭头", () => {
  // 去掉系统箭头却不给替代品，下拉框看起来就是个普通输入框——没有任何「这里可以展开」
  // 的提示。选自定义箭头而非保留原生：原生箭头在 Safari / Chrome / Firefox 下长得
  // 完全不同，而统一正是这次迁移的目的。
  render(<Select aria-label="有箭头"><option>a</option></Select>);

  const select = screen.getByLabelText("有箭头");
  expect(select.className).toContain("appearance-none");
  // 箭头是兄弟节点上的 lucide 图标，且不能吃掉指向 select 的点击。
  const icon = select.parentElement!.querySelector("svg");
  expect(icon).toBeTruthy();
  expect(icon!.getAttribute("class")).toContain("pointer-events-none");
});
