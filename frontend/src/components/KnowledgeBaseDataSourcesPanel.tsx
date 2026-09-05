import { useEffect, useState } from "react";
import { api } from "../api";
import type { DataSource, DocumentCategory, SyncResourceRun, SyncRun } from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Checkbox } from "./ui/Checkbox";
import { type Column, DataTable } from "./ui/DataTable";
import { Dialog, DialogActions } from "./ui/Dialog";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input } from "./ui/Input";
import { PipelineStepper } from "./ui/PipelineStepper";
import { RowAction, RowActions } from "./ui/RowActions";
import { Select } from "./ui/Select";
import { Toolbar } from "./ui/Toolbar";
import { useConfirm } from "./ui/useConfirm";
import { useToast } from "./ui/Toast";

const SYNC_LABEL: Record<DataSource["sync_status"], string> = {
  idle: "未同步", queued: "等待同步", running: "同步中", succeeded: "同步完成", failed: "同步失败", aborted: "已熔断",
};
const RESOURCE_STATUS: Record<string, string> = { discovered: "已发现", fetching: "读取中", normalizing: "规范化中", parsing: "解析中", chunking: "切片中", enriching: "治理中", building: "构建索引", validating: "验证中", activated: "已激活", succeeded: "已完成", unchanged: "无变化", skipped: "已跳过", deleted: "已删除", failed: "失败", dead_letter: "死信", cancelled: "已取消" };
const RUN_STATUS: Record<string, string> = { queued: "等待同步", discovering: "资源发现中", syncing: "差异处理中", indexing: "索引处理中", succeeded: "同步完成", partial_failed: "部分失败", aborted: "已取消或熔断", failed: "同步失败" };
const STAGE_LABEL: Record<string, string> = { discover: "资源发现", diff: "差异计算", fetch: "内容获取", normalize: "内容规范化", build: "索引构建", retry: "重试", complete: "完成", complete_with_failures: "失败收口", cancelled: "已取消", dead_letter: "死信", retry_wait: "等待重试", size_limit: "超过大小限制" };
const CHANGE_LABEL: Record<string, string> = { add: "新增", update: "更新", delete: "删除", acl_update: "权限更新", metadata_update: "元数据更新", unchanged: "无变化", skip: "跳过", retry: "重试" };
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
const EMPTY_DRAFT = { sourceType: "object_storage" as "object_storage" | "web" | "connector", name: "", endpoint: "", bucket: "", prefix: "", region: "", credentialEnv: "", secure: true, urls: "", sitemapUrl: "", maxObjects: 1000, databaseUrlEnv: "", view: "", idColumn: "id", contentColumn: "content", updatedColumn: "updated_at", metadataMapping: "", aclMapping: "", categoryId: "", department: "" };
type Props = { knowledgeBaseId: string; items: DataSource[]; categories: DocumentCategory[]; onRefresh: () => Promise<void> };

