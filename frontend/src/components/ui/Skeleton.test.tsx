import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { Skeleton, SkeletonRows } from "./Skeleton";

afterEach(cleanup);

test("骨架块对辅助技术不可见", () => {
  render(<Skeleton className="h-4 w-20" />);

  // 骨架屏是视觉占位，不承载信息。让屏幕阅读器去读一堆空盒子只会制造噪音；
  // 加载状态由外层的 role="status" 承担。
  expect(screen.queryByRole("presentation")).toBeNull();
  expect(document.querySelector("[aria-hidden='true']")).not.toBeNull();
});

test("外部 className 决定尺寸，组件只提供质感", () => {
  render(<Skeleton className="h-8 w-40" />);

  const cls = document.querySelector("[aria-hidden='true']")!.className.split(/\s+/);
  expect(cls).toContain("h-8");
  expect(cls).toContain("w-40");
  expect(cls).toContain("animate-pulse");
});

test("SkeletonRows 按给定行列数占位，行高与真实表格一致", () => {
  render(
    <table>
      <tbody>
        <SkeletonRows rows={3} columns={4} />
      </tbody>
    </table>,
  );

  const rows = document.querySelectorAll("tr");
  expect(rows).toHaveLength(3);
  expect(rows[0].querySelectorAll("td")).toHaveLength(4);
  // h-14 必须与 DataTable 的行高一致，否则数据到达时页面会跳——
  // 而消除这个跳动正是引入骨架屏的全部理由。
  expect(rows[0].className).toContain("h-14");
});
