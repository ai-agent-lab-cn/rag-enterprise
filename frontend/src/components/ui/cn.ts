import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * 合并 class：条件拼接（clsx）+ 冲突消解（tailwind-merge）。
 *
 * 后者不可省。`cn("bg-brand", "bg-transparent")` 若只做拼接，两条都会留在
 * className 里，最终哪个生效取决于它们在生成的 CSS 里的先后顺序——调用方无法预测，
 * 也无法通过传 className 来覆盖组件的默认外观。
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
