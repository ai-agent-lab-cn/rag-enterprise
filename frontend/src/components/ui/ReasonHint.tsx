import { Info } from "lucide-react";
import { Tooltip } from "./Tooltip";
import { cn } from "./cn";

/**
 * ⓘ + Tooltip 的原因提示。
 *
 * 收敛掉「状态说不出为什么」这一类问题：一个只写着「分类失败」的徽章，用户看到的不是
 * 「这里有原因」而是「功能坏了」（CLAUDE.md 第一条）。
 *
 * **为什么是 ⓘ 而不是一行小字**：块级小字会把 `ui/DataTable` 保证的统一行高撑破，
 * 而消灭行高不一致正是 DataTable 存在的理由之一。同样的权衡在 `ui/Button` 的
 * `blockedReason` 上已经做过一次（见 Button.tsx:96 的注释），这里沿用同一套模式，
 * 避免同类问题在两处长出两种样子（CLAUDE.md 第二条）。
 *
 * **ⓘ 自己必须是可用的按钮**，不能把 Tooltip 包在禁用元素外面：真实浏览器里
 * `disabled` 的 button 不派发 `pointerenter`，而 jsdom 会——那样写的测试会绿着骗人，
 * 浏览器里 Tooltip 永远弹不出来。
 *
 * `delay={0}`：原因属于「必须马上看到」的信息，不做 200ms 悬停延迟。
 */
export function ReasonHint({
  reason,
  label,
  tone = "danger",
  className,
}: {
  /** 原因文案。为空时整个组件不渲染——没有原因就不该出现一个点了没反应的 ⓘ。 */
  reason: string | null | undefined;
  /** 可访问名，形如「broken.md 的分类失败原因」。必填：一张表里几十个 ⓘ 长得一样。 */
  label: string;
  /** danger 用于失败，muted 用于中性说明。 */
  tone?: "danger" | "muted";
  className?: string;
}) {
  if (!reason) return null;
  return (
    <Tooltip content={reason} delay={0}>
      <button
        type="button"
        aria-label={label}
        className={cn(
          // border-0 与 bg-none 都是显式的：preflight 虽已启用，但 legacy 时期
          // 属性选择器命中过这个 ⓘ 的背景（见 6424072），显式声明省得再踩。
          "grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full border-0 bg-none p-0",
          "focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand/20",
          tone === "danger" ? "text-danger-text" : "text-ink-faint",
          className,
        )}
      >
        <Info size={13} />
      </button>
    </Tooltip>
  );
}
