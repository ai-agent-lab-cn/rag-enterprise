import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "./cn";

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
     * 所以做成一个可选字符串而不是 `disabled` + `reason` 的联合类型：后者只接受字面量
     * `true`，遇到 `disabled={a || b}` 这种表达式直接编译不过。
     */
    blockedReason?: string;
    /**
     * 不把原因渲染成可见小字，只留 title。
     *
     * **仅当按钮文案本身已经说明了原因时才用**，例如「应用到 0 份」——那个 0 就是
     * 「还没勾选」。此时再补一句「请先勾选资料」只是重复，还会撑开工具栏布局。
     * 默认必须可见，见 CLAUDE.md 第一条。
     */
    reasonHidden?: boolean;
    /** 处理中：自动禁用并阻止重复提交，无需另给原因。 */
    loading?: boolean;
  };

export function Button({
  variant,
  size,
  className,
  blockedReason,
  reasonHidden = false,
  loading = false,
  children,
  ...rest
}: ButtonProps) {
  const reason = loading ? "处理中…" : blockedReason;

  return (
    <>
      <button
        type="button"
        {...rest}
        className={cn(button({ variant, size }), className)}
        disabled={Boolean(blockedReason) || loading}
        aria-busy={loading || undefined}
        title={reason}
      >
        {children}
      </button>
      {/* 原因要看得见，不能只躺在 title 里。loading 是短暂状态，不额外占位。 */}
      {!loading && blockedReason && !reasonHidden ? (
        <small className="block text-xs text-ink-faint">{blockedReason}</small>
      ) : null}
    </>
  );
}
