import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { ListItemButton } from "./ListItemButton";

afterEach(cleanup);

test("active 为 true 时带 aria-current，默认 token 是 true", () => {
  render(<ListItemButton active>选中项</ListItemButton>);
  expect(screen.getByRole("button", { name: "选中项" })).toHaveAttribute("aria-current", "true");
});

test("active 为 false（或未传）时不带 aria-current", () => {
  render(<ListItemButton>普通项</ListItemButton>);
  expect(screen.getByRole("button", { name: "普通项" })).not.toHaveAttribute("aria-current");
});

test("aria-current 的 token 可覆盖（导航项用 page），但仅在 active 为真时生效", () => {
  const { rerender } = render(
    <ListItemButton active aria-current="page">
      导航项
    </ListItemButton>,
  );
  expect(screen.getByRole("button", { name: "导航项" })).toHaveAttribute("aria-current", "page");

  // active 为假时，即使传了 aria-current="page" 也不应该出现——避免视觉未选中却语义选中。
  rerender(
    <ListItemButton aria-current="page">
      导航项
    </ListItemButton>,
  );
  expect(screen.getByRole("button", { name: "导航项" })).not.toHaveAttribute("aria-current");
});

test("基类含 border-0 与显式 bg-transparent（防 UA 默认边框/背景回归）", () => {
  render(<ListItemButton>项目</ListItemButton>);
  const cls = screen.getByRole("button", { name: "项目" }).className.split(/\s+/);
  expect(cls).toContain("border-0");
  expect(cls).toContain("bg-transparent");
});

test("默认 type 是 button，防止意外提交外层表单", () => {
  render(<ListItemButton>项目</ListItemButton>);
  expect(screen.getByRole("button", { name: "项目" })).toHaveAttribute("type", "button");
});

test("外部 className 能追加而不覆盖基类，冲突的背景色由调用方覆盖", () => {
  render(<ListItemButton className="bg-brand-subtle rounded-lg">卡片</ListItemButton>);
  const cls = screen.getByRole("button", { name: "卡片" }).className.split(/\s+/);
  expect(cls).toContain("border-0");
  expect(cls).toContain("rounded-lg");
  // tailwind-merge：同组的背景色类只保留后来的那条。
  expect(cls).toContain("bg-brand-subtle");
  expect(cls).not.toContain("bg-transparent");
});

test("style 能透传（ParsingPanel 用它做层级缩进）", () => {
  render(<ListItemButton style={{ paddingLeft: 30 }}>节点</ListItemButton>);
  expect(screen.getByRole("button", { name: "节点" })).toHaveStyle({ paddingLeft: "30px" });
});
