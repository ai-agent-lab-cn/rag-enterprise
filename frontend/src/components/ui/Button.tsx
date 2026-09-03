import { cva, type VariantProps } from "class-variance-authority";
import { Info } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "./cn";
import { normalizeBlockedReason } from "./blockedReason";
import { Tooltip } from "./Tooltip";

/**
 * 统一按钮。规格见 docs/design/ui-foundation-tokens.md 第 3 节。
 *
 * 迁移前全仓库有五套并行的按钮 class 外加 47 个裸 `<button>`，同一语义的主色有四个
 * 近似值、圆角有两种。这个组件是那些的唯一去处。
 */
const button = cva(
  // 所有 variant 共享：尺寸靠 size 控制，这里只放形态与交互反馈。
  // border-0 是必需的，不是冗余：Tailwind preflight 在迁移完成前不能启用，浏览器给
  // `<button>` 的默认 `border: 1px outset` 还在。它以前被 `.table-actions button
  // { border: 0 }` 这类遗留 reset 压着，class 一清干净就冒出来。需要边框的 variant
  // 自己声明 `border`，tailwind-merge 会让后来的那条胜出。
  "inline-flex items-center justify-center gap-1.5 rounded-md font-semibold whitespace-nowrap " +
    "border-0 transition-colors cursor-pointer " +
    "focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand/20 " +
    "disabled:cursor-not-allowed disabled:opacity-55",
  {
    variants: {
      variant: {
        primary: "bg-brand text-white hover:bg-brand-hover",
        secondary: "bg-surface text-ink-muted border border-line-firm hover:bg-canvas",
        outline: "bg-transparent text-ink border border-line hover:bg-canvas",
        ghost: "bg-transparent text-brand hover:bg-brand-subtle",
        // 实底红只用于确认弹层。表格行内的删除请用 ghost + danger 文字：
        // 一行里出现红色实体按钮会把视线全吸走，而删除并不是那一行最重要的事。
        destructive: "bg-danger text-white hover:brightness-95",
        link: "bg-transparent text-brand underline-offset-4 hover:underline p-0 h-auto",
      },
      size: {
        sm: "h-7 px-2 text-sm",
        md: "h-9 px-3.5 text-md",
        lg: "h-11 px-4 text-md",
        icon: "h-8 w-8 p-0",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

// 有意不暴露 `disabled`：禁用只能通过 `blockedReason` 表达，从类型上就消除了
// 「点不动又不解释自己」的按钮。这是 CLAUDE.md 第一条的编码化。
type NativeProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "disabled" | "children">;

export type ButtonProps = NativeProps &
  VariantProps<typeof button> & {
    children: ReactNode;
    /**
     * 为什么点不了。**给了就禁用，没给就可用**——不存在「禁用但没有原因」这种状态。
     *
     * 真实代码里的禁用条件几乎都是动态的（`count > 0 ? "请先迁移资料" : undefined`），
     * 所以做成可选字符串而不是 `disabled` + `reason` 的联合类型：后者只接受字面量
     * `true`，遇到 `disabled={a || b}` 这种表达式直接编译不过。
     *
     * 接受数组是因为一个按钮可能同时被多个条件挡住——「应用到 N 份」既要求勾选资料
     * 又要求选中目标分类，三元表达式只能说出第一个。**空数组等价于 undefined**：
     * 调用方常写 `blockedReason={reasons}` 而 reasons 是 filter 出来的，
     * 不这么定义就会出现「条件都满足了按钮还是灰的」。
     */
    blockedReason?: string | string[];
    /** 处理中：自动禁用并阻止重复提交，无需另给原因。 */
    loading?: boolean;
  };

export function Button({
  variant,
  size,
  className,
  blockedReason,
  loading = false,
  children,
  ...rest
}: ButtonProps) {
  const reasons = normalizeBlockedReason(blockedReason);
  const blocked = reasons.length > 0;
  const title = loading ? "处理中…" : blocked ? reasons.join("、") : undefined;

  return (
    <>
      <button
        type="button"
        {...rest}
        className={cn(button({ variant, size }), className)}
        disabled={blocked || loading}
        aria-busy={loading || undefined}
        title={title}
      >
        {children}
      </button>
      {/* 原因由独立的 ⓘ 承载，不再是块级小字——小字会把表格行撑高，
          而消灭行高不一致正是 DataTable 存在的理由之一。
          ⓘ 自己是可用的按钮：真实浏览器里 disabled 的 button 不派发
          pointerenter，把 Tooltip 包在禁用按钮外面在 jsdom 里会绿，
          在浏览器里永远弹不出来。 */}
      {!loading && blocked ? (
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
