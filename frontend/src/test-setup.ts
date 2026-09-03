import "@testing-library/jest-dom/vitest";

// jsdom 不实现滚动 API。App.tsx 路由切换后会滚动到锚点或页首，真实浏览器一定有这两个
// 方法，所以补在测试环境而不是在业务代码里做特性检测。缺了它会抛未捕获异常，Vitest
// 报「1 error」但测试仍然全绿——正是那种会被忽略掉的失败。
Element.prototype.scrollIntoView = () => {};
window.scrollTo = () => {};

// Radix 的 Popper（Tooltip / DropdownMenu 共用）挂载时就构造 ResizeObserver，
// jsdom 不实现它。补一个空实现即可：测试断言的是 DOM 结构和可访问性属性，
// 不是浮层的实际坐标，而坐标计算正是 ResizeObserver 唯一参与的部分。
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// Radix 的菜单类组件用 pointer capture 处理「按下后拖到菜单项再松开」的手势。
// jsdom 的 Element 没有这三个方法，userEvent 触发 pointerdown 时会抛 TypeError。
Element.prototype.hasPointerCapture = () => false;
Element.prototype.setPointerCapture = () => {};
Element.prototype.releasePointerCapture = () => {};
