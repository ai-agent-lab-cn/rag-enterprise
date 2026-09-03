import { Tabs as RadixTabs } from "radix-ui";
import type { ReactNode } from "react";
import { cn } from "./cn";

/**
 * 统一分页签。
 *
 * **换 Radix 的理由是键盘规范。** 自定义实现只能靠 Tab 键逐个走，详情页 7 个 tab
 * 要按 7 下才能到达内容区；tablist 的规范是方向键在组内切换、Tab 键跳出整组。
 *
 * 内容由调用方按 value 自行渲染，不做 TabsContent 的多份挂载——详情页每个 tab 各自
 * 拉数据，全部挂载会在进页面时打七个请求。
 */
export type TabItem = { value: string; label: string; count?: number };

export function Tabs({
  items,
  value,
  onChange,
  children,
  label,
}: {
  items: TabItem[];
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  label: string;
}) {
  return (
    <RadixTabs.Root value={value} onValueChange={onChange}>
      <RadixTabs.List aria-label={label} className="flex gap-1 border-b border-line">
        {items.map((item) => (
          <RadixTabs.Trigger
            key={item.value}
            value={item.value}
            className={cn(
              "border-0 border-b-2 border-transparent bg-transparent px-3 py-2 text-md text-ink-muted",
              "cursor-pointer hover:text-ink",
              "focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand/20",
              "data-[state=active]:border-brand data-[state=active]:font-semibold data-[state=active]:text-ink",
            )}
          >
            {item.label}
            {item.count === undefined ? null : (
              <span className="ml-1.5 tabular-nums text-sm text-ink-faint">{item.count}</span>
            )}
          </RadixTabs.Trigger>
        ))}
      </RadixTabs.List>
      {/* 只挂载当前 tab 的内容：详情页每个 tab 各自拉数据，全挂载会一次打七个请求。 */}
      <RadixTabs.Content value={value} className="pt-4 focus-visible:outline-none">
        {children}
      </RadixTabs.Content>
    </RadixTabs.Root>
  );
}
