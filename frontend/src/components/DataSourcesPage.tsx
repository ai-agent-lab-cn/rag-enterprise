import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { DataSource, KnowledgeBase } from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Column, DataTable } from "./ui/DataTable";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Pagination } from "./ui/Pagination";
import { RowAction, RowActions } from "./ui/RowActions";
import { Select } from "./ui/Select";
import { Toolbar } from "./ui/Toolbar";

const INDEX_LABEL = { idle: "未索引", queued: "等待索引", running: "索引中", succeeded: "索引完成", failed: "索引失败" } as const;
// 与 KnowledgeBasesPage 的 STATUS_TONE 同一套约定：queued/running 合并成「进行中」用品牌色，
// 其余三档对应空/成功/失败。原实现（迁移前的 `status-${...}` 拼接）在「非更新中」分支
// 把「未上传」也算成 status-ready（绿色），与文案不符——这里改用真实语义分档，不再沿用那处配色 bug。
const INDEX_TONE: Record<DataSource["index_status"], "neutral" | "brand" | "success" | "danger"> = {
  idle: "neutral", queued: "brand", running: "brand", succeeded: "success", failed: "danger",
};
const SYNC_LABEL: Record<DataSource["sync_status"], string> = { idle: "未同步", queued: "等待同步", running: "同步中", succeeded: "同步完成", failed: "同步失败", aborted: "已中止" };
const SYNC_TONE: Record<DataSource["sync_status"], "neutral" | "brand" | "success" | "danger"> = { idle: "neutral", queued: "brand", running: "brand", succeeded: "success", failed: "danger", aborted: "danger" };
const SOURCE_TYPE_LABEL: Record<string, string> = { file: "上传文件", web: "网页", object_storage: "S3 对象存储", local_directory: "本地目录", connector: "数据库连接器" };
function bytes(value: number) { if (!value) return "0 KB"; const unit = value >= 1024 ** 3 ? [1024 ** 3, "GB"] : value >= 1024 ** 2 ? [1024 ** 2, "MB"] : [1024, "KB"]; return `${(value / Number(unit[0])).toFixed(1)} ${unit[1]}`; }

