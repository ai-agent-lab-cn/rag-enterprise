import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { DataSource, KnowledgeBase } from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Column, DataTable } from "./ui/DataTable";
import { ErrorBanner } from "./ui/ErrorBanner";
import { FileButton } from "./ui/FileButton";
import { Pagination } from "./ui/Pagination";
import { RowAction, RowActions } from "./ui/RowActions";
import { Select } from "./ui/Select";
import { Toolbar } from "./ui/Toolbar";
import { useConfirm } from "./ui/useConfirm";
import { useToast } from "./ui/Toast";

const INDEX_LABEL = { idle: "未索引", queued: "等待索引", running: "索引中", succeeded: "索引完成", failed: "索引失败" } as const;
// 与 KnowledgeBasesPage 的 STATUS_TONE 同一套约定：queued/running 合并成「进行中」用品牌色，
// 其余三档对应空/成功/失败。原实现（迁移前的 `status-${...}` 拼接）在「非更新中」分支
// 把「未上传」也算成 status-ready（绿色），与文案不符——这里改用真实语义分档，不再沿用那处配色 bug。
const INDEX_TONE: Record<DataSource["index_status"], "neutral" | "brand" | "success" | "danger"> = {
  idle: "neutral", queued: "brand", running: "brand", succeeded: "success", failed: "danger",
};
/** 与后端 parsers.py 支持的扩展名一致；上传和更新走同一份，避免两处漂移。 */
const UPLOAD_ACCEPT = ".pdf,.docx,.txt,.md,.html,.htm,.xlsx,.csv";
function bytes(value: number) { if (!value) return "0 KB"; const unit = value >= 1024 ** 3 ? [1024 ** 3, "GB"] : value >= 1024 ** 2 ? [1024 ** 2, "MB"] : [1024, "KB"]; return `${(value / Number(unit[0])).toFixed(1)} ${unit[1]}`; }

