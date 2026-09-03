import { Checkbox as RadixCheckbox } from "radix-ui";
import { Check, Minus } from "lucide-react";
import { cn } from "./cn";

/**
 * 统一复选框。
 *
 * **换 Radix 的理由是 indeterminate，不是外观。** 原生 checkbox 的第三态只能命令式设置
 * （`el.indeterminate = true`），React 里没有声明式表达——而表格头部的「选择全部」在部分
 * 选中时必须是这个态，否则用户看到「未选中」，点一下却变成全选，与预期相反。
 *
 * `label` 做成必填：一张表里几十个 checkbox 长得一模一样，缺了可访问名，屏幕阅读器
 * 读出来是几十个「复选框」。做成必填就不可能漏。
 *
 * 表单里那种「复选框 + 一行可见文案」的形态开 `showLabel`。它把 `label` 同时用作可见
 * 文案，而不是另开一个 prop：视觉文案与可访问名分成两个来源就一定会分叉
 * （CLAUDE.md 第一条同源原则）。可见文案的样式不开放给调用方——收敛前这两处一处
 * `#4e576c` 一处 `#344054`，同语义两个值正是这轮迁移要消除的东西。
 */
export function Checkbox({
  checked,
  onCheckedChange,
  label,
  showLabel = false,
  className,
}: {
  checked: boolean | "indeterminate";
  onCheckedChange: (checked: boolean) => void;
  /** 可访问名。必填。开 `showLabel` 时同时作为可见文案。 */
  label: string;
  /** 把 `label` 渲染成复选框右侧的可见文案，整行可点。 */
  showLabel?: boolean;
  className?: string;
}) {
  const box = (
    <RadixCheckbox.Root
      checked={checked}
      // indeterminate 点击后 Radix 回传 true（全选），这符合用户预期：
      // 部分选中时点一下是「补齐」，不是「清空」。
      onCheckedChange={(next) => onCheckedChange(next === true)}
      aria-label={label}
      className={cn(
        // border-0 不适用：这个组件的边框就是它的形状。但 preflight 未启用，
        // 需要显式声明 border 的粗细与颜色，不能依赖 UA 默认值。
        "grid h-4 w-4 shrink-0 place-items-center rounded-sm border border-line-firm bg-surface",
        "data-[state=checked]:border-brand data-[state=checked]:bg-brand",
        "data-[state=indeterminate]:border-brand data-[state=indeterminate]:bg-brand",
        "focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand/20",
        className,
      )}
    >
      <RadixCheckbox.Indicator className="text-white">
        {checked === "indeterminate" ? <Minus size={11} strokeWidth={3} /> : <Check size={11} strokeWidth={3} />}
      </RadixCheckbox.Indicator>
    </RadixCheckbox.Root>
  );

  if (!showLabel) return box;

  // 包在 <label> 里而不是加 htmlFor：Radix 的 Root 渲染成 <button>，它是 labelable
  // element，隐式关联成立，点文案能切换——这是原生 checkbox 版本的既有行为，不能丢。
  // aria-label 仍要留在 Root 上：label 元素不参与 button 的可访问名计算（HTML-AAM
  // 只把 label 映射给 input/select/textarea 那几类），删了读屏就没名字了。
  return (
    <label className="flex items-center gap-[7px] text-md font-semibold text-ink-muted">
      {box}
      {label}
    </label>
  );
}
