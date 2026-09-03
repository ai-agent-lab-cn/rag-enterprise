import { useEffect, useState } from "react";
import { api } from "../api";
import type { DataSource, DocumentCategory, SyncRun } from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Checkbox } from "./ui/Checkbox";
import { Column, DataTable } from "./ui/DataTable";
import { Dialog, DialogActions } from "./ui/Dialog";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input } from "./ui/Input";
import { RowAction, RowActions } from "./ui/RowActions";
import { Select } from "./ui/Select";
import { Toolbar } from "./ui/Toolbar";
import { useConfirm } from "./ui/useConfirm";
import { useToast } from "./ui/Toast";

const SYNC_LABEL: Record<DataSource["sync_status"], string> = {
  idle: "未同步", queued: "等待同步", running: "同步中", succeeded: "同步完成", failed: "同步失败", aborted: "已熔断",
};
/**
 * 有意继续用 `sync_status`，不跟 DataSourcesPage 换成 `index_status`。
 * `index_status` 的合法集合不含 `aborted`（见 backend/app/main.py:2043-2045），熔断态会被
 * 折叠成 `idle`——对这个面板管的 S3/本地目录同步治理场景，"已熔断"和"从未同步过"是完全
 * 不同的信息（前者是 `SYNC_DELETE_CIRCUIT_BREAKER` 触发的保护性中止，见
 * backend/app/data_source_sync.py:532），跟着换会丢状态，不是单纯的样式迁移。
 */
const SYNC_TONE: Record<DataSource["sync_status"], "neutral" | "brand" | "success" | "danger"> = {
  idle: "neutral", queued: "brand", running: "brand", succeeded: "success", failed: "danger", aborted: "danger",
};
/** 与 DataSourcesPage 的 UPLOAD_ACCEPT 同一份，避免两处漂移。 */
const UPLOAD_ACCEPT = ".pdf,.docx,.txt,.md,.html,.htm,.xlsx,.csv";
const EMPTY_DRAFT = { name: "", endpoint: "", bucket: "", prefix: "", region: "", credentialEnv: "", secure: true, categoryId: "", department: "" };
type Props = { knowledgeBaseId: string; items: DataSource[]; categories: DocumentCategory[]; onRefresh: () => Promise<void> };

