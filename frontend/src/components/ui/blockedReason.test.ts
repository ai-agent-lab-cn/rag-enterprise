import { expect, test } from "vitest";
import { normalizeBlockedReason } from "./blockedReason";

test("undefined 归一化为空数组", () => {
  expect(normalizeBlockedReason(undefined)).toEqual([]);
});

test("空数组保持为空数组，等价于没有原因", () => {
  // Boolean([]) 在 JS 里是 true。调用方写 blockedReason={reasons} 而 reasons 是
  // filter 出来的空列表时，不特殊处理就会「条件都满足了按钮还是灰的」。
  expect(normalizeBlockedReason([])).toEqual([]);
});

test("单字符串包成单元素数组", () => {
  expect(normalizeBlockedReason("默认知识库不能删除")).toEqual(["默认知识库不能删除"]);
});

test("数组原样返回", () => {
  expect(normalizeBlockedReason(["请先勾选资料", "请先选择目标分类"])).toEqual([
    "请先勾选资料",
    "请先选择目标分类",
  ]);
});
