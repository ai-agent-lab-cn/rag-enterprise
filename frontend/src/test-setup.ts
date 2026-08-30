import "@testing-library/jest-dom/vitest";

// jsdom 不实现滚动 API。App.tsx 路由切换后会滚动到锚点或页首，真实浏览器一定有这两个
// 方法，所以补在测试环境而不是在业务代码里做特性检测。缺了它会抛未捕获异常，Vitest
// 报「1 error」但测试仍然全绿——正是那种会被忽略掉的失败。
Element.prototype.scrollIntoView = () => {};
window.scrollTo = () => {};
