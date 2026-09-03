/**
 * 把 `blockedReason` 归一化成原因数组。
 *
 * `Button` 与 `FileButton` 都要做这件事，抽出来是因为它们必须**同时**改变：
 * 分隔符、空值语义任何一处漂移，两个控件的禁用行为就会不一致，而这种不一致
 * 在页面上表现为「同样的条件，一个按钮点得动一个点不动」——最难查的那类 bug。
 *
 * **空数组等价于 undefined。** 调用方常写 `blockedReason={reasons}` 而 reasons 是
 * filter 出来的；`Boolean([])` 在 JS 里是 true，不特殊处理就会出现「条件都满足了
 * 按钮还是灰的」。
 */
export function normalizeBlockedReason(value?: string | string[]): string[] {
  if (value === undefined) return [];
  return Array.isArray(value) ? value : [value];
}
