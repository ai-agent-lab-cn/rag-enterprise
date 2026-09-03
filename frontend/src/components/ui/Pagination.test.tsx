import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Pagination } from "./Pagination";

afterEach(cleanup);

test("只有一页时整个组件不渲染", () => {
  const { container } = render(
    <Pagination page={0} hasNext={false} onChange={() => {}} label="知识库分页" />,
  );

  // 三行数据下面挂一个「第 1 页」+ 两个灰按钮，是纯噪音。
  expect(container).toBeEmptyDOMElement();
});

test("第一页时上一页禁用且说得出原因", () => {
  render(<Pagination page={0} hasNext onChange={() => {}} label="知识库分页" />);

  const prev = screen.getByRole("button", { name: "上一页" });
  expect(prev).toBeDisabled();
  expect(prev).toHaveAttribute("title", "已经是第一页");
});

test("翻页回传 0-based 页码", async () => {
  const onChange = vi.fn();
  render(<Pagination page={2} hasNext onChange={onChange} label="知识库分页" />);

  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  expect(onChange).toHaveBeenCalledWith(3);

  await userEvent.click(screen.getByRole("button", { name: "上一页" }));
  expect(onChange).toHaveBeenCalledWith(1);
});

test("页码按 1-based 显示给用户", () => {
  render(<Pagination page={2} hasNext onChange={() => {}} label="知识库分页" />);

  // 内部 0-based、显示 1-based。两处旧实现都这么做，保持一致。
  expect(screen.getByText("第 3 页")).toBeInTheDocument();
});

test("label 成为导航的可访问名", () => {
  render(<Pagination page={0} hasNext onChange={() => {}} label="审计记录分页" />);

  expect(screen.getByRole("navigation", { name: "审计记录分页" })).toBeInTheDocument();
});
