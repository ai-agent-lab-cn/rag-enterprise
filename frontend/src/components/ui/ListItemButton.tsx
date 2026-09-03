import type { ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

/**
 * 列表项 / 卡片式可点区域的公共基座。
 *
 * 覆盖导航项、Tab、历史会话项、树节点、Chunk 项、卡片式操作——形态差异很大（单行/
 * 多行、图标+文字/纯文字、整卡可点），装不下统一的固定高度或强制样式，所以只提供
 * 三条硬约束，具体布局全部交给调用方通过 `className` 表达：
 *
 * 1. **`border-0` + 显式 `bg-transparent`。** preflight 尚未启用（Task 6 才开），
 *    UA 给 `<button>` 的默认 `border: 2px outset` + `background-color: ButtonFace`
 *    仍然生效——这个仓库已经在这里栽了两个 Critical（ChatPage 的 Tab 和历史项漏
 *    `border-0`，`AppNavigation` 漏 `py-0`）。`bg-transparent` 不是装饰：只设置
 *    `background-image` 的 utility（比如历史会话选中态的渐变）不会重置
 *    `background-color`，UA 默认会从透明缝隙里露出来——这正是 ChatPage 选中态历史项
 *    实测 `backgroundColor` 是 `rgb(239,239,239)`（靠不透明渐变盖住）的原因。
 * 2. **`active` 与 `aria-current` 同一个来源。** 视觉「选中」和语义「选中」不能分叉
 *    （CLAUDE.md 第一条同源原则）——传 `active` 才会有 `aria-current`，两者不可能
 *    只出现一个。默认 token 是 `"true"`（列表内选中，不是分页导航）；需要 `"page"`
 *    的调用方显式传 `aria-current="page"`，只在 `active` 为真时才会生效。
 *    `role="tab"` 场景语义上要用 `aria-selected` 而不是 `aria-current`，不传 `active`，
 *    自己通过透传属性给 `aria-selected`。
 * 3. **默认 `type="button"`。** 防止列表项按钮意外提交外层表单。
 */
export type ListItemButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  /** 是否为当前选中/激活项。驱动 `aria-current` 默认值；具体激活视觉由调用方通过 className 表达。 */
  active?: boolean;
};

export function ListItemButton({
  active = false,
  type = "button",
  className,
  "aria-current": ariaCurrent,
  ...rest
}: ListItemButtonProps) {
  return (
    <button
      type={type}
      {...rest}
      aria-current={active ? (ariaCurrent ?? "true") : undefined}
      className={cn(
        // 有意不带默认 gap：这里的调用方要么自己声明 gap（flex/grid 子项间距），
        // 要么靠子元素的 margin 排间距（比如 OverviewPage 的卡片式磁贴）。给个默认
        // gap 会在后一种场景里和 margin 叠加，把间距撑大——已经在磁贴上实测过。
        "inline-flex w-full items-center border-0 bg-transparent text-left font-normal cursor-pointer " +
          "transition-colors focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand/20",
        className,
      )}
    />
  );
}
