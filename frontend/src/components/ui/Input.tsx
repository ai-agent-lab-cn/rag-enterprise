import { cva, type VariantProps } from "class-variance-authority";
import type { InputHTMLAttributes, TextareaHTMLAttributes } from "react";
import { cn } from "./cn";

/**
 * 文本输入。
 *
 * 本来排在 P1，被提前做了：Button 统一之后，按钮（h-9=36px）和旧输入框（min-height
 * 40px，且散落在 `.metadata-form input`、`.parsing-toolbar input` 等好几处规则里）
 * 并排就明显高低不齐——解析面板的「重新处理」挨着两个参数框，一眼能看出来。
 * 只统一按钮不统一输入框，等于把不一致从「样式各异」换成「高度打架」。
 *
 * 高度与 Button 对齐：`sm` 28px、`md` 36px，同一行放按钮和输入框时基线一致。
 */
const field = cva(
  // min-w-0 与 w-full 是一对：flex 子项的默认 min-width 是 auto，光有 w-full 时
  // 它不肯收缩，会把同一行的 label 文字挤到换行（问答工作台的「标签」就断成了两行）。
  "w-full min-w-0 rounded-md border bg-surface text-ink placeholder:text-ink-faint " +
    "focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand/20 " +
    "disabled:cursor-not-allowed disabled:bg-canvas disabled:opacity-55",
  {
    variants: {
      size: {
        sm: "h-7 px-2 text-sm",
        md: "h-9 px-2.5 text-md",
      },
      invalid: {
        true: "border-danger",
        false: "border-line-firm",
      },
    },
    defaultVariants: { size: "md", invalid: false },
  },
);

type NativeInput = Omit<InputHTMLAttributes<HTMLInputElement>, "size">;

export type InputProps = NativeInput & VariantProps<typeof field>;

export function Input({ size, invalid, className, ...rest }: InputProps) {
  return (
    <input
      {...rest}
      className={cn(field({ size, invalid }), className)}
      aria-invalid={invalid || undefined}
    />
  );
}

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  invalid?: boolean;
};

/** 多行输入。高度由 `rows` 决定，所以不套用 field 的固定高度。 */
export function Textarea({ invalid, className, rows = 3, ...rest }: TextareaProps) {
  return (
    <textarea
      {...rest}
      rows={rows}
      className={cn(
        "w-full rounded-md border bg-surface px-2.5 py-2 text-md text-ink",
        "placeholder:text-ink-faint focus-visible:outline-none focus-visible:ring-3",
        "focus-visible:ring-brand/20 disabled:cursor-not-allowed disabled:bg-canvas",
        invalid ? "border-danger" : "border-line-firm",
        className,
      )}
      aria-invalid={invalid || undefined}
    />
  );
}
