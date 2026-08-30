import { useEffect, useState } from "react";
import { api } from "../api";
import type { DataSource, DocumentCategory, SyncRun } from "../types";
import { Button } from "./ui/Button";
import { Dialog, DialogActions } from "./ui/Dialog";
import { Input } from "./ui/Input";
import { Select } from "./ui/Select";

const SYNC_LABEL: Record<DataSource["sync_status"], string> = {
  idle: "未同步", queued: "等待同步", running: "同步中", succeeded: "同步完成", failed: "同步失败", aborted: "已熔断",
};
const EMPTY_DRAFT = { name: "", endpoint: "", bucket: "", prefix: "", region: "", credentialEnv: "", secure: true, categoryId: "", department: "" };
type Props = { knowledgeBaseId: string; items: DataSource[]; categories: DocumentCategory[]; onRefresh: () => Promise<void> };

export function KnowledgeBaseDataSourcesPanel({ knowledgeBaseId, items, categories, onRefresh }: Props) {
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<DataSource | null>(null);
  const [historyFor, setHistoryFor] = useState<DataSource | null>(null);
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [busyId, setBusyId] = useState("");
  const [pendingDelete, setPendingDelete] = useState<DataSource | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState(EMPTY_DRAFT);

  useEffect(() => {
    if (!items.some((item) => item.sync_status === "queued" || item.sync_status === "running")) return;
    const timer = window.setInterval(() => { void onRefresh(); }, 1500);
    return () => window.clearInterval(timer);
  }, [items, onRefresh]);

  const act = async (id: string, action: () => Promise<unknown>, message: string) => {
    setBusyId(id); setError(""); setNotice("");
    try { await action(); setNotice(message); await onRefresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败。"); }
    finally { setBusyId(""); }
  };
  const openCreate = () => { setEditing(null); setDraft(EMPTY_DRAFT); setFormOpen(true); };
  const openEdit = (item: DataSource) => {
    const config = item.configuration || {};
    setEditing(item);
    setDraft({
      name: item.name, endpoint: String(config.endpoint || ""), bucket: String(config.bucket || ""),
      prefix: String(config.prefix || ""), region: String(config.region || ""),
      credentialEnv: String(config.credential_env || ""), secure: config.secure !== false,
      categoryId: item.default_category_id || "", department: String(item.metadata_defaults?.department || ""),
    });
    setFormOpen(true);
  };
  const save = async () => {
    setBusyId("save"); setError("");
    const configuration = { endpoint: draft.endpoint.trim(), bucket: draft.bucket.trim(), prefix: draft.prefix.trim(), region: draft.region.trim() || null, secure: draft.secure, credential_env: draft.credentialEnv.trim() };
    const payload = { name: draft.name.trim(), configuration, default_category_id: draft.categoryId || null, metadata_defaults: draft.department.trim() ? { department: draft.department.trim() } : {} };
    try {
      if (editing) await api.updateDataSource(editing.data_source_id, payload);
      else await api.createDataSource(knowledgeBaseId, { ...payload, source_type: "object_storage" });
      setNotice(editing ? "数据源配置已更新。" : "外部数据源已创建，可先测试连接再同步。");
      setFormOpen(false); setEditing(null); await onRefresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败。"); }
    finally { setBusyId(""); }
  };
  const openHistory = async (item: DataSource) => {
    setHistoryFor(item); setRuns([]); setError("");
    try { setRuns(await api.listDataSourceSyncRuns(item.data_source_id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "同步记录读取失败。"); }
  };

  return <section className="source-governance">
    <div className="source-toolbar"><Button size="sm" onClick={openCreate}>新建外部数据源</Button></div>
    {error ? <div className="error-banner" role="alert">{error}</div> : null}
    {notice ? <div className="success-banner" role="status">{notice}</div> : null}
    {items.length ? <div className="management-table-wrap"><table className="management-table source-governance-table">
      <thead><tr><th>数据源</th><th>类型</th><th>同步状态</th><th>资料数</th><th>最近同步</th><th>失败原因</th><th>操作</th></tr></thead>
      <tbody>{items.map((item) => {
        const syncing = item.sync_status === "queued" || item.sync_status === "running";
        const state = item.sync_status === "failed" || item.sync_status === "aborted" ? "failed" : syncing ? "processing" : item.sync_status === "succeeded" ? "ready" : "empty";
        return <tr key={item.data_source_id}>
          <td><strong>{item.name}</strong>{!item.enabled ? <span className="base-type-tag">已停用</span> : null}</td>
          <td>{item.source_type === "object_storage" ? "S3 对象存储" : item.source_type === "local_directory" ? "本地目录" : "文件"}</td>
          <td><span className={`status-tag status-${state}${syncing ? " index-loading" : ""}`} aria-busy={syncing}>{SYNC_LABEL[item.sync_status]}</span>{syncing ? <progress aria-label={`${item.name} 同步进度`} /> : null}</td>
          <td>{item.document_count}</td><td>{item.last_synced_at ? new Date(item.last_synced_at).toLocaleString("zh-CN") : "—"}</td>
          <td><span className="truncate-cell" title={item.failure_reason || "—"}>{item.failure_reason || "—"}</span></td>
          <td><div className="table-actions">
            {item.source_type === "object_storage" && item.allowed_actions.includes("edit") ? <Button variant="ghost" size="sm" loading={!!busyId || syncing} onClick={() => openEdit(item)}>编辑</Button> : null}
            {item.allowed_actions.includes("test") ? <Button variant="ghost" size="sm" loading={!!busyId} onClick={() => void act(item.data_source_id, () => api.testDataSource(item.data_source_id), "连接测试通过。")}>测试连接</Button> : null}
            {item.allowed_actions.includes("sync") ? <Button variant="ghost" size="sm" loading={!!busyId || syncing} onClick={() => void act(item.data_source_id, () => api.syncDataSource(item.data_source_id), "同步任务已进入队列。")}>同步</Button> : null}
            <Button variant="ghost" size="sm" onClick={() => void openHistory(item)}>记录</Button>
            {item.sync_status === "failed" || item.sync_status === "aborted" ? <Button variant="ghost" size="sm" loading={!!busyId} onClick={() => void act(item.data_source_id, () => api.retryDataSource(item.data_source_id), "失败同步已重新入队。")}>重试</Button> : null}
            {item.allowed_actions.includes(item.enabled ? "disable" : "enable") ? <Button variant="ghost" size="sm" loading={!!busyId || syncing} onClick={() => void act(item.data_source_id, () => api.setDataSourceEnabled(item.data_source_id, !item.enabled), item.enabled ? "数据源已停用。" : "数据源已启用。")}>{item.enabled ? "停用" : "启用"}</Button> : null}
            {item.allowed_actions.includes("delete") ? <Button variant="ghost" size="sm" className="text-danger-text hover:bg-danger-subtle" loading={!!busyId} onClick={() => setPendingDelete(item)}>删除</Button> : null}
          </div></td>
        </tr>;
      })}</tbody>
    </table></div> : <p className="empty-copy">当前知识库没有数据源。</p>}
    {formOpen ? <Dialog open size="md" title={editing ? "编辑 S3 兼容数据源" : "新建 S3 兼容数据源"} description="凭据只读取运行环境变量，不保存到数据库。" onClose={() => { if (!busyId) { setFormOpen(false); setEditing(null); } }}>
      <form className="modal-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <label>名称<Input required value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })}/></label>
        <label>Endpoint<Input required placeholder="s3.example.com" value={draft.endpoint} onChange={(event) => setDraft({ ...draft, endpoint: event.target.value })}/></label>
        <label>Bucket<Input required value={draft.bucket} onChange={(event) => setDraft({ ...draft, bucket: event.target.value })}/></label>
        <label>Prefix<Input value={draft.prefix} onChange={(event) => setDraft({ ...draft, prefix: event.target.value })}/></label>
        <label>Region<Input value={draft.region} onChange={(event) => setDraft({ ...draft, region: event.target.value })}/></label>
        <label>凭据环境变量前缀<Input required placeholder="ENTERPRISE_DOCS" value={draft.credentialEnv} onChange={(event) => setDraft({ ...draft, credentialEnv: event.target.value })}/></label>
        <label>默认分类<Select value={draft.categoryId} onChange={(event) => setDraft({ ...draft, categoryId: event.target.value })}><option value="">不指定分类</option>{categories.filter((item) => item.active).map((item) => <option key={item.category_id} value={item.category_id}>{item.name}</option>)}</Select></label>
        <label>默认部门<Input value={draft.department} onChange={(event) => setDraft({ ...draft, department: event.target.value })}/></label>
        <label className="checkbox-field"><input type="checkbox" checked={draft.secure} onChange={(event) => setDraft({ ...draft, secure: event.target.checked })}/>使用 HTTPS</label>
        <DialogActions><Button variant="secondary" loading={!!busyId} onClick={() => setFormOpen(false)}>取消</Button><Button type="submit" loading={!!busyId}>{busyId ? "保存中…" : "保存"}</Button></DialogActions>
      </form>
    </Dialog> : null}
    {historyFor ? <Dialog open size="md" title="同步记录" description={historyFor.name} onClose={() => setHistoryFor(null)}><div className="sync-run-list">{runs.length ? runs.map((run) => <div key={run.sync_run_id}><strong>{run.status} · {run.stage}</strong><span>新增 {run.added_count} / 更新 {run.updated_count} / 删除 {run.deleted_count} / 跳过 {run.skipped_count} / 失败 {run.failed_count}</span><small>{new Date(run.created_at).toLocaleString("zh-CN")}{run.failure_reason ? ` · ${run.error_code || "ERROR"}: ${run.failure_reason}` : ""}</small></div>) : <p className="empty-copy">暂无同步记录。</p>}</div></Dialog> : null}
    {pendingDelete ? <Dialog open title="删除数据源" onClose={() => { if (!busyId) setPendingDelete(null); }}><div className="confirm-copy">确认删除数据源「{pendingDelete.name}」吗？<p>已同步进来的资料不会被删除，但该数据源将不再参与后续同步。</p></div><DialogActions><Button variant="secondary" loading={!!busyId} onClick={() => setPendingDelete(null)}>取消</Button><Button variant="destructive" loading={!!busyId} onClick={() => { const target = pendingDelete; setPendingDelete(null); void act(target.data_source_id, () => api.deleteDataSource(target.data_source_id), "数据源已删除。"); }}>确认删除</Button></DialogActions></Dialog> : null}
  </section>;
}
