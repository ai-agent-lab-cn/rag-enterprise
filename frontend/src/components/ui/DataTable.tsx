import { Fragment, type ReactNode } from "react";
import { Checkbox } from "./Checkbox";
import { EmptyState, type EmptyStateProps } from "./EmptyState";
import { SkeletonRows } from "./Skeleton";
import { Tooltip } from "./Tooltip";
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
  /**
   * 截断后要能看到的完整内容。
   *
   * 简单的 string / number 单元格不必写：`render` 返回的就是全文时，DataTable 自动把它
   * 挂成 `title`。只有当 `render` 返回的是徽章、图标这类复合节点、而完整内容另有来源
   * 时才需要它——例如列里显示的是缩写 ID，完整 ID 要在悬停时给出。
   *
   * 返回值走 `delay={0}` 的 Tooltip，hover 与键盘聚焦都能触发；它渲染在 `<td>` 内部
   * 的行内元素上，**不会撑高行**。
   */
  tooltip?: (row: T) => ReactNode;
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
        // 每列至少 120px，否则外层那个 overflow-x-auto 形同虚设：w-full 让表格永远
        // 正好等于容器宽度，窄屏下不会溢出、也就不会滚动，六列全被压成三四个字，
        // 连状态徽章都被截掉一半。实测 412px 视口下的索引版本表就是这样。
        style={{ minWidth: `${(selection ? 44 : 0) + columns.length * 120}px` }}
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
                    {columns.map((column) => {
                      const content = column.render(row);
                      const hint = column.tooltip?.(row);
                      return (
                        <td
                          key={column.key}
                          className={cn(
                            "px-3 text-ink",
                            (column.truncate ?? true) && "truncate",
                            column.numeric && "tabular-nums text-right",
                            !column.numeric && column.align === "right" && "text-right",
                          )}
                          // 简单文本自动带全文 title：被 truncate 截掉的内容此前在任何
                          // 地方都看不到，而要求调用方逐列手写 title 就一定会漏。
                          title={plainTitle(content)}
                        >
                          {hint ? (
                            <Tooltip content={hint} delay={0}>
                              {/* 行内 span + tabIndex：Radix 的 trigger 必须可聚焦，
                                  键盘用户才看得到。inline-block + max-w-full 保证它
                                  不改变行高，也不撑破列宽。 */}
                              <span
                                tabIndex={0}
                                className="inline-block max-w-full truncate align-bottom focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand/20"
                              >
                                {content}
                              </span>
                            </Tooltip>
                          ) : (
                            content
                          )}
                        </td>
                      );
                    })}
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


/**
 * 只给「渲染结果本身就是全文」的单元格加 title。
 *
 * 复合节点（徽章、按钮组、多行结构）拿不到有意义的纯文本，硬拼只会得到一串拼接后的
 * 碎片；那类列请用 `column.tooltip` 显式给出完整内容。
 */
function plainTitle(value: ReactNode): string | undefined {
  if (typeof value === "string") return value.trim() || undefined;
  if (typeof value === "number") return String(value);
  return undefined;
}
