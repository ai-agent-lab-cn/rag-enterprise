import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { useConfirm } from "./useConfirm";

afterEach(cleanup);

function Harness({ onConfirm }: { onConfirm: () => Promise<void> }) {
  const { confirm, dialog } = useConfirm();
  return (
    <div>
      <button
        onClick={() =>
          confirm({
            title: "删除资料",
            consequence: "会同时删除原始文件和对应向量索引，删除后无法在当前知识库中检索。",
            confirmLabel: "确认删除",
            tone: "destructive",
            onConfirm,
          })
        }
      >
        删除
      </button>
      {dialog}
    </div>
  );
}

test("确认前后果文案必须出现在弹层里", async () => {
  render(<Harness onConfirm={async () => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  expect(await screen.findByRole("dialog")).toHaveTextContent(
    "会同时删除原始文件和对应向量索引",
  );
});

test("确认按钮不抢焦点，回车不会直接执行", async () => {
  const onConfirm = vi.fn(async () => {});
  render(<Harness onConfirm={onConfirm} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  await screen.findByRole("dialog");
  // DocumentPanel.tsx:133 现在给确认按钮加了 autoFocus，弹层一开回车就删。
  // 破坏性操作不该是一个回车的距离。
  expect(screen.getByRole("button", { name: "确认删除" })).not.toHaveFocus();

  // 初始焦点实际落在 Dialog 头部的关闭按钮（X）上——它在 DOM 顺序里先于
  // DialogActions，Radix 聚焦 Content 内第一个可聚焦元素。所以这里的回车
  // 是「关闭弹层」，不是「什么都没发生」。两条断言一起才说清了真实行为；
  // 只断言 onConfirm 未被调用会让下一个人以为焦点在取消按钮上。
  await userEvent.keyboard("{Enter}");
  expect(onConfirm).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("点确认才执行，执行期间两个按钮都锁住", async () => {
  let release: () => void = () => {};
  const onConfirm = vi.fn(() => new Promise<void>((resolve) => { release = resolve; }));
  render(<Harness onConfirm={onConfirm} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  await userEvent.click(await screen.findByRole("button", { name: "确认删除" }));

  expect(onConfirm).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: /确认删除/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /取消/ })).toBeDisabled();

  release();
});

test("取消不执行且关闭弹层", async () => {
  const onConfirm = vi.fn(async () => {});
  render(<Harness onConfirm={onConfirm} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  await userEvent.click(await screen.findByRole("button", { name: "取消" }));

  expect(onConfirm).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("onConfirm 失败后弹层保持打开、按钮解锁，且不产生未处理 rejection", async () => {
  const onUnhandledRejection = vi.fn();
  window.addEventListener("unhandledrejection", onUnhandledRejection);

  const onConfirm = vi.fn(async () => {
    throw new Error("删除失败");
  });
  render(<Harness onConfirm={onConfirm} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  await userEvent.click(await screen.findByRole("button", { name: "确认删除" }));

  // 给未处理 rejection 一个机会冒出来：如果 run() 没有 catch，
  // window 会在这个 tick 之后触发 unhandledrejection。
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(onConfirm).toHaveBeenCalledTimes(1);
  // 弹层仍在——失败不等于「当作删除已完成」关掉它。
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  // 按钮解锁，用户能重试或取消，而不是卡在锁定态。
  expect(screen.getByRole("button", { name: "确认删除" })).not.toBeDisabled();
  expect(screen.getByRole("button", { name: "取消" })).not.toBeDisabled();
  expect(onUnhandledRejection).not.toHaveBeenCalled();

  window.removeEventListener("unhandledrejection", onUnhandledRejection);
});

test("onConfirm 失败后弹层内展示错误文案，且不影响重试", async () => {
  const onConfirm = vi.fn(async () => {
    throw new Error("删除失败：资料仍被引用");
  });
  render(<Harness onConfirm={onConfirm} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  await userEvent.click(await screen.findByRole("button", { name: "确认删除" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("删除失败：资料仍被引用");
  // 弹层仍打开，两个按钮恢复可用——用户能看着错误重试或取消。
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确认删除" })).not.toBeDisabled();
  expect(screen.getByRole("button", { name: "取消" })).not.toBeDisabled();
});

test("关掉弹层再开一个新的确认，上次的错误不应该还在", async () => {
  const onConfirm = vi.fn(async () => {
    throw new Error("第一次的错误");
  });
  render(<Harness onConfirm={onConfirm} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  await userEvent.click(await screen.findByRole("button", { name: "确认删除" }));
  await screen.findByRole("alert");

  await userEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(screen.queryByRole("dialog")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  await screen.findByRole("dialog");
  expect(screen.queryByRole("alert")).toBeNull();
});

test("失败后重试成功，错误消失且弹层关闭", async () => {
  let shouldFail = true;
  const onConfirm = vi.fn(async () => {
    if (shouldFail) {
      shouldFail = false;
      throw new Error("第一次失败");
    }
  });
  render(<Harness onConfirm={onConfirm} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  await userEvent.click(await screen.findByRole("button", { name: "确认删除" }));
  await screen.findByRole("alert");

  await userEvent.click(screen.getByRole("button", { name: "确认删除" }));

  expect(onConfirm).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("destructive 的确认按钮用红底", async () => {
  render(<Harness onConfirm={async () => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "删除" }));
  const confirmButton = await screen.findByRole("button", { name: "确认删除" });
  expect(confirmButton.className.split(/\s+/)).toContain("bg-danger");
});