export function KnowledgeBaseDataSourcesPanel({ knowledgeBaseId, items, categories, onRefresh }: Props) {
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<DataSource | null>(null);
  const [formError, setFormError] = useState("");
  const [historyFor, setHistoryFor] = useState<DataSource | null>(null);
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const toast = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();

  useEffect(() => {
    if (!items.some((item) => item.sync_status === "queued" || item.sync_status === "running")) return;
    const timer = window.setInterval(() => { void onRefresh(); }, 1500);
    return () => window.clearInterval(timer);
  }, [items, onRefresh]);

  const act = async (id: string, action: () => Promise<unknown>, message: string) => {
    setBusyId(id);
    try { await action(); toast.success(message); await onRefresh(); }
    catch (reason) { toast.error(reason instanceof Error ? reason.message : "操作失败。"); }
    finally { setBusyId(""); }
  };
  const openCreate = () => { setEditing(null); setDraft(EMPTY_DRAFT); setFormError(""); setFormOpen(true); };
  const openEdit = (item: DataSource) => {
    const config = item.configuration || {};
    setEditing(item);
    setDraft({
      name: item.name, endpoint: String(config.endpoint || ""), bucket: String(config.bucket || ""),
      prefix: String(config.prefix || ""), region: String(config.region || ""),
      credentialEnv: String(config.credential_env || ""), secure: config.secure !== false,
      categoryId: item.default_category_id || "", department: String(item.metadata_defaults?.department || ""),
    });
    setFormError("");
    setFormOpen(true);
  };
  const save = async () => {
    setBusyId("save"); setFormError("");
    const configuration = { endpoint: draft.endpoint.trim(), bucket: draft.bucket.trim(), prefix: draft.prefix.trim(), region: draft.region.trim() || null, secure: draft.secure, credential_env: draft.credentialEnv.trim() };
    const payload = { name: draft.name.trim(), configuration, default_category_id: draft.categoryId || null, metadata_defaults: draft.department.trim() ? { department: draft.department.trim() } : {} };
    try {
      if (editing) await api.updateDataSource(editing.data_source_id, payload);
      else await api.createDataSource(knowledgeBaseId, { ...payload, source_type: "object_storage" });
      toast.success(editing ? "数据源配置已更新。" : "外部数据源已创建，可先测试连接再同步。");
      setFormOpen(false); setEditing(null); await onRefresh();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "保存失败。";
      setFormError(message); toast.error(message);
    } finally { setBusyId(""); }
  };
  const updateFile = async (item: DataSource, file: File) => {
    if (file.name !== item.name) { toast.error(`请选择同名文件“${item.name}”，避免意外创建新的数据源。`); return; }
    setBusyId(item.data_source_id);
    try {
      const result = await api.uploadKnowledgeBaseDocument(item.knowledge_base_id, file);
      toast.success(result.status === "ready" ? `“${item.name}”内容未变化，无需创建新版本。` : `“${item.name}”的新版本已上传并加入索引队列。`);
      await onRefresh();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "文件更新失败。");
    } finally { setBusyId(""); }
  };
  const openHistory = async (item: DataSource) => {
    setHistoryFor(item); setRuns([]); setHistoryError("");
    try { setRuns(await api.listDataSourceSyncRuns(item.data_source_id)); }
    catch (reason) { setHistoryError(reason instanceof Error ? reason.message : "同步记录读取失败。"); }
  };
  const startDelete = (item: DataSource) => {
    confirm({
      title: "删除数据源",
      consequence: `将删除「${item.name}」。已同步进来的资料不会被删除，但该数据源将不再参与后续同步。`,
      confirmLabel: "确认删除",
      tone: "destructive",
      onConfirm: async () => {
        try {
          await api.deleteDataSource(item.data_source_id);
          toast.success(`已删除「${item.name}」`);
          await onRefresh();
        } catch (reason) {
          toast.error(reason instanceof Error ? reason.message : "删除失败。");
          throw reason;
        }
      },
    });
  };

  const rowActions = (item: DataSource): RowAction[] => {
    const syncing = item.sync_status === "queued" || item.sync_status === "running";
    const busy = Boolean(busyId);
    const actions: RowAction[] = [];
    // 与 DataSourcesPage 的「更新文件」分支保持一致（同一个 allowed_action、同一个
    // uploadKnowledgeBaseDocument 接口）——这个面板原来完全没有这个入口，是迁移中补上的
    // 缺口，见任务报告。
    if (item.source_type === "file" && item.allowed_actions.includes("update_file")) {
      actions.push({
        label: "更新文件",
        file: { accept: UPLOAD_ACCEPT, onSelect: (files) => void updateFile(item, files[0]) },
        blockedReason: !item.enabled ? "数据源已停用" : syncing ? "索引进行中" : undefined,
      });
    }
    if (item.source_type === "object_storage" && item.allowed_actions.includes("edit")) {
      actions.push({ label: "编辑", blockedReason: busy ? "处理中" : syncing ? "同步进行中" : undefined, onSelect: () => openEdit(item) });
    }
    if (item.allowed_actions.includes("test")) {
      actions.push({ label: "测试连接", blockedReason: busy ? "处理中" : undefined, onSelect: () => void act(item.data_source_id, () => api.testDataSource(item.data_source_id), "连接测试通过。") });
    }
    if (item.allowed_actions.includes("sync")) {
      actions.push({ label: "同步", blockedReason: busy ? "处理中" : syncing ? "同步进行中" : undefined, onSelect: () => void act(item.data_source_id, () => api.syncDataSource(item.data_source_id), "同步任务已进入队列。") });
    }
    // 「记录」不受 busy/syncing 影响，与迁移前一致：查看同步历史不应该被别的操作挡住。
    actions.push({ label: "记录", onSelect: () => void openHistory(item) });
    if (item.sync_status === "failed" || item.sync_status === "aborted") {
      actions.push({ label: "重试", blockedReason: busy ? "处理中" : undefined, onSelect: () => void act(item.data_source_id, () => api.retryDataSource(item.data_source_id), "失败同步已重新入队。") });
    }
    if (item.allowed_actions.includes(item.enabled ? "disable" : "enable")) {
      actions.push({
        label: item.enabled ? "停用" : "启用",
        blockedReason: busy ? "处理中" : syncing ? "同步进行中" : undefined,
        onSelect: () => void act(item.data_source_id, () => api.setDataSourceEnabled(item.data_source_id, !item.enabled), item.enabled ? "数据源已停用。" : "数据源已启用。"),
      });
    }
    if (item.allowed_actions.includes("delete")) {
      actions.push({ label: "删除", tone: "destructive", blockedReason: busy ? "处理中" : undefined, onSelect: () => startDelete(item) });
    }
    return actions;
  };

  const columns: Column<DataSource>[] = [
    {
      key: "name", header: "数据源", width: "180px", truncate: false,
      render: (item) => (
        <span className="flex min-w-0 items-center gap-2">
          <strong className="min-w-0 truncate font-medium text-ink" title={item.name}>{item.name}</strong>
          {!item.enabled ? <Badge shape="type" className="shrink-0">已停用</Badge> : null}
        </span>
      ),
    },
    {
      key: "source_type", header: "类型", width: "110px",
      render: (item) => (item.source_type === "object_storage" ? "S3 对象存储" : item.source_type === "local_directory" ? "本地目录" : "文件"),
    },
    {
      key: "sync_status", header: "同步状态", width: "150px", truncate: false,
      render: (item) => {
        const syncing = item.sync_status === "queued" || item.sync_status === "running";
        return (
          <>
            {/* Badge 不透传任意 DOM 属性（见 ui/Badge.tsx），aria-busy 挂不到它自己的
                <span> 上，用一层不带样式的包装 span 承载，与 DataSourcesPage 的
                index_status 列同一处理方式。 */}
            <span aria-busy={syncing || undefined}>
              <Badge shape="status" tone={SYNC_TONE[item.sync_status]} className={syncing ? "before:content-[''] before:w-[9px] before:h-[9px] before:border-[1.5px] before:border-solid before:border-current before:border-r-transparent before:rounded-full before:[animation:spin_0.7s_linear_infinite]" : undefined}>
                {SYNC_LABEL[item.sync_status]}
              </Badge>
            </span>
            {syncing ? <progress aria-label={`${item.name} 同步进度`} className="mt-[5px] block h-1 w-[110px] accent-brand" /> : null}
          </>
        );
      },
    },
    { key: "document_count", header: "资料数", width: "65px", numeric: true, render: (item) => item.document_count },
    { key: "last_synced_at", header: "最近同步", width: "145px", render: (item) => (item.last_synced_at ? new Date(item.last_synced_at).toLocaleString("zh-CN") : "—") },
    { key: "failure_reason", header: "失败原因", width: "150px", render: (item) => <span title={item.failure_reason || "—"}>{item.failure_reason || "—"}</span> },
    { key: "actions", header: "操作", width: "440px", align: "right", truncate: false, render: (item) => <RowActions rowLabel={item.name} actions={rowActions(item)} /> },
  ];

  return <section aria-label="知识库数据源" className="grid gap-3">
    <Toolbar actions={<Button size="sm" onClick={openCreate}>新建外部数据源</Button>} />
    <DataTable
      rows={items}
      columns={columns}
      rowKey={(item) => item.data_source_id}
      label="数据源列表"
      emptyState={{ kind: "empty", title: "当前知识库没有数据源", description: "新建外部数据源以同步 S3 兼容存储中的资料，或前往「资料」Tab 上传文件。" }}
    />
    {formOpen ? <Dialog open size="md" title={editing ? "编辑 S3 兼容数据源" : "新建 S3 兼容数据源"} description="凭据只读取运行环境变量，不保存到数据库。" onClose={() => { if (!busyId) { setFormOpen(false); setEditing(null); } }}>
      <form className="grid gap-[9px] pt-[20px] px-[22px]" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        {formError ? <ErrorBanner>{formError}</ErrorBanner> : null}
        <label className="text-[#4e576c] text-[13px] font-semibold">名称<Input className="py-[10px]" required value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })}/></label>
        <label className="text-[#4e576c] text-[13px] font-semibold">Endpoint<Input className="py-[10px]" required placeholder="s3.example.com" value={draft.endpoint} onChange={(event) => setDraft({ ...draft, endpoint: event.target.value })}/></label>
        <label className="text-[#4e576c] text-[13px] font-semibold">Bucket<Input className="py-[10px]" required value={draft.bucket} onChange={(event) => setDraft({ ...draft, bucket: event.target.value })}/></label>
        <label className="text-[#4e576c] text-[13px] font-semibold">Prefix<Input className="py-[10px]" value={draft.prefix} onChange={(event) => setDraft({ ...draft, prefix: event.target.value })}/></label>
        <label className="text-[#4e576c] text-[13px] font-semibold">Region<Input className="py-[10px]" value={draft.region} onChange={(event) => setDraft({ ...draft, region: event.target.value })}/></label>
        <label className="text-[#4e576c] text-[13px] font-semibold">凭据环境变量前缀<Input className="py-[10px]" required placeholder="ENTERPRISE_DOCS" value={draft.credentialEnv} onChange={(event) => setDraft({ ...draft, credentialEnv: event.target.value })}/></label>
        <label className="text-[#4e576c] text-[13px] font-semibold">默认分类<Select value={draft.categoryId} onChange={(event) => setDraft({ ...draft, categoryId: event.target.value })}><option value="">不指定分类</option>{categories.filter((item) => item.active).map((item) => <option key={item.category_id} value={item.category_id}>{item.name}</option>)}</Select></label>
        <label className="text-[#4e576c] text-[13px] font-semibold">默认部门<Input className="py-[10px]" value={draft.department} onChange={(event) => setDraft({ ...draft, department: event.target.value })}/></label>
        <Checkbox showLabel label="使用 HTTPS" checked={draft.secure} onCheckedChange={(next) => setDraft({ ...draft, secure: next })}/>
        <DialogActions><Button variant="secondary" loading={!!busyId} onClick={() => setFormOpen(false)}>取消</Button><Button type="submit" loading={!!busyId}>{busyId ? "保存中…" : "保存"}</Button></DialogActions>
      </form>
    </Dialog> : null}
    {historyFor ? <Dialog open size="md" title="同步记录" description={historyFor.name} onClose={() => setHistoryFor(null)}>
      {historyError ? <ErrorBanner>{historyError}</ErrorBanner> : null}
      <div className="grid gap-0 max-h-[420px] overflow-auto">
        {runs.length ? runs.map((run) => (
          <div key={run.sync_run_id} className="grid gap-1 border-b border-divider px-[22px] py-3">
            <strong>{run.status} · {run.stage}</strong>
            <span className="text-base text-ink-faint">新增 {run.added_count} / 更新 {run.updated_count} / 删除 {run.deleted_count} / 跳过 {run.skipped_count} / 失败 {run.failed_count}</span>
            <small className="text-base text-ink-faint">{new Date(run.created_at).toLocaleString("zh-CN")}{run.failure_reason ? ` · ${run.error_code || "ERROR"}: ${run.failure_reason}` : ""}</small>
          </div>
        )) : <p className="text-md text-[#737c90] leading-[1.6] mt-[13px] mb-[13px]">暂无同步记录。</p>}
      </div>
    </Dialog> : null}
    {confirmDialog}
  </section>;
}