export function DataSourcesPage({ onOpen }: { onOpen: (path: string) => void }) {
  const [items, setItems] = useState<DataSource[] | null>(null); const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState("kb_default"); const [uploading, setUploading] = useState(false);
  const [updatingId, setUpdatingId] = useState(""); const [error, setError] = useState("");
  const [page, setPage] = useState(0); const [hasNext, setHasNext] = useState(false); const pageSize = 20;
  const { confirm, dialog: confirmDialog } = useConfirm();
  const toast = useToast();
  const load = useCallback(async () => { setError(""); try { const [sources, knowledgeBases] = await Promise.all([api.listDataSources(page * pageSize, pageSize + 1), api.listKnowledgeBases()]); setHasNext(sources.length > pageSize); setItems(sources.slice(0, pageSize)); setBases(knowledgeBases); if (!knowledgeBases.some((item) => item.knowledge_base_id === knowledgeBaseId) && knowledgeBases[0]) setKnowledgeBaseId(knowledgeBases[0].knowledge_base_id); } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取数据源。"); setItems([]); } }, [knowledgeBaseId, page]);
  useEffect(() => { void Promise.resolve().then(load); }, [load]);
  const hasActiveIndexing = items?.some((item) => item.index_status === "queued" || item.index_status === "running") ?? false;
  useEffect(() => {
    if (!hasActiveIndexing) return undefined;
    const timer = window.setInterval(() => { void load(); }, 1_000);
    return () => window.clearInterval(timer);
  }, [hasActiveIndexing, load]);
  const upload = async (file: File) => {
    setUploading(true);
    try {
      const result = await api.uploadKnowledgeBaseDocument(knowledgeBaseId, file);
      toast.success(result.status === "ready" ? `“${file.name}”上传成功，内容未变化。` : `“${file.name}”上传成功，等待索引。`);
      await load();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "上传失败。");
    } finally { setUploading(false); }
  };
  const updateFile = async (item: DataSource, file: File) => {
    if (file.name !== item.name) { toast.error(`请选择同名文件“${item.name}”，避免意外创建新的数据源。`); return; }
    setUpdatingId(item.data_source_id);
    try {
      const result = await api.uploadKnowledgeBaseDocument(item.knowledge_base_id, file);
      toast.success(result.status === "ready" ? `“${item.name}”内容未变化，无需创建新版本。` : `“${item.name}”的新版本已上传并加入索引队列。`);
      await load();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "文件更新失败。");
    } finally { setUpdatingId(""); }
  };
  const setEnabled = async (item: DataSource, enabled: boolean) => {
    try {
      await api.setDataSourceEnabled(item.data_source_id, enabled);
      toast.success(enabled ? `已启用「${item.name}」` : `已停用「${item.name}」`);
      await load();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "操作失败。");
    }
  };
  const startDelete = (item: DataSource) => {
    confirm({
      title: "删除数据源",
      consequence: `将删除「${item.name}」。关联文档必须先由知识库详情页显式删除。`,
      confirmLabel: "确认删除",
      tone: "destructive",
      onConfirm: async () => {
        try {
          await api.deleteDataSource(item.data_source_id);
          toast.success(`已删除「${item.name}」`);
          await load();
        } catch (reason) {
          toast.error(reason instanceof Error ? reason.message : "删除失败。");
          throw reason;
        }
      },
    });
  };
  const rowActions = (item: DataSource): RowAction[] => {
    const actions: RowAction[] = [];
    const isIndexing = item.index_status === "running" || item.index_status === "queued";
    if (item.source_type === "file" && item.allowed_actions.includes("update_file")) {
      actions.push({
        label: "更新文件",
        file: { accept: UPLOAD_ACCEPT, onSelect: (files) => void updateFile(item, files[0]) },
        blockedReason: !item.enabled ? "数据源已停用" : isIndexing ? "索引进行中" : undefined,
      });
    }
    if (item.allowed_actions.includes("disable")) actions.push({ label: "停用", onSelect: () => void setEnabled(item, false) });
    if (item.allowed_actions.includes("enable")) actions.push({ label: "启用", onSelect: () => void setEnabled(item, true) });
    if (item.allowed_actions.includes("delete")) actions.push({ label: "删除", tone: "destructive", onSelect: () => startDelete(item) });
    return actions;
  };
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
    {
      key: "knowledge_base", header: "所属知识库", width: "140px", truncate: false,
      render: (item) => (
        <Button variant="link" className="min-w-0 justify-start truncate font-medium text-ink no-underline hover:text-brand hover:no-underline" onClick={() => onOpen(`/knowledge-bases/${item.knowledge_base_id}`)}>
          {item.knowledge_base_name}
        </Button>
      ),
    },
    {
      key: "upload_status", header: "上传状态", width: "90px",
      render: (item) => { const updating = updatingId === item.data_source_id; return <Badge shape="status" tone={updating ? "brand" : item.upload_status === "succeeded" ? "success" : "neutral"}>{updating ? "上传中" : item.upload_status === "succeeded" ? "上传成功" : "未上传"}</Badge>; },
    },
    {
      key: "index_status", header: "索引状态", width: "100px",
      render: (item) => {
        const isIndexing = item.index_status === "running" || item.index_status === "queued";
        // Badge 不透传任意 DOM 属性（见 ui/Badge.tsx），aria-busy 挂不到它自己的 <span> 上，
        // 所以用一层不带样式的包装 span 承载它；旋转动画的 utility class 仍作为
        // className 传给 Badge 本体，保证视觉上动画还是画在徽章胶囊自身（与迁移前一致）。
        return (
          <span aria-busy={isIndexing || undefined}>
            <Badge shape="status" tone={INDEX_TONE[item.index_status]} className={isIndexing ? "before:content-[''] before:w-[9px] before:h-[9px] before:border-[1.5px] before:border-solid before:border-current before:border-r-transparent before:rounded-full before:[animation:spin_0.7s_linear_infinite]" : undefined}>
              {INDEX_LABEL[item.index_status]}
            </Badge>
          </span>
        );
      },
    },
    { key: "source_file_bytes", header: "存储空间", width: "90px", numeric: true, render: (item) => bytes(item.source_file_bytes) },
    { key: "last_indexed_at", header: "最后索引", width: "150px", render: (item) => item.last_indexed_at ? new Date(item.last_indexed_at).toLocaleString("zh-CN") : "—" },
    { key: "failure_reason", header: "失败原因", width: "150px", render: (item) => <span title={item.failure_reason || "—"}>{item.failure_reason || "—"}</span> },
    { key: "actions", header: "操作", width: "210px", align: "right", truncate: false, render: (item) => <RowActions rowLabel={item.name} actions={rowActions(item)} /> },
  ];
  return <section className="mx-auto max-w-[1440px] p-[26px_24px_52px] min-[1025px]:p-[20px_20px_40px]" aria-label="数据源管理">
    <Toolbar
      filters={<>
        <Select size="sm" className="w-44" aria-label="上传到知识库" value={knowledgeBaseId} onChange={(event) => setKnowledgeBaseId(event.target.value)}>{bases.map((item) => <option key={item.knowledge_base_id} value={item.knowledge_base_id}>{item.name}</option>)}</Select>
        <FileButton size="sm" loading={uploading} blockedReason={bases.length ? undefined : "请先创建知识库"} accept={UPLOAD_ACCEPT} inputLabel="上传文件数据源" onSelect={(files) => void upload(files[0])}>＋ 文件数据源</FileButton>
      </>}
    />
    {error ? (
      <ErrorBanner>{error} <Button variant="ghost" size="sm" onClick={() => void load()}>重试</Button></ErrorBanner>
    ) : (
      <>
        <DataTable
          rows={items}
          columns={columns}
          rowKey={(item) => item.data_source_id}
          label="数据源列表"
          emptyState={{ kind: "empty", title: "还没有数据源", description: "选择知识库并上传文件，系统将创建可追溯的文件数据源。" }}
        />
        {items !== null ? <Pagination page={page} hasNext={hasNext} onChange={setPage} label="数据源分页" /> : null}
      </>
    )}
    {confirmDialog}
  </section>;
}
