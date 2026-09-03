import type { ReactNode } from "react";
import { cn } from "./cn";

/**
 * 错误横幅。
 *
 * 收敛掉散在 16 个文件里的 23 处 `.error-banner`——它们此前共用一条 legacy 规则，
 * 任何一处想调间距都得改全局。
 *
 * 边框色 `#f0cccc` 与圆角 `7px` 在 `tailwind.css` 的 `@theme` 里没有对应 token
 * （danger 系只有 `--color-danger`/`--color-danger-text`/`--color-danger-subtle`，
 * `--radius-sm` 是 6px、`--radius-md` 是 8px，均非 7px），保留原始任意值。
 *
 * 固定带 `role="alert"`：错误是要打断当前任务的信息，读屏必须立刻播报，
 * 不能等用户 tab 到这里才知道。
 */
export function ErrorBanner({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "mb-3.5 rounded-[7px] border border-[#f0cccc] bg-danger-subtle px-[13px] py-[11px] text-sm text-danger-text",
        className,
      )}
    >
      {children}
    </div>
  );
}
