import { cva, type VariantProps } from "class-variance-authority";
import type { ReactNode } from "react";
import { cn } from "./cn";

/**
 * 统一徽章。收敛掉三套并行实现：`.status-tag`、`.base-type-tag`、`.status-pill`，
 * 外加成员页那个用按钮冒充徽章的「未授权」。
 *
 * **shape 区分语义类别，不是装饰选项。** 状态是会变的（可用→处理中→失败），画成
 * 圆角胶囊；类型是固有属性（默认知识库/独立知识库），画成方角标签。用户不需要读文字
 * 就能知道哪个是「现在怎么样」、哪个是「它是什么」。
 */
const badge = cva(
  "inline-flex items-center gap-1 whitespace-nowrap px-1.5 py-0.5 text-sm font-medium",
  {
    variants: {
      tone: {
        neutral: "bg-canvas text-ink-muted",
        success: "bg-success-subtle text-success",
        warning: "bg-warning/10 text-warning",
        danger: "bg-danger-subtle text-danger-text",
        brand: "bg-brand-subtle text-brand",
      },
      shape: {
        status: "rounded-full",
        type: "rounded-sm",
      },
    },
    defaultVariants: { tone: "neutral", shape: "status" },
  },
);

export type BadgeProps = VariantProps<typeof badge> & {
  children: ReactNode;
  className?: string;
};

export function Badge({ tone, shape, className, children }: BadgeProps) {
  return <span className={cn(badge({ tone, shape }), className)}>{children}</span>;
}
