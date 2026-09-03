import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";
import { Tooltip } from "./Tooltip";

afterEach(cleanup);

test("默认不显示内容，悬停后才出现", async () => {
  render(
    <Tooltip content="默认知识库不能删除">
      <button>删除</button>
    </Tooltip>,
  );

  expect(screen.queryByText("默认知识库不能删除")).toBeNull();

  await userEvent.hover(screen.getByRole("button", { name: "删除" }));
  // Radix 把内容渲染进 portal，且同时存在一份 aria-live 副本，所以用 findAllByText。
  const shown = await screen.findAllByText("默认知识库不能删除");
  expect(shown.length).toBeGreaterThan(0);
});

test("触发元素带 aria-describedby，屏幕阅读器读得到原因", async () => {
  render(
    <Tooltip content="请先选择知识库">
      <button>授权</button>
    </Tooltip>,
  );

  const trigger = screen.getByRole("button", { name: "授权" });
  await userEvent.hover(trigger);
  await screen.findAllByText("请先选择知识库");
  // 这是这个组件存在的核心理由：把原因绑到触发元素上，而不是只画一个浮层。
  // CLAUDE.md 第一条禁止「只有 title」，aria-describedby 是无障碍这一路的补齐。
  expect(trigger).toHaveAttribute("aria-describedby");
});

test("Provider 由组件自己内联，调用方不必包一层", () => {
  // Radix 要求 Tooltip.Root 必须在 Tooltip.Provider 之内，否则运行时报错。
  // 把 Provider 收进组件，调用方就不会因为忘了包而在生产环境炸——那种错误
  // 只在真正悬停时才触发，单元测试和构建都发现不了。
  expect(() =>
    render(
      <Tooltip content="提示">
        <button>触发</button>
      </Tooltip>,
    ),
  ).not.toThrow();
});
