import { expect, test } from "vitest";

test("jsdom 具备 Radix Popper 依赖的浏览器 API", () => {
  // Radix 的 Tooltip / DropdownMenu 共用 Popper，它在挂载时就构造 ResizeObserver。
  // jsdom 不实现这些，缺了会抛 ReferenceError / TypeError，而不是干净的断言失败——
  // 报错信息指向 Radix 内部，排查要花很久。这条测试让缺失变成一句人话。
  expect(typeof globalThis.ResizeObserver).toBe("function");
  expect(typeof Element.prototype.hasPointerCapture).toBe("function");
  expect(typeof Element.prototype.setPointerCapture).toBe("function");
  expect(typeof Element.prototype.releasePointerCapture).toBe("function");
});
