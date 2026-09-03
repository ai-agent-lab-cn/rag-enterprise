import { cn } from "./cn";

/**
 * 骨架占位。
 *
 * 替代「正在读取知识库…」这类文案：文字高度与表格高度差得远，数据一到页面就跳一下。
 * 骨架按真实行高占位，布局全程不动。
 *
 * 对辅助技术不可见——它是视觉占位，不承载信息，加载状态由外层的 role="status" 承担。
 */
export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden="true" className={cn("block animate-pulse rounded-sm bg-divider", className)} />;
}

/** 表格骨架行。行高必须与 DataTable 一致，否则数据到达时页面仍会跳。 */
export function SkeletonRows({ rows, columns }: { rows: number; columns: number }) {
  return (
    <>
      {Array.from({ length: rows }, (_, row) => (
        <tr key={row} className="h-14 border-b border-divider">
          {Array.from({ length: columns }, (_, column) => (
            <td key={column} className="px-3">
              {/* 宽度交错，避免一列列等宽的骨架看起来像真实数据。 */}
              <Skeleton className={column === 0 ? "h-3 w-32" : "h-3 w-16"} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
