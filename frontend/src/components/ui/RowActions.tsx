import { Button } from "./Button";
import { FileButton } from "./FileButton";

/**
 * 行操作的唯一出口。
 *
 * 所有可用操作直接平铺，避免用户先打开「更多操作」菜单才能发现功能。
 * 常用操作按「详情、编辑、启停、其他、删除」排序，destructive 项强制排到最后——
 * 「删除」紧挨着「编辑」是误点的主要来源，而每个调用点都记得把它写在最后是不现实的。
 */
export type RowAction = {
  label: string;
  onSelect?: () => void;
  /** 给了就渲染 FileButton 而非 Button——用于「更新文件」这类需要弹出文件选择器的操作。与 onSelect 二选一。 */
  file?: { accept: string; onSelect: (files: File[]) => void };
  tone?: "default" | "destructive";
  /** 给了就禁用。见 CLAUDE.md 第一条。 */
  blockedReason?: string | string[];
};

export function RowActions({ actions, rowLabel }: { actions: RowAction[]; rowLabel: string }) {
  if (actions.length === 0) return null;

  const ordered = [...actions].sort((a, b) => actionOrder(a) - actionOrder(b));

  return (
    <div className="flex flex-nowrap items-center justify-end gap-1 max-[767px]:flex-wrap max-[767px]:[&>button]:min-h-11">
      {ordered.map((action) =>
        action.file ? (
          <FileButton
            key={action.label}
            variant="ghost"
            size="sm"
            accept={action.file.accept}
            inputLabel={`${rowLabel} 的${action.label}`}
            blockedReason={action.blockedReason}
            className={action.tone === "destructive" ? "text-danger-text hover:bg-danger-subtle" : undefined}
            onSelect={action.file.onSelect}
          >
            {action.label}
          </FileButton>
        ) : (
          <Button
            key={action.label}
            variant="ghost"
            size="sm"
            blockedReason={action.blockedReason}
            className={action.tone === "destructive" ? "text-danger-text hover:bg-danger-subtle" : undefined}
            onClick={action.onSelect}
          >
            {action.label}
          </Button>
        ),
      )}
    </div>
  );
}

function actionOrder(action: RowAction): number {
  if (action.tone === "destructive" || action.label === "删除") return 4;
  if (action.label === "详情") return 0;
  if (action.label === "编辑") return 1;
  if (action.label === "停用" || action.label === "启用") return 2;
  return 3;
}
