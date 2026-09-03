import { cva, type VariantProps } from "class-variance-authority";
import { ChevronDown, Info } from "lucide-react";
import type { ReactNode, SelectHTMLAttributes } from "react";
import { cn } from "./cn";
import { normalizeBlockedReason } from "./blockedReason";
import { Tooltip } from "./Tooltip";

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
  // font-normal 是必须的：控件不声明字重就会继承容器。阶段 5 把
  // `.modal-form label { font-weight: 600 }` 收口成 utility 后，成员弹层里的
  // select 文字被继承成 600（实测暗像素 +38%）。控件的字重该由控件自己决定。
  "w-full min-w-0 rounded-md border bg-surface text-ink font-normal appearance-none cursor-pointer " +
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
    /**
     * 与 Button 同一条规则：给了原因才禁用，见 CLAUDE.md 第一条。
     * 接受数组是因为一个控件可能同时被多个条件挡住，见 `Button.blockedReason`
     * 与 `normalizeBlockedReason()`——两处共用同一份归一化逻辑，避免分隔符或
     * 空值语义在两个控件之间静默漂移。
     */
    blockedReason?: string | string[];
  };

export function Select({
  size,
  invalid,
  className,
  blockedReason,
  children,
  ...rest
}: SelectProps) {
  const reasons = normalizeBlockedReason(blockedReason);
  const blocked = reasons.length > 0;
  const title = blocked ? reasons.join("、") : undefined;

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
          disabled={blocked}
          aria-invalid={invalid || undefined}
          title={title}
        >
          {children}
        </select>
        <ChevronDown
          size={12}
          aria-hidden
          className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-faint"
        />
      </span>
      {/* 原因由独立的 ⓘ 承载，不再是块级小字——小字会把表格行撑高，
          而消灭行高不一致正是 DataTable 存在的理由之一。
          ⓘ 自己是可用的按钮：真实浏览器里 disabled 的原生控件不派发
          pointerenter，把 Tooltip 包在禁用的 select 外面在 jsdom 里会绿，
          在浏览器里永远弹不出来。 */}
      {blocked ? (
        <Tooltip content={reasons.join("、")} delay={0}>
          <button
            type="button"
            aria-label={`为什么不可用：${reasons.join("、")}`}
            // min-w-0 / bg-none 不是多余的：legacy CSS 里有
            // `.question-footer button { width/min-width/height: 34px; background:
            // linear-gradient(...) }` 这类标签选择器，会连带命中这个 ⓘ。同名属性 utilities
            // 层能压过 legacy 层，但 legacy 用的是简写属性，utilities 这边必须每个子属性都
            // 单独给一份才压得住：min-width 对应 min-w-0，background 简写里的
            // background-image 对应 bg-none（bg-transparent 只压 background-color，
            // 两者是不同属性）。迁移期内 legacy 层还在，同类规则可能不止这一条。
            className="inline-grid h-4 w-4 min-w-0 shrink-0 place-items-center rounded-full border-0 bg-transparent bg-none p-0 text-ink-faint hover:text-ink-muted focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand/20"
          >
            <Info size={13} />
          </button>
        </Tooltip>
      ) : null}
    </>
  );
}
