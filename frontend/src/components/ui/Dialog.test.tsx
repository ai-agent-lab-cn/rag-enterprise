import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { Dialog } from "./Dialog";

afterEach(cleanup);

function Basic({ onClose = vi.fn() }: { onClose?: () => void }) {
  return (
    <Dialog open title="删除知识库" description="此操作不可撤销" onClose={onClose}>
      <p>确认删除吗？</p>
      <button type="button">确认</button>
    </Dialog>
  );
}

test("渲染为带无障碍标注的模态", () => {
  render(<Basic />);

  const dialog = screen.getByRole("dialog");
  // 断言行为而非实现：Radix 靠给背景加 aria-hidden 来实现模态，比 `aria-modal` 属性
  // 更可靠（后者依赖屏幕阅读器支持），所以不去断言那个属性。
  expect(dialog).toHaveAttribute("aria-labelledby");
  expect(document.getElementById(dialog.getAttribute("aria-labelledby")!)).toHaveTextContent(
    "删除知识库",
  );
  expect(screen.getByText("此操作不可撤销")).toBeVisible();
});

test("ESC 关闭", async () => {
  const onClose = vi.fn();
  render(<Basic onClose={onClose} />);

  await userEvent.keyboard("{Escape}");

  expect(onClose).toHaveBeenCalled();
});

test("焦点进入弹层，且被困在里面", async () => {
  // 这是换掉手写 Modal 的**唯一理由**：原来那 27 行没有任何焦点管理，
  // 键盘用户 Tab 出去就再也回不来，屏幕阅读器仍在朗读背后的页面。
  render(<Basic />);

  const dialog = screen.getByRole("dialog");
  await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));

  // 连续 Tab 若干次，焦点始终不离开弹层。
  for (let i = 0; i < 6; i += 1) {
    await userEvent.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
  }
});

test("关闭后弹层从 DOM 移除", async () => {
  // 注意：**焦点是否还给触发元素，jsdom 测不出来**——Radix 的 focus restore 依赖真实
  // 浏览器的焦点模型，在 jsdom 里等满超时 activeElement 仍是 <body>。那条行为放到
  // Playwright 里验证，见 e2e/visual-baseline.spec.ts 的弹层用例。这里只测开合本身。
  function Harness() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>打开</button>
        <Dialog open={open} title="标题" onClose={() => setOpen(false)}>
          <p>内容</p>
        </Dialog>
      </>
    );
  }
  render(<Harness />);

  await userEvent.click(screen.getByRole("button", { name: "打开" }));
  await screen.findByRole("dialog");

  await userEvent.keyboard("{Escape}");

  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("open 为 false 时不渲染任何内容", () => {
  render(
    <Dialog open={false} title="不该出现" onClose={vi.fn()}>
      <p>内容</p>
    </Dialog>,
  );

  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByText("不该出现")).toBeNull();
});
