import type { ReactNode } from "react";
import { cn } from "./cn";

/**
 * 指标卡。
 *
 * **数值的字阶和字重与内容类型无关。** 旧实现给文字值加了 `valueClass`，于是「3」和
 * 「通过」的视觉重量完全不同，四张卡并排读起来不像一组。这里数值样式是固定的，
 * `tone` 只改颜色。
 *
 * **图标底色恒为中性。** 旧实现有 6 套装饰底色（is-purple/green/blue/amber/slate/gray），
 * 它们不携带任何信息——颜色只留给数值本身表意。
 */
export function MetricCard({
  icon,
  label,
  value,
  note,
  tone = "neutral",
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  note?: string;
  tone?: "neutral" | "success" | "danger";
}) {
  return (
    <article className="grid gap-1.5 rounded-lg border border-line bg-surface p-4">
      <div className="flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded-md bg-canvas text-ink-muted">{icon}</span>
        <span className="text-base text-ink-muted">{label}</span>
      </div>
      <strong
        className={cn(
          "text-xl font-semibold tabular-nums",
          tone === "success" && "text-success",
          tone === "danger" && "text-danger-text",
          tone === "neutral" && "text-ink",
        )}
      >
        {value}
      </strong>
      {note ? <small className="text-sm text-ink-faint">{note}</small> : null}
    </article>
  );
}
