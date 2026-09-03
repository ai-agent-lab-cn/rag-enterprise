import type { ReactNode } from "react";

/**
 * 列表页工具栏。
 *
 * **批量操作区有选中项才出现。** DocumentPanel 现在把「批量归类/应用到 N 份/重新分类
 * N 份」三个控件常驻一排，没勾选时全是死的——用户看到的不是「条件没满足」，
 * 是「功能坏了」。没选中时它们本来无事可做，不该占位置。
 */
export function Toolbar({
  filters,
  actions,
  batch,
}: {
  filters?: ReactNode;
  actions?: ReactNode;
  batch?: { count: number; children: ReactNode };
}) {
  return (
    <div className="grid gap-2 pb-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap items-center gap-2">{filters}</div>
        <div className="ml-auto flex items-center gap-2">{actions}</div>
      </div>
      {batch && batch.count > 0 ? (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-brand/25 bg-brand-subtle px-3 py-2">
          {/* live region 只放文本，不放按钮：批量操作完成后 count 归 0、
              整个区域会被卸载，若按钮在 live region 里，会连同刚点击、
              此刻持有焦点的按钮一起消失，焦点掉回 body。 */}
          <span role="status" className="text-base font-medium text-brand">
            已选 {batch.count} 项
          </span>
          {batch.children}
        </div>
      ) : null}
    </div>
  );
}
