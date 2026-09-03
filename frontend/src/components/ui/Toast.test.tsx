import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ToastProvider, useToast } from "./Toast";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function Harness() {
  const toast = useToast();
  return (
    <div>
      <button onClick={() => toast.success("已删除 报销制度.md")}>删除</button>
      <button onClick={() => toast.error("删除失败：资料正在索引")}>失败</button>
    </div>
  );
}

test("成功提示出现在 role=status 里", async () => {
  render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  expect(screen.getByRole("status")).toHaveTextContent("已删除 报销制度.md");
});

test("成功提示同时有 role=status 和 aria-live=polite", async () => {
  // aria-hidden 库会跳过 [aria-live] 属性的元素，避免在确认弹层打开期间隐藏 toast。
  // 但选择器是 [aria-live]——要求 DOM 上有显式属性。role="status" 只是隐含 live 语义，
  // querySelectorAll 匹配不到，所以必须补显式属性，否则错误提示在弹层打开期间对屏幕阅读器不可达。
  render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  const element = screen.getByRole("status");
  expect(element).toHaveAttribute("aria-live", "polite");
});

test("失败提示用 role=alert，会打断屏幕阅读器", async () => {
  render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );

  await userEvent.click(screen.getByRole("button", { name: "失败" }));
  // 成功是可以慢慢读的，失败必须立刻打断——两者的 ARIA 角色不同不是细节。
  expect(screen.getByRole("alert")).toHaveTextContent("删除失败：资料正在索引");
});

test("失败提示同时有 role=alert 和 aria-live=assertive", async () => {
  // aria-hidden 库会跳过 [aria-live] 属性的元素，避免在确认弹层打开期间隐藏 toast。
  // role="alert" 只是隐含 assertive 语义，需要补显式属性，否则在弹层打开期间无法读到。
  // 失败提示常在弹层里触发（点「确认删除」后后端报错），所以这个修复直接影响可用性。
  render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );

  await userEvent.click(screen.getByRole("button", { name: "失败" }));
  const element = screen.getByRole("alert");
  expect(element).toHaveAttribute("aria-live", "assertive");
});

test("成功提示 4 秒后自动消失", () => {
  // 这条测的是时间契约，不是交互序列，所以用 fireEvent 而不是 userEvent：
  // userEvent 内部的 delay 与 vi.useFakeTimers() 会互相等待，实测 10.8 秒后超时，
  // 而 advanceTimers 选项在 vitest 4 + React 19 这个组合下解不开。
  vi.useFakeTimers();
  render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );

  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  expect(screen.getByRole("status")).toBeInTheDocument();

  act(() => void vi.advanceTimersByTime(4000));
  expect(screen.queryByRole("status")).toBeNull();
});

test("失败提示不自动消失，必须手动关闭", () => {
  vi.useFakeTimers();
  render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );

  fireEvent.click(screen.getByRole("button", { name: "失败" }));
  act(() => void vi.advanceTimersByTime(10_000));
  // 错误信息自动消失等于没说过——用户可能正在别处看，回头什么都没有。
  expect(screen.getByRole("alert")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "关闭提示" }));
  expect(screen.queryByRole("alert")).toBeNull();
});

test("Provider 之外调用 useToast 直接报错，而不是静默无效", () => {
  function Orphan() {
    useToast();
    return null;
  }
  // 静默无效意味着「写操作成功了但没提示」这种 bug 要靠人眼发现。
  expect(() => render(<Orphan />)).toThrow(/ToastProvider/);
});

test("弹层打开后才出现的 toast 仍然对辅助技术可达", () => {
  // 这是最常见的路径：打开确认弹层 → 操作失败 → 出现提示。
  // aria-hidden 库在 hideOthers() 调用的那一刻对 DOM 做快照，之后插入的元素不被追认，
  // 所以 aria-live 必须挂在常驻容器上，而不是按需渲染的 toast item 上。
  // 只断言「item 有 aria-live」的测试无法发现这个问题——上一版修复就是这么漏掉的。
  render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );

  // 容器必须在没有任何 toast 时就已带 aria-live 存在于 DOM 中。
  const region = document.querySelector("[aria-live]");
  expect(region).not.toBeNull();
  expect(region!.querySelectorAll("[role=status], [role=alert]")).toHaveLength(0);
});

test("无 toast 时 aria-hidden 库的选择器仍能命中容器", () => {
  // aria-hidden 用的正是这个选择器（node_modules/aria-hidden/dist/es2015/index.js:133）：
  //   activeParentNode.querySelectorAll('[aria-live], script')
  // 命中不到就会把整个 #root 标记为 aria-hidden。
  render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );

  expect(document.body.querySelectorAll("[aria-live], script").length).toBeGreaterThan(0);
});

test("Provider 卸载时清理未触发的定时器", () => {
  vi.useFakeTimers();
  const { unmount } = render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );

  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  expect(vi.getTimerCount()).toBe(1);

  // 不清理的话，这个 timer 会在 4 秒后对着已卸载的组件调 setItems。
  // React 18+ 不再警告，所以这个泄漏只能这样断言出来。
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});