export function KnowledgeBaseDataSourcesPanel({ knowledgeBaseId, items, categories, onRefresh }: Props) {
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<DataSource | null>(null);
  const [formError, setFormError] = useState("");
  const [historyFor, setHistoryFor] = useState<DataSource | null>(null);
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [selectedRun, setSelectedRun] = useState<SyncRun | null>(null);
  const [resources, setResources] = useState<SyncResourceRun[]>([]);
  const [previewFor, setPreviewFor] = useState<DataSource | null>(null);
  const [previewItems, setPreviewItems] = useState<Array<{ key: string; version: string; size: number; modified_at: string | null }>>([]);
  const [busyId, setBusyId] = useState("");
  const [sourceTypeFilter, setSourceTypeFilter] = useState("");
  const [syncStatusFilter, setSyncStatusFilter] = useState("");
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
      ...EMPTY_DRAFT, sourceType: item.source_type === "web" ? "web" : item.source_type === "connector" ? "connector" : "object_storage",
      name: item.name, endpoint: String(config.endpoint || ""), bucket: String(config.bucket || ""),
      prefix: String(config.prefix || ""), region: String(config.region || ""),
      credentialEnv: String(config.credential_env || ""), secure: config.secure !== false,
      urls: Array.isArray(config.urls) ? config.urls.join("\n") : "",
      sitemapUrl: String(config.sitemap_url || ""), maxObjects: Number(config.max_objects || 1000),
      databaseUrlEnv: String(config.database_url_env || ""), view: String(config.view || ""),
      idColumn: String(config.id_column || "id"), contentColumn: String(config.content_column || "content"),
      updatedColumn: String(config.updated_column || "updated_at"),
      metadataMapping: Object.entries((config.metadata_mapping || {}) as Record<string, unknown>).map(([target, column]) => `${target}=${column}`).join("\n"),
      aclMapping: Object.entries((config.acl_mapping || {}) as Record<string, unknown>).map(([target, column]) => `${target}=${column}`).join("\n"),
      categoryId: item.default_category_id || "", department: String(item.metadata_defaults?.department || ""),
    });
    setFormError("");
    setFormOpen(true);
  };
  const save = async () => {
    setBusyId("save"); setFormError("");
    const parseMapping = (value: string) => Object.fromEntries(value.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
      const [target, column, ...rest] = line.split("=").map((part) => part.trim());
      if (!target || !column || rest.length) throw new Error("字段映射请使用“目标字段=来源列”，每行一项。");
      return [target, column];
    }));
    let metadataMapping: Record<string, string> = {}; let aclMapping: Record<string, string> = {};
    try { metadataMapping = parseMapping(draft.metadataMapping); aclMapping = parseMapping(draft.aclMapping); }
    catch (reason) { const message = reason instanceof Error ? reason.message : "字段映射格式无效。"; setFormError(message); setBusyId(""); return; }
    const configuration = draft.sourceType === "web"
      ? { urls: draft.urls.split("\n").map((item) => item.trim()).filter(Boolean), sitemap_url: draft.sitemapUrl.trim() || null, max_objects: draft.maxObjects }
      : draft.sourceType === "connector"
        ? { connector_type: "database_readonly", database_url_env: draft.databaseUrlEnv.trim(), view: draft.view.trim(), id_column: draft.idColumn.trim(), content_column: draft.contentColumn.trim(), updated_column: draft.updatedColumn.trim() || null, metadata_mapping: metadataMapping, acl_mapping: aclMapping }
        : { endpoint: draft.endpoint.trim(), bucket: draft.bucket.trim(), prefix: draft.prefix.trim(), region: draft.region.trim() || null, secure: draft.secure, credential_env: draft.credentialEnv.trim() };
    const payload = { name: draft.name.trim(), configuration, default_category_id: draft.categoryId || null, metadata_defaults: draft.department.trim() ? { department: draft.department.trim() } : {} };
    try {
      if (editing) await api.updateDataSource(editing.data_source_id, payload);
      else await api.createDataSource(knowledgeBaseId, { ...payload, source_type: draft.sourceType });
      toast.success(editing ? "数据源配置已更新。" : "外部数据源已创建，可先测试连接再同步。");
      setFormOpen(false); setEditing(null); await onRefresh();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "保存失败。";
      setFormError(message); toast.error(message);
    } finally { setBusyId(""); }
  };
  const openHistory = async (item: DataSource) => {
    setHistoryFor(item); setRuns([]); setSelectedRun(null); setResources([]); setHistoryError("");
    try { setRuns(await api.listDataSourceSyncRuns(item.data_source_id)); }
    catch (reason) { setHistoryError(reason instanceof Error ? reason.message : "同步记录读取失败。"); }
  };
  const openRun = async (run: SyncRun) => {
    if (!historyFor) return;
    setSelectedRun(run); setResources([]); setHistoryError("");
    try { setResources(await api.listSyncRunResources(historyFor.data_source_id, run.sync_run_id)); }
    catch (reason) { setHistoryError(reason instanceof Error ? reason.message : "单资料状态读取失败。"); }
  };
  const openPreview = async (item: DataSource) => {
    setPreviewFor(item); setPreviewItems([]); setHistoryError("");
    try { setPreviewItems((await api.previewDataSource(item.data_source_id)).items); }
    catch (reason) { setHistoryError(reason instanceof Error ? reason.message : "数据预览失败。"); }
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
    if (["object_storage", "web", "connector"].includes(item.source_type) && item.allowed_actions.includes("edit")) {
      actions.push({ label: "编辑", blockedReason: busy ? "处理中" : syncing ? "同步进行中" : undefined, onSelect: () => openEdit(item) });
    }
    if (item.allowed_actions.includes("test")) {
      actions.push({ label: "测试连接", blockedReason: busy ? "处理中" : undefined, onSelect: () => void act(item.data_source_id, () => api.testDataSource(item.data_source_id), "连接测试通过。") });
      actions.push({ label: "数据预览", blockedReason: busy ? "处理中" : undefined, onSelect: () => void openPreview(item) });
    }
    if (item.allowed_actions.includes("sync")) {
      actions.push({ label: "同步", blockedReason: busy ? "处理中" : syncing ? "同步进行中" : undefined, onSelect: () => void act(item.data_source_id, () => api.syncDataSource(item.data_source_id), "同步任务已进入队列。") });
    }
    // 文件更新进入 Document Processing Run，不伪装成 Connector Sync Run。
    if (item.source_type !== "file") actions.push({ label: "记录", onSelect: () => void openHistory(item) });
    if (item.sync_status === "failed" || item.sync_status === "aborted") {
      actions.push({ label: "重试", blockedReason: busy ? "处理中" : undefined, onSelect: () => void act(item.data_source_id, () => api.retryDataSource(item.data_source_id), "失败同步已重新入队。") });
    }
    if (item.allowed_actions.includes(item.enabled ? "disable" : "enable")) {
      actions.push({
        label: item.enabled ? "停用同步" : "启用同步",
        blockedReason: busy ? "处理中" : syncing ? "同步进行中" : undefined,
        onSelect: () => void act(item.data_source_id, () => api.setDataSourceEnabled(item.data_source_id, !item.enabled), item.enabled ? "已停用后续同步；现有资料仍可检索。" : "数据源同步已启用。"),
      });
    }
    const retrievalEnabled = item.retrieval_enabled !== false;
    if (item.allowed_actions.includes(retrievalEnabled ? "disable_retrieval" : "enable_retrieval")) {
      actions.push({
        label: retrievalEnabled ? "停用检索" : "启用检索",
        blockedReason: busy ? "处理中" : undefined,
        onSelect: () => void act(
          item.data_source_id,
          () => api.setDataSourceRetrievalEnabled(item.data_source_id, !retrievalEnabled),
          retrievalEnabled ? "已从后续检索中排除；资料与同步记录保持不变。" : "该数据源资料已恢复参与检索。",
        ),
      });
    }
    if (item.allowed_actions.includes("delete")) {
      actions.push({ label: "删除", tone: "destructive", blockedReason: busy ? "处理中" : undefined, onSelect: () => startDelete(item) });
    }
    return actions;
  };

  const visibleItems = items.filter((item) =>
    (!sourceTypeFilter || item.source_type === sourceTypeFilter)
    && (!syncStatusFilter || item.sync_status === syncStatusFilter));

  const columns: Column<DataSource>[] = [
    {
      key: "name", header: "数据源", width: "190px", truncate: false,
      render: (item) => <span className="flex min-w-0 items-center gap-2"><strong className="min-w-0 truncate font-medium text-ink" title={item.name}>{item.name}</strong>{!item.enabled ? <Badge shape="type" className="shrink-0">已停用</Badge> : null}</span>,
    },
    {
      key: "type", header: "类型", width: "120px",
      render: (item) => item.source_type === "object_storage" ? "S3 对象存储" : item.source_type === "local_directory" ? "本地目录" : item.source_type === "web" ? "网页" : item.source_type === "connector" ? "连接器" : "文件上传",
    },
    {
      key: "sync", header: "同步进度", width: "520px", truncate: false,
      render: (item) => {
        const syncing = item.sync_status === "queued" || item.sync_status === "running";
        return <div aria-busy={syncing || undefined}><PipelineStepper kind="sync_run" currentStage={item.sync_current_stage} status={item.sync_status} progressPercent={item.sync_progress_percent ?? (item.sync_status === "succeeded" ? 100 : 0)} label={`${item.name} 同步进度`} failureReason={item.failure_reason}/></div>;
      },
    },
    { key: "documents", header: "资料数", width: "80px", numeric: true, render: (item) => item.document_count },
    { key: "synced", header: "最近同步", width: "160px", render: (item) => item.last_synced_at ? new Date(item.last_synced_at).toLocaleString("zh-CN") : "—" },
    { key: "failure", header: "失败原因", width: "160px", render: (item) => <span title={item.failure_reason || "—"}>{item.failure_reason || "—"}</span> },
    { key: "actions", header: "操作", width: "360px", align: "right", truncate: false, render: (item) => <RowActions rowLabel={item.name} actions={rowActions(item)} /> },
  ];

  return <section aria-label="知识库数据源" className="grid gap-3">
    <Toolbar filters={<>
      <Select size="sm" aria-label="来源类型筛选" value={sourceTypeFilter} onChange={(event) => setSourceTypeFilter(event.target.value)}><option value="">全部外部来源</option><option value="object_storage">S3 对象存储</option><option value="local_directory">本地目录</option><option value="web">网页</option><option value="connector">连接器</option></Select>
      <Select size="sm" aria-label="同步状态筛选" value={syncStatusFilter} onChange={(event) => setSyncStatusFilter(event.target.value)}><option value="">全部同步状态</option>{Object.entries(SYNC_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select>
    </>} actions={<Button size="sm" onClick={openCreate}>新建外部数据源</Button>} />
    <DataTable
      rows={visibleItems}
      columns={columns}
      rowKey={(item) => item.data_source_id}
      label="数据源列表"
      emptyState={items.length
        ? { kind: "filtered", title: "没有符合条件的数据源", description: "调整来源类型或同步状态后重试。" }
        : { kind: "empty", title: "暂无外部数据源", description: "接入 S3、网页或数据库等外部来源。" }}
    />
    {formOpen ? <Dialog open size="md" title={editing ? "编辑数据源" : "新建外部数据源"} description="凭据只读取运行环境变量，不保存到数据库。" onClose={() => { if (!busyId) { setFormOpen(false); setEditing(null); } }}>
      <form className="grid gap-[9px] pt-[20px] px-[22px]" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        {formError ? <ErrorBanner>{formError}</ErrorBanner> : null}
        {!editing ? <label className="text-[#4e576c] text-[13px] font-semibold">类型<Select value={draft.sourceType} onChange={(event) => setDraft({ ...draft, sourceType: event.target.value as typeof draft.sourceType })}><option value="object_storage">S3 / MinIO</option><option value="web">Web URL</option><option value="connector">数据库只读 View</option></Select></label> : null}
        <label className="text-[#4e576c] text-[13px] font-semibold">名称<Input className="py-[10px]" required value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })}/></label>
        {draft.sourceType === "web" ? <><label className="text-[#4e576c] text-[13px] font-semibold">固定 URL（每行一个）<textarea className="min-h-24" value={draft.urls} onChange={(event) => setDraft({ ...draft, urls: event.target.value })}/></label><label className="text-[#4e576c] text-[13px] font-semibold">Sitemap URL<Input value={draft.sitemapUrl} onChange={(event) => setDraft({ ...draft, sitemapUrl: event.target.value })}/></label><label className="text-[#4e576c] text-[13px] font-semibold">最多发现资料数<Input type="number" min={1} max={10000} value={draft.maxObjects} onChange={(event) => setDraft({ ...draft, maxObjects: Number(event.target.value) })}/></label></> : null}
        {draft.sourceType === "connector" ? <><label className="text-[#4e576c] text-[13px] font-semibold">数据库 URL 环境变量<Input required value={draft.databaseUrlEnv} onChange={(event) => setDraft({ ...draft, databaseUrlEnv: event.target.value })}/></label><label className="text-[#4e576c] text-[13px] font-semibold">只读 View<Input required value={draft.view} onChange={(event) => setDraft({ ...draft, view: event.target.value })}/></label><div className="grid grid-cols-3 gap-2"><Input aria-label="ID 列" placeholder="ID 列" value={draft.idColumn} onChange={(event) => setDraft({ ...draft, idColumn: event.target.value })}/><Input aria-label="内容列" placeholder="内容列" value={draft.contentColumn} onChange={(event) => setDraft({ ...draft, contentColumn: event.target.value })}/><Input aria-label="更新时间列" placeholder="更新时间列" value={draft.updatedColumn} onChange={(event) => setDraft({ ...draft, updatedColumn: event.target.value })}/></div><label className="text-[#4e576c] text-[13px] font-semibold">Metadata 映射（目标字段=来源列）<textarea className="min-h-20" value={draft.metadataMapping} onChange={(event) => setDraft({ ...draft, metadataMapping: event.target.value })}/></label><label className="text-[#4e576c] text-[13px] font-semibold">ACL 映射（目标字段=来源列）<textarea className="min-h-20" value={draft.aclMapping} onChange={(event) => setDraft({ ...draft, aclMapping: event.target.value })}/></label></> : null}
        {draft.sourceType === "object_storage" ? <>
        <label className="text-[#4e576c] text-[13px] font-semibold">Endpoint<Input className="py-[10px]" required placeholder="s3.example.com" value={draft.endpoint} onChange={(event) => setDraft({ ...draft, endpoint: event.target.value })}/></label>
        <label className="text-[#4e576c] text-[13px] font-semibold">Bucket<Input className="py-[10px]" required value={draft.bucket} onChange={(event) => setDraft({ ...draft, bucket: event.target.value })}/></label>
        <label className="text-[#4e576c] text-[13px] font-semibold">Prefix<Input className="py-[10px]" value={draft.prefix} onChange={(event) => setDraft({ ...draft, prefix: event.target.value })}/></label>
        <label className="text-[#4e576c] text-[13px] font-semibold">Region<Input className="py-[10px]" value={draft.region} onChange={(event) => setDraft({ ...draft, region: event.target.value })}/></label>
        <label className="text-[#4e576c] text-[13px] font-semibold">凭据环境变量前缀<Input className="py-[10px]" required placeholder="ENTERPRISE_DOCS" value={draft.credentialEnv} onChange={(event) => setDraft({ ...draft, credentialEnv: event.target.value })}/></label>
        <Checkbox showLabel label="使用 HTTPS" checked={draft.secure} onCheckedChange={(next) => setDraft({ ...draft, secure: next })}/></> : null}
        <label className="text-[#4e576c] text-[13px] font-semibold">默认分类<Select value={draft.categoryId} onChange={(event) => setDraft({ ...draft, categoryId: event.target.value })}><option value="">不指定分类</option>{categories.filter((item) => item.active).map((item) => <option key={item.category_id} value={item.category_id}>{item.name}</option>)}</Select></label>
        <label className="text-[#4e576c] text-[13px] font-semibold">默认部门<Input className="py-[10px]" value={draft.department} onChange={(event) => setDraft({ ...draft, department: event.target.value })}/></label>
        <DialogActions><Button variant="secondary" loading={!!busyId} onClick={() => setFormOpen(false)}>取消</Button><Button type="submit" loading={!!busyId}>{busyId ? "保存中…" : "保存"}</Button></DialogActions>
      </form>
    </Dialog> : null}
    {previewFor ? <Dialog open size="lg" title="数据预览" description={previewFor.name} onClose={() => setPreviewFor(null)}>{historyError ? <ErrorBanner>{historyError}</ErrorBanner> : null}<DataTable label="数据源预览" rows={previewItems} rowKey={(item) => item.key} columns={[
      { key: "key", header: "资源", width: "45%", render: (item) => item.key },
      { key: "size", header: "大小", width: "15%", numeric: true, render: (item) => `${item.size} B` },
      { key: "version", header: "版本", width: "25%", render: (item) => item.version.slice(0, 16) },
      { key: "updated", header: "更新时间", width: "15%", render: (item) => item.modified_at ? new Date(item.modified_at).toLocaleString("zh-CN") : "—" },
    ]} emptyState={{ kind: "empty", title: "未发现资源", description: "检查连接配置与数据范围。" }}/></Dialog> : null}
    {historyFor ? <Dialog open size="md" title="同步记录" description={historyFor.name} onClose={() => setHistoryFor(null)}>
      {historyError ? <ErrorBanner>{historyError}</ErrorBanner> : null}
      <div className="grid gap-0 max-h-[420px] overflow-auto">
        {runs.length ? runs.map((run) => {
          const percent = run.total_count ? Math.round(run.completed_count * 100 / run.total_count) : (run.status === "succeeded" ? 100 : 0);
          return <div role="button" tabIndex={0} onClick={() => void openRun(run)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") void openRun(run); }} key={run.sync_run_id} className="grid cursor-pointer gap-1 border-b border-divider px-[22px] py-3 text-left hover:bg-surface-muted">
            <span className="flex items-center justify-between gap-3"><strong>{RUN_STATUS[run.status] || run.status} · {STAGE_LABEL[run.stage] || run.stage}</strong>{["queued", "discovering", "syncing", "indexing"].includes(run.status) ? <Button variant="ghost" size="sm" onClick={(event) => { event.stopPropagation(); void act(run.sync_run_id, () => api.cancelSyncRun(historyFor!.data_source_id, run.sync_run_id), "同步任务已取消。"); }}>取消</Button> : null}</span>
            <PipelineStepper kind="sync_run" currentStage={run.stage} status={run.status} progressPercent={percent} label={`${run.sync_run_id} 同步进度`} failureReason={run.failure_reason}/>
            <span className="text-base text-ink-faint">新增 {run.added_count} / 更新 {run.updated_count} / 删除 {run.deleted_count} / 跳过 {run.skipped_count} / 失败 {run.failed_count}</span>
            <small className="text-base text-ink-faint">{new Date(run.created_at).toLocaleString("zh-CN")}{run.failure_reason ? ` · ${run.error_code || "ERROR"}: ${run.failure_reason}` : ""}</small>
          </div>;
        }) : <p className="text-md text-[#737c90] leading-[1.6] mt-[13px] mb-[13px]">暂无同步记录。</p>}
      </div>
    </Dialog> : null}
    {selectedRun && historyFor ? <Dialog open size="lg" title="同步任务详情" description={`${historyFor.name} · ${selectedRun.sync_run_id}`} onClose={() => setSelectedRun(null)}>
      {historyError ? <ErrorBanner>{historyError}</ErrorBanner> : null}
      <DataTable label="单资料同步状态" rows={resources} rowKey={(item) => item.sync_resource_run_id} columns={[
        { key: "resource", header: "资源", width: "32%", render: (item) => <strong className="font-medium">{item.external_resource_id}</strong> },
        { key: "operation", header: "变更", width: "12%", render: (item) => CHANGE_LABEL[item.operation] || item.operation },
        { key: "stage", header: "当前阶段", width: "16%", render: (item) => STAGE_LABEL[item.current_stage] || item.current_stage },
        { key: "status", header: "状态", width: "16%", render: (item) => <Badge shape="status" tone={item.status === "failed" || item.status === "dead_letter" ? "danger" : item.status === "succeeded" ? "success" : "brand"}>{RESOURCE_STATUS[item.status] || item.status}</Badge> },
        { key: "retry", header: "重试", width: "10%", numeric: true, render: (item) => `${item.attempt_count}/${item.max_attempts}` },
        { key: "error", header: "失败原因", width: "14%", render: (item) => item.error_message || "—" },
        { key: "actions", header: "操作", width: "12%", align: "right", truncate: false, render: (item) => ["failed", "dead_letter"].includes(item.status) ? (item.document_version_id ? <Button variant="ghost" size="sm" onClick={() => void act(item.sync_resource_run_id, async () => { await api.retrySyncResource(historyFor.data_source_id, selectedRun.sync_run_id, item.sync_resource_run_id); setResources(await api.listSyncRunResources(historyFor.data_source_id, selectedRun.sync_run_id)); }, "资源已重新入队。")}>重试</Button> : <span className="text-ink-faint" title="资源尚未形成文档版本，请在数据源列表执行重试同步">重新同步数据源</span>) : "—" },
      ]} emptyState={{ kind: "empty", title: "暂无单资料记录", description: "旧同步任务仅保留批次级汇总。" }}/>
    </Dialog> : null}
    {confirmDialog}
  </section>;
}
