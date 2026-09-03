import { Inbox, SearchX } from "lucide-react";
import { Button } from "./Button";

/**
 * 空态。
 *
 * **kind 是必填的。** 「一条都没有」和「筛选后没找到」是两回事：前者要引导创建，
 * 后者要引导放宽条件。DocumentPanel.tsx:113 现在两种情况都显示「还没有资料」，
 * 用户筛完看到它会以为资料被删了。做成必填，抄的时候不可能漏。
 */
export type EmptyStateProps = {
  kind: "empty" | "filtered";
  title: string;
  description: string;
  action?: { label: string; onClick: () => void };
};

export function EmptyState({ kind, title, description, action }: EmptyStateProps) {
  const Icon = kind === "empty" ? Inbox : SearchX;
  return (
    <div className="grid justify-items-center gap-2 px-4 py-14 text-center">
      <Icon size={28} className="text-ink-faint" aria-hidden />
      <h2 className="text-md font-semibold text-ink">{title}</h2>
      <p className="max-w-80 text-base text-ink-muted">{description}</p>
      {action ? (
        <Button className="mt-1" onClick={action.onClick}>
          {action.label}
        </Button>
      ) : null}
    </div>
  );
}
