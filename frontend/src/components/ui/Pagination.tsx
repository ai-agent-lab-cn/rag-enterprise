import { Button } from "./Button";

/**
 * 分页。抽自 KnowledgeBasesPage.tsx:44 与 DataSourcesPage.tsx:38——两处逐字相同。
 *
 * **只有一页时整个组件不渲染。** 三行数据下面挂一个「第 1 页」加两个灰按钮是纯噪音。
 * 旧实现把这个判断留在调用方（`{items && (page > 0 || hasNext) ? ... : null}`），
 * 于是每个新页面都要记得抄一遍；收进组件里就不会漏。
 *
 * 页码内部 0-based、显示 1-based，与两处旧实现一致。
 */
export function Pagination({
  page,
  hasNext,
  onChange,
  label,
}: {
  /** 0-based。 */
  page: number;
  hasNext: boolean;
  onChange: (page: number) => void;
  /** 导航的可访问名，如「知识库分页」。 */
  label: string;
}) {
  if (page === 0 && !hasNext) return null;
  return (
    <nav className="mt-3 flex items-center justify-end gap-2 text-base text-ink-muted" aria-label={label}>
      <Button
        variant="outline"
        size="sm"
        blockedReason={page === 0 ? "已经是第一页" : undefined}
        onClick={() => onChange(Math.max(0, page - 1))}
      >
        上一页
      </Button>
      <span>第 {page + 1} 页</span>
      <Button
        variant="outline"
        size="sm"
        blockedReason={hasNext ? undefined : "没有下一页"}
        onClick={() => onChange(page + 1)}
      >
        下一页
      </Button>
    </nav>
  );
}
