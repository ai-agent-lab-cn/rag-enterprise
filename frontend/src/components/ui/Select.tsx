import { cva, type VariantProps } from "class-variance-authority";
import { ChevronDown } from "lucide-react";
import type { ReactNode, SelectHTMLAttributes } from "react";
import { cn } from "./cn";

/**
 * 统一下拉。规格见 docs/design/ui-foundation-tokens.md。
 *
 * **有意包装原生 `<select>`，而不是用 Radix 重写。** 组件治理任务第六章禁止的是
 * 「div 模拟 Select」，而这个仓库的 21 处用的本来就是原生元素：语义正确、键盘可用、
 * 移动端还能唤起系统选择器。换成 Radix 要多 22 个依赖，换来的语义收益是零。
 *
 * 保持原生 API 也让迁移变成纯样式替换，调用方 `value`/`onChange`/`<option>` 全不用改。
 *
 * 例外情形：选项多到需要搜索时，用 Popover + Command，不要往这里加复杂度。
 */
const select = cva(
  // min-w-0 同 Input：w-full 的 flex 子项不加它就不肯收缩，会挤压同排的标签文字。
  "w-full min-w-0 rounded-md border bg-surface text-ink appearance-none cursor-pointer " +
    "bg-[right_10px_center] bg-no-repeat " +
    "focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand/20 " +
    "disabled:cursor-not-allowed disabled:opacity-55 disabled:bg-canvas",
  {
    variants: {
      size: {
        sm: "h-7 pl-2 pr-6 text-sm",
        md: "h-9 pl-2.5 pr-7 text-md",
      },
      invalid: {
        true: "border-danger",
        false: "border-line-firm",
      },
    },
    defaultVariants: { size: "md", invalid: false },
  },
);

type NativeProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, "disabled" | "size" | "children">;

export type SelectProps = NativeProps &
  VariantProps<typeof select> & {
    children: ReactNode;
    /** 与 Button 同一条规则：给了原因才禁用，见 CLAUDE.md 第一条。 */
    blockedReason?: string;
  };

export function Select({
  size,
  invalid,
  className,
  blockedReason,
  children,
  ...rest
}: SelectProps) {
  return (
    <>
      {/* appearance-none 去掉了系统箭头，这里给回一个。用 lucide 图标而不是 CSS
          background：data URI 会被 Vite 当成文件路径去解析，构建直接失败（实测）。
          选自定义而非保留原生，是因为原生箭头在 Safari / Chrome / Firefox 下长得
          完全不同，而统一正是这次迁移的目的。 */}
      {/* min-w-0：这层包装本身也可能是 flex 子项（问答工作台的过滤条就是）。 */}
      <span className="relative block min-w-0">
        <select
          {...rest}
          className={cn(select({ size, invalid }), className)}
          disabled={Boolean(blockedReason)}
          aria-invalid={invalid || undefined}
          title={blockedReason}
        >
          {children}
        </select>
        <ChevronDown
          size={12}
          aria-hidden
          className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-faint"
        />
      </span>
      {blockedReason ? (
        <small className="block text-xs text-ink-faint">{blockedReason}</small>
      ) : null}
    </>
  );
}
