import { Tooltip as RadixTooltip } from "radix-ui";
import type { ReactNode } from "react";

/**
 * 统一悬浮提示。
 *
 * **Provider 内联在组件里，不要求调用方包一层。** Radix 的规范是把 Provider 放在应用
 * 根部共享，但那样每个调用点都要记得「根部已经有了」——忘了包的后果是运行时报错，
 * 且只在真正悬停时才触发，单元测试和构建都发现不了。内联的代价是多几个 Provider 实例，
 * 它们无状态，代价可以忽略。
 *
 * 它承载两类内容：禁用原因（`delay={0}`，必须马上看到）和截断文本的全名（默认延迟）。
 */
export function Tooltip({
  content,
  children,
  delay = 200,
  side = "top",
}: {
  content: ReactNode;
  children: ReactNode;
  /** 默认 200ms；0 表示立即显示，用于禁用原因这类必须马上看到的场景。 */
  delay?: number;
  side?: "top" | "right" | "bottom" | "left";
}) {
  return (
    <RadixTooltip.Provider delayDuration={delay}>
      <RadixTooltip.Root>
        {/* asChild：把触发行为合并到子元素上，不额外套一层 span——套了会破坏
            flex/grid 布局，也会让 Button 的 w-fit 之类的 className 失效。 */}
        <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content
            side={side}
            sideOffset={6}
            className="z-50 max-w-64 rounded-sm bg-ink px-2 py-1 text-sm text-white shadow-pop"
          >
            {content}
            <RadixTooltip.Arrow className="fill-ink" />
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  );
}
