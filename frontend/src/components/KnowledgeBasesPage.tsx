import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { CategoryTemplate, KnowledgeBase } from "../types";
import { CategoryTemplateModal } from "./CategoryTemplateModal";
import { Button } from "./ui/Button";
import { Dialog, DialogActions } from "./ui/Dialog";
import { Input } from "./ui/Input";
import { Select } from "./ui/Select";

const STATUS_LABEL = { empty: "空库", processing: "处理中", ready: "可用", failed: "失败" } as const;
function formatBytes(bytes: number) { if (!bytes) return "0 KB"; const units = ["B", "KB", "MB", "GB"]; const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 3); return `${(bytes / 1024 ** exponent).toFixed(exponent ? 1 : 0)} ${units[exponent]}`; }

export function KnowledgeBasesPage({ isAdmin, onOpen, showCreate, onCloseCreate }: { isAdmin: boolean; onOpen: (path: string) => void; showCreate: boolean; onCloseCreate: () => void }) {
  const [items, setItems] = useState<KnowledgeBase[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState(""); const [description, setDescription] = useState("");
  const [search, setSearch] = useState(""); const [status, setStatus] = useState("");
  const [sort, setSort] = useState<"updated_desc" | "updated_asc">("updated_desc");
  const [page, setPage] = useState(0); const [hasNext, setHasNext] = useState(false); const pageSize = 10;
  const [editing, setEditing] = useState<KnowledgeBase | null>(null); const [deleting, setDeleting] = useState<KnowledgeBase | null>(null);
  const [error, setError] = useState("");
  const [template, setTemplate] = useState<CategoryTemplate | null>(null);
  const [showTemplate, setShowTemplate] = useState(false);
  const [applyTemplate, setApplyTemplate] = useState(true);
  const loadTemplate = async () => { try { const value = await api.getDefaultCategoryTemplate(); setTemplate(value); return value; } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取分类模板。"); return null; } };
  useEffect(() => {
    if (!isAdmin) return;
    api.getDefaultCategoryTemplate().then(setTemplate, (reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "无法读取分类模板。");
    });
  }, [isAdmin]);
  const load = useCallback(() => { setError(""); setItems(null); return api.listKnowledgeBases({ name: search.trim(), status, sort, offset: page * pageSize, limit: pageSize + 1 }).then((result) => { setHasNext(result.length > pageSize); setItems(result.slice(0, pageSize)); }, (reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取知识库。")); }, [search, status, sort, page]);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 250); return () => window.clearTimeout(timer); }, [load]);
  const create = async (event: FormEvent) => { event.preventDefault(); setBusy(true); setError(""); try { const item = await api.createKnowledgeBase(name.trim(), description.trim(), applyTemplate); setName(""); setDescription(""); setApplyTemplate(true); onCloseCreate(); await load(); onOpen(`/knowledge-bases/${item.knowledge_base_id}`); } catch (reason) { setError(reason instanceof Error ? reason.message : "创建失败。"); } finally { setBusy(false); } };
  const saveEdit = async (event: FormEvent) => { event.preventDefault(); if (!editing) return; setBusy(true); try { await api.updateKnowledgeBase(editing.knowledge_base_id, name.trim(), description.trim()); setEditing(null); setName(""); setDescription(""); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败。"); } finally { setBusy(false); } };
  const remove = async () => { if (!deleting) return; setBusy(true); try { await api.deleteKnowledgeBase(deleting.knowledge_base_id); setDeleting(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "删除失败。"); } finally { setBusy(false); } };
  const closeEdit = () => { if (!busy) { setEditing(null); setName(""); setDescription(""); } };
  return <section className="product-page" aria-label="知识库管理">
    <div className="management-toolbar"><label><span className="sr-only">搜索知识库</span><Input size="sm" className="w-52" value={search} onChange={(event) => { setSearch(event.target.value); setPage(0); }} placeholder="搜索知识库名称" /></label><Select size="sm" className="w-28" aria-label="状态筛选" value={status} onChange={(event) => { setStatus(event.target.value); setPage(0); }}><option value="">全部状态</option><option value="empty">空库</option><option value="processing">处理中</option><option value="ready">可用</option><option value="failed">失败</option></Select><Select size="sm" className="w-28" aria-label="更新时间排序" value={sort} onChange={(event) => { setSort(event.target.value as typeof sort); setPage(0); }}><option value="updated_desc">最近更新</option><option value="updated_asc">最早更新</option></Select>{isAdmin ? <Button variant="secondary" size="sm" onClick={() => { setShowTemplate(true); if (!template) void loadTemplate(); }}>知识库分类模板</Button> : null}</div>
    {error ? <div className="error-banner" role="alert">{error} <Button variant="ghost" size="sm" onClick={() => void load()}>重试</Button></div> : null}
    {items === null && !error ? <div className="evaluation-state pulse">正在读取知识库…</div> : null}
    {items?.length === 0 ? <div className="evaluation-state"><h2>{search.trim() || status ? "没有符合条件的知识库" : "还没有知识库"}</h2><p>{search.trim() || status ? "调整搜索词或筛选条件后重试。" : "创建一个知识库后即可上传资料并开始问答。"}</p></div> : null}
    {items?.length ? <div className="management-table-wrap"><table className="management-table"><thead><tr><th>知识库名称</th><th>描述</th><th>文档数量</th><th>存储空间</th><th>状态</th><th>权限</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{items.map((item) => { const itemStatus = item.index_status ?? (item.document_count ? "ready" : "empty"); const actions = item.allowed_actions ?? ["detail"]; return <tr key={item.knowledge_base_id}><td><button className="table-primary-link" onClick={() => onOpen(`/knowledge-bases/${item.knowledge_base_id}`)}>{item.name}</button><span className={`base-type-tag ${item.is_default ? "is-default" : "is-independent"}`}>{item.is_default ? "默认知识库" : "独立知识库"}</span></td><td><span className="truncate-cell" title={item.description || "—"}>{item.description || "—"}</span></td><td>{item.document_count}</td><td>{formatBytes(item.source_file_bytes ?? 0)}</td><td><span className={`status-tag status-${itemStatus}`}>{STATUS_LABEL[itemStatus]}</span></td><td>{item.current_user_permission === "admin" ? "管理员" : "可使用"}</td><td>{new Date(item.updated_at).toLocaleString("zh-CN")}</td><td><div className="table-actions"><Button variant="ghost" size="sm" onClick={() => onOpen(`/knowledge-bases/${item.knowledge_base_id}`)}>详情</Button>{actions.includes("edit") ? <Button variant="ghost" size="sm" onClick={() => { setEditing(item); setName(item.name); setDescription(item.description); }}>编辑</Button> : null}{item.current_user_permission === "admin" ? <Button variant="ghost" size="sm" className="text-danger-text hover:bg-danger-subtle" onClick={() => setDeleting(item)}>删除</Button> : null}</div></td></tr>; })}</tbody></table></div> : null}
    {items && (page > 0 || hasNext) ? <nav className="management-pagination" aria-label="知识库分页"><Button variant="outline" size="sm" reasonHidden blockedReason={page === 0 ? "已经是第一页" : undefined} onClick={() => setPage((value) => Math.max(0, value - 1))}>上一页</Button><span>第 {page + 1} 页</span><Button variant="outline" size="sm" reasonHidden blockedReason={hasNext ? undefined : "没有下一页"} onClick={() => setPage((value) => value + 1)}>下一页</Button></nav> : null}
    {showCreate ? <Dialog open title="新建知识库" description="知识库之间的资料、索引和会话相互隔离。" onClose={() => { if (!busy) onCloseCreate(); }}><BaseForm name={name} description={description} busy={busy} submitText="确认创建" applyTemplate={applyTemplate} template={template} onApplyTemplate={setApplyTemplate} onName={setName} onDescription={setDescription} onCancel={onCloseCreate} onSubmit={create}/></Dialog> : null}
    {editing ? <Dialog open title="编辑知识库" description="知识库类型不可修改。" onClose={closeEdit}><BaseForm name={name} description={description} busy={busy} submitText="保存" onName={setName} onDescription={setDescription} onCancel={closeEdit} onSubmit={saveEdit}/></Dialog> : null}
    {deleting ? (() => { const blocked = deleteBlockReason(deleting); return <Dialog open title={blocked ? "无法删除" : "删除知识库"} onClose={() => { if (!busy) setDeleting(null); }}><div className="confirm-copy">{deleting.is_default ? <>默认知识库不能删除，它是新资料的兜底归属。</> : blocked ? <>「{deleting.name}」下还有 <strong>{deleting.document_count} 份资料</strong>。<p>删除知识库会连带删除它的全部资料、索引与会话，所以需要先清空。</p></> : <>确认删除「{deleting.name}」吗？<p>该知识库当前没有资料，删除后其索引与会话一并移除。</p></>}</div><DialogActions><Button variant="secondary" loading={busy} onClick={() => setDeleting(null)}>{blocked ? "知道了" : "取消"}</Button>{blocked ? (!deleting.is_default && deleting.document_count ? <Button onClick={() => { const target = deleting; setDeleting(null); onOpen(`/knowledge-bases/${target.knowledge_base_id}`); }}>去清空资料</Button> : null) : <Button variant="destructive" loading={busy} onClick={() => void remove()}>确认删除</Button>}</DialogActions></Dialog>; })() : null}
    {showTemplate && template ? <CategoryTemplateModal template={template} onChanged={setTemplate} onClose={() => setShowTemplate(false)}/> : null}
  </section>;
}

/**
 * 删除被挡住的原因；返回空串表示可以删。
 *
 * 判定依据是后端下发的 ``allowed_actions``，而不是前端自己重算一遍条件——前端算不
 * 全后端的判断，一旦后端多加一条限制，这里就会出现「按钮禁用了却说不出原因」。
 * 所以最后留一个兜底文案，宁可说得笼统，也不能什么都不说。
 */
function deleteBlockReason(item: KnowledgeBase): string {
  if ((item.allowed_actions ?? ["detail"]).includes("delete")) return "";
  if (item.is_default) return "默认知识库不能删除";
  if (item.document_count) return `请先删除 ${item.document_count} 份资料`;
  return "当前不可删除";
}

function BaseForm({ name, description, busy, submitText, applyTemplate, template, onApplyTemplate, onName, onDescription, onCancel, onSubmit }: { name: string; description: string; busy: boolean; submitText: string; applyTemplate?: boolean; template?: CategoryTemplate | null; onApplyTemplate?: (value: boolean) => void; onName: (value: string) => void; onDescription: (value: string) => void; onCancel: () => void; onSubmit: (event: FormEvent) => void }) { const activeItems = template?.items.filter((item) => item.active) ?? []; const templateSummary = template === null ? "正在读取默认分类模板…" : activeItems.length ? `将复制 ${activeItems.length} 个有效分类：${activeItems.slice(0, 4).map((item) => item.name).join("、")}${activeItems.length > 4 ? "等" : ""}` : "当前模板无有效分类，新知识库的分类列表将为空"; return <form className="modal-form" onSubmit={onSubmit}><label htmlFor="base-name">知识库名称</label><Input autoFocus id="base-name" value={name} onChange={(event) => onName(event.target.value)} maxLength={80} required/><label htmlFor="base-description">描述 <span>选填</span></label><textarea id="base-description" value={description} onChange={(event) => onDescription(event.target.value)} maxLength={500} rows={4}/>{onApplyTemplate ? <div className="template-apply-option"><label><input type="checkbox" checked={applyTemplate} onChange={(event) => onApplyTemplate(event.target.checked)}/>应用默认分类模板</label><small>{templateSummary}</small><small>资料可以暂时没有分类，系统不会替它创建占位分类。</small></div> : null}<DialogActions><Button variant="secondary" loading={busy} onClick={onCancel}>取消</Button><Button type="submit" loading={busy}>{submitText}</Button></DialogActions></form>; }
