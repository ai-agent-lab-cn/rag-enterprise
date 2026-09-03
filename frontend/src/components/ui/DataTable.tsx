import { Fragment, type ReactNode } from "react";
import { Checkbox } from "./Checkbox";
import { EmptyState, type EmptyStateProps } from "./EmptyState";
import { SkeletonRows } from "./Skeleton";
import { cn } from "./cn";

/**
 * 全站唯一的表格。
 *
 * 它一次性收掉这些实际存在的问题：
 * - 两套列表实现（`<table class="management-table">` 与 `div[role=table]` + grid）
 * - 三种行高（成员 72px、知识库 59px、文档 52px）
 * - 缺行分隔线（`--border` 等变量从未定义，声明全部失效）
 * - 列宽随内容漂移（宽度写在 th 上，会被内容撑开）
 * - 数字不等宽，整列看着是歪的
 * - checkbox 与首列内容挤在同一个 td 里
 * - 空态不区分「没有」与「没找到」
 * - 加载态是一行文字，数据到达时页面跳一下
 *
 * **`emptyState` 是必填 prop。** 没有空态的表格在类型层面就不存在。
 */
export type Column<T> = {
  key: string;
  header: string;
  align?: "left" | "right";
  /** CSS 宽度，落到 <col> 上。配 table-fixed 才是硬约束。 */
  width?: string;
  /** 等宽数字 + 右对齐。文档数、切片数这类必须开。 */
  numeric?: boolean;
  /** 是否单行截断。默认 true；组合内容（名称+徽章）与操作列必须设为 false。 */
  truncate?: boolean;
  render: (row: T) => ReactNode;
};

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  emptyState,
  label,
  density = "default",
  selection,
  expandedRow,
}: {
  /** null 表示加载中，[] 表示确实没有数据——两者渲染完全不同的东西。 */
  rows: T[] | null;
  columns: Column<T>[];
  rowKey: (row: T) => string;
  emptyState: EmptyStateProps;
  label: string;
  /** compact 仅用于行数可达数千的审计记录页。 */
  density?: "default" | "compact";
  selection?: {
    selected: string[];
    onChange: (selected: string[]) => void;
    /** 每行 checkbox 的可访问名来源，如 (row) => row.filename。 */
    rowLabel: (row: T) => string;
  };
  /** 在数据行下方追加跨全部列的详情区域。返回 null 时不渲染。 */
  expandedRow?: (row: T) => ReactNode;
}) {
  const rowHeight = density === "compact" ? "h-11" : "h-14";
  const columnCount = columns.length + (selection ? 1 : 0);

  if (rows !== null && rows.length === 0) {
    // 空态也要包在与加载态/数据态同一个容器里，否则同一张列表页会出现
    // 「加载态有卡片边框、空态没有」——视觉上像组件坏了。
    return (
      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <EmptyState {...emptyState} />
      </div>
    );
  }

  const keys = rows?.map(rowKey) ?? [];
  const allSelected = Boolean(selection && keys.length > 0 && keys.every((key) => selection.selected.includes(key)));
  const someSelected = Boolean(selection && keys.some((key) => selection.selected.includes(key)));

  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface">
      <table
        aria-label={label}
        aria-busy={rows === null || undefined}
        role="table"
        // table-fixed 是 width 生效的前提：auto 布局下浏览器会按内容重算列宽，
        // <col width> 只被当作建议。
        className="w-full table-fixed border-collapse text-base"
      >
        <colgroup>
          {selection ? <col style={{ width: "44px" }} /> : null}
          {columns.map((column) => (
            <col key={column.key} style={column.width ? { width: column.width } : undefined} />
          ))}
        </colgroup>
        <thead>
          <tr className={cn("border-b border-line bg-canvas", density === "compact" ? "h-9" : "h-11")}>
            {selection ? (
              <th className="px-3">
                <Checkbox
                  checked={allSelected ? true : someSelected ? "indeterminate" : false}
                  onCheckedChange={(next) => selection.onChange(next ? keys : [])}
                  label="选择全部"
                />
              </th>
            ) : null}
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={cn(
                  "px-3 text-sm font-medium text-ink-muted",
                  column.numeric || column.align === "right" ? "text-right" : "text-left",
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows === null ? (
            <SkeletonRows rows={3} columns={columnCount} />
          ) : (
            rows.map((row, index) => {
              const key = rowKey(row);
              const details = expandedRow?.(row);
              return (
                <Fragment key={key}>
                  <tr
                    className={cn(
                      rowHeight,
                      "border-b border-divider hover:bg-canvas",
                      index === rows.length - 1 && !details && "border-b-0",
                    )}
                  >
                    {selection ? (
                      <td className="px-3">
                        <Checkbox
                          checked={selection.selected.includes(key)}
                          onCheckedChange={(next) =>
                            selection.onChange(
                              next
                                ? [...selection.selected, key]
                                : selection.selected.filter((item) => item !== key),
                            )
                          }
                          label={`选择 ${selection.rowLabel(row)}`}
                        />
                      </td>
                    ) : null}
                    {columns.map((column) => (
                      <td
                        key={column.key}
                        className={cn(
                          "px-3 text-ink",
                          (column.truncate ?? true) && "truncate",
                          column.numeric && "tabular-nums text-right",
                          !column.numeric && column.align === "right" && "text-right",
                        )}
                      >
                        {column.render(row)}
                      </td>
                    ))}
                  </tr>
                  {details ? (
                    <tr className={cn("bg-canvas", index !== rows.length - 1 && "border-b border-divider")}>
                      <td colSpan={columnCount} className="px-4 py-3">
                        {details}
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })
          )}
        </tbody>
      </table>
      {/* 加载状态由这里承担，骨架本身对辅助技术不可见。 */}
      {rows === null ? (
        <span role="status" className="sr-only">
          正在读取{label}
        </span>
      ) : null}
    </div>
  );
}