export function DataSourcesPage({ onOpen }: { onOpen: (path: string) => void }) {
  const [items, setItems] = useState<DataSource[] | null>(null); const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState(""); const [sourceType, setSourceType] = useState("");
  const [error, setError] = useState("");
  const [page, setPage] = useState(0); const [hasNext, setHasNext] = useState(false); const pageSize = 20;
  const load = useCallback(async () => { setError(""); try { const [sources, knowledgeBases] = await Promise.all([api.listDataSources(page * pageSize, pageSize + 1), api.listKnowledgeBases()]); setHasNext(sources.length > pageSize); setItems(sources.slice(0, pageSize)); setBases(knowledgeBases); } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取数据源。"); setItems([]); } }, [page]);
  useEffect(() => { void Promise.resolve().then(load); }, [load]);
  const hasActiveIndexing = items?.some((item) => item.index_status === "queued" || item.index_status === "running") ?? false;
  useEffect(() => {
    if (!hasActiveIndexing) return undefined;
    const timer = window.setInterval(() => { void load(); }, 1_000);
    return () => window.clearInterval(timer);
  }, [hasActiveIndexing, load]);
  const rowActions = (item: DataSource): RowAction[] => {
    const target = `/knowledge-bases/${item.knowledge_base_id}${item.source_type === "file" ? "" : "?tab=data_sources"}`;
    return [{ label: item.source_type === "file" ? "进入资料" : "进入管理", onSelect: () => onOpen(target) }];
  };
  const visibleItems = (items || []).filter((item) => (!knowledgeBaseId || item.knowledge_base_id === knowledgeBaseId) && (!sourceType || item.source_type === sourceType));
  const columns: Column<DataSource>[] = [
    {
      key: "name", header: "数据源", width: "170px", truncate: false,
      render: (item) => (
        <span className="flex min-w-0 items-center gap-2">
          <strong className="min-w-0 truncate font-medium text-ink" title={item.name}>{item.name}</strong>
          {!item.enabled ? <Badge shape="type" className="shrink-0">已停用</Badge> : null}
        </span>
      ),
    },
    { key: "type", header: "类型", width: "110px", render: (item) => SOURCE_TYPE_LABEL[item.source_type] || item.source_type },
    {
      key: "knowledge_base", header: "所属知识库", width: "140px", truncate: false,
      render: (item) => (
        <Button variant="link" className="min-w-0 justify-start truncate font-medium text-ink no-underline hover:text-brand hover:no-underline" onClick={() => onOpen(`/knowledge-bases/${item.knowledge_base_id}`)}>
          {item.knowledge_base_name}
        </Button>
      ),
    },
    {
      key: "processing_status", header: "处理状态", width: "120px",
      render: (item) => {
        const isFile = item.source_type === "file";
        const processing = isFile ? item.index_status === "running" || item.index_status === "queued" : item.sync_status === "running" || item.sync_status === "queued";
        // Badge 不透传任意 DOM 属性（见 ui/Badge.tsx），aria-busy 挂不到它自己的 <span> 上，
        // 所以用一层不带样式的包装 span 承载它；旋转动画的 utility class 仍作为
        // className 传给 Badge 本体，保证视觉上动画还是画在徽章胶囊自身（与迁移前一致）。
        return (
          <span aria-busy={processing || undefined}>
            <Badge shape="status" tone={isFile ? INDEX_TONE[item.index_status] : SYNC_TONE[item.sync_status]} className={processing ? "before:content-[''] before:w-[9px] before:h-[9px] before:border-[1.5px] before:border-solid before:border-current before:border-r-transparent before:rounded-full before:[animation:spin_0.7s_linear_infinite]" : undefined}>
              {isFile ? INDEX_LABEL[item.index_status] : SYNC_LABEL[item.sync_status]}
            </Badge>
          </span>
        );
      },
    },
    { key: "source_file_bytes", header: "存储空间", width: "90px", numeric: true, render: (item) => bytes(item.source_file_bytes) },
    { key: "last_processed_at", header: "最近处理", width: "150px", render: (item) => { const value = item.source_type === "file" ? item.last_indexed_at : item.last_synced_at; return value ? new Date(value).toLocaleString("zh-CN") : "—"; } },
    { key: "failure_reason", header: "失败原因", width: "150px", render: (item) => <span title={item.failure_reason || "—"}>{item.failure_reason || "—"}</span> },
    { key: "actions", header: "操作", width: "210px", align: "right", truncate: false, render: (item) => <RowActions rowLabel={item.name} actions={rowActions(item)} /> },
  ];
  return <section className="mx-auto max-w-[1440px] p-[26px_24px_52px] min-[1025px]:p-[20px_20px_40px]" aria-label="数据源管理">
    <Toolbar
      filters={<><Select size="sm" className="w-44" aria-label="知识库筛选" value={knowledgeBaseId} onChange={(event) => setKnowledgeBaseId(event.target.value)}><option value="">全部知识库</option>{bases.map((item) => <option key={item.knowledge_base_id} value={item.knowledge_base_id}>{item.name}</option>)}</Select><Select size="sm" className="w-40" aria-label="来源类型筛选" value={sourceType} onChange={(event) => setSourceType(event.target.value)}><option value="">全部来源</option><option value="file">上传文件</option><option value="web">网页</option><option value="object_storage">S3 对象存储</option><option value="local_directory">本地目录</option><option value="connector">数据库连接器</option></Select></>}
    />
    {error ? (
      <ErrorBanner>{error} <Button variant="ghost" size="sm" onClick={() => void load()}>重试</Button></ErrorBanner>
    ) : (
      <>
        <DataTable
          rows={visibleItems}
          columns={columns}
          rowKey={(item) => item.data_source_id}
          label="数据源列表"
          emptyState={{ kind: "empty", title: "还没有数据源", description: "请进入具体知识库上传文件或接入外部数据源。" }}
        />
        {items !== null ? <Pagination page={page} hasNext={hasNext} onChange={setPage} label="数据源分页" /> : null}
      </>
    )}
  </section>;
}
