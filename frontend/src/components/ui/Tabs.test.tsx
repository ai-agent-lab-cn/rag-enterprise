import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Tabs } from "./Tabs";

afterEach(cleanup);

const ITEMS = [
  { value: "documents", label: "资料", count: 5 },
  { value: "categories", label: "分类管理", count: 6 },
  { value: "members", label: "权限边界", count: 1 },
];

test("渲染 tablist，选中项带 aria-selected", () => {
  render(
    <Tabs items={ITEMS} value="documents" onChange={() => {}} label="知识库详情">
      <div>内容</div>
    </Tabs>,
  );

  expect(screen.getByRole("tablist", { name: "知识库详情" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /资料/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: /分类管理/ })).toHaveAttribute("aria-selected", "false");
});

test("count 渲染在标签旁", () => {
  render(
    <Tabs items={ITEMS} value="documents" onChange={() => {}} label="知识库详情">
      <div>内容</div>
    </Tabs>,
  );

  expect(screen.getByRole("tab", { name: /资料/ }).textContent).toContain("5");
});

test("方向键切换，符合 tablist 键盘规范", async () => {
  const onChange = vi.fn();
  render(
    <Tabs items={ITEMS} value="documents" onChange={onChange} label="知识库详情">
      <div>内容</div>
    </Tabs>,
  );

  // 自定义实现的 tab 只能靠 Tab 键逐个走，7 个 tab 要按 7 下才能到内容区。
  // 规范是方向键在组内切换、Tab 键跳出整组——这是换 Radix 的实际理由。
  screen.getByRole("tab", { name: /资料/ }).focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(onChange).toHaveBeenCalledWith("categories");
});

test("点击切换回传新值", async () => {
  const onChange = vi.fn();
  render(
    <Tabs items={ITEMS} value="documents" onChange={onChange} label="知识库详情">
      <div>内容</div>
    </Tabs>,
  );

  await userEvent.click(screen.getByRole("tab", { name: /权限边界/ }));
  expect(onChange).toHaveBeenCalledWith("members");
});

test("内容区带 tabpanel 角色", () => {
  render(
    <Tabs items={ITEMS} value="documents" onChange={() => {}} label="知识库详情">
      <div>资料列表</div>
    </Tabs>,
  );

  expect(screen.getByRole("tabpanel")).toHaveTextContent("资料列表");
});
