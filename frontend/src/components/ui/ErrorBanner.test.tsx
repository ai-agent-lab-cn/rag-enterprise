import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { ErrorBanner } from "./ErrorBanner";

afterEach(cleanup);

test("渲染为 role=alert，读屏能立刻播报", () => {
  render(<ErrorBanner>删除失败</ErrorBanner>);
  expect(screen.getByRole("alert")).toHaveTextContent("删除失败");
});

test("外部 className 能追加而不覆盖基类", () => {
  render(<ErrorBanner className="mt-4">出错了</ErrorBanner>);
  const el = screen.getByRole("alert");
  expect(el.className).toContain("mt-4");
  expect(el.className).toMatch(/border/);
});
