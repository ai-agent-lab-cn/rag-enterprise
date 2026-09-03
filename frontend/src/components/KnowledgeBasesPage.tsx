import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { CategoryTemplate, KnowledgeBase } from "../types";
import { CategoryTemplateModal } from "./CategoryTemplateModal";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Checkbox } from "./ui/Checkbox";
import { Column, DataTable } from "./ui/DataTable";
import { Dialog, DialogActions } from "./ui/Dialog";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input } from "./ui/Input";
import { Pagination } from "./ui/Pagination";
import { RowAction, RowActions } from "./ui/RowActions";
import { Select } from "./ui/Select";
import { Toolbar } from "./ui/Toolbar";
import { useConfirm } from "./ui/useConfirm";
import { useToast } from "./ui/Toast";

const STATUS_LABEL = { empty: "空库", processing: "处理中", ready: "可用", failed: "失败" } as const;
// Badge 只有 5 档语义色，没有专门的「进行中/蓝色」；processing 借用 brand（品牌色）表达
// 「正在推进」，与 ready 的 success（绿）、failed 的 danger（红）区分开。
const STATUS_TONE = { empty: "neutral", processing: "brand", ready: "success", failed: "danger" } as const;
function formatBytes(bytes: number) { if (!bytes) return "0 KB"; const units = ["B", "KB", "MB", "GB"]; const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 3); return `${(bytes / 1024 ** exponent).toFixed(exponent ? 1 : 0)} ${units[exponent]}`; }

export function KnowledgeBasesPage({ isAdmin, onOpen, showCreate, onCloseCreate }: { isAdmin: boolean; onOpen: (path: string) => void; showCreate: boolean; onCloseCreate: () => void }) {
  const [items, setItems] = useState<KnowledgeBase[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState(""); const [description, setDescription] = useState("");
  const [search, setSearch] = useState(""); const [status, setStatus] = useState("");
  const [sort, setSort] = useState<"updated_desc" | "updated_asc">("updated_desc");
  const [page, setPage] = useState(0); const [hasNext, setHasNext] = useState(false); const pageSize = 10;
  const [editing, setEditing] = useState<KnowledgeBase | null>(null);
  // 仅用于「无法删除」这一分支：需要区分「知道了」与「去清空资料」两个出口，
  // useConfirm 的通用弹层只有一个确认按钮，装不下第二个出口，所以这里单独留一个 Dialog。
  const [blocked, setBlocked] = useState<KnowledgeBase | null>(null);
  const [error, setError] = useState("");
  const [template, setTemplate] = useState<CategoryTemplate | null>(null);
  const [showTemplate, setShowTemplate] = useState(false);
  const [applyTemplate, setApplyTemplate] = useState(true);
  const { confirm, dialog: confirmDialog } = useConfirm();
  const toast = useToast();
  const loadTemplate = async () => { try { const value = await api.getDefaultCategoryTemplate(); setTemplate(value); return value; } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取分类模板。"); return null; } };
  useEffect(() => {
    if (!isAdmin) return;
    api.getDefaultCategoryTemplate().then(setTemplate, (reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "无法读取分类模板。");
    });
  }, [isAdmin]);
  const load = useCallback(() => { setError(""); setItems(null); return api.listKnowledgeBases({ name: search.trim(), status, sort, offset: page * pageSize, limit: pageSize + 1 }).then((result) => { setHasNext(result.length > pageSize); setItems(result.slice(0, pageSize)); }, (reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取知识库。")); }, [search, status, sort, page]);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 250); return () => window.clearTimeout(timer); }, [load]);
  const create = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true);
    try {
      const item = await api.createKnowledgeBase(name.trim(), description.trim(), applyTemplate);
      setName(""); setDescription(""); setApplyTemplate(true); onCloseCreate();
      toast.success(`已创建知识库「${item.name}」`);
      await load();
      onOpen(`/knowledge-bases/${item.knowledge_base_id}`);
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "创建失败。");
    } finally { setBusy(false); }
  };
  const saveEdit = async (event: FormEvent) => {
    event.preventDefault(); if (!editing) return; setBusy(true);
    try {
      await api.updateKnowledgeBase(editing.knowledge_base_id, name.trim(), description.trim());
      toast.success(`已保存「${name.trim()}」`);
      setEditing(null); setName(""); setDescription("");
      await load();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "保存失败。");
    } finally { setBusy(false); }
  };
  const closeEdit = () => { if (!busy) { setEditing(null); setName(""); setDescription(""); } };
  const startDelete = (item: KnowledgeBase) => {
    if (deleteBlockReason(item)) { setBlocked(item); return; }
    confirm({
      title: "删除知识库",
      consequence: `确认删除「${item.name}」吗？该知识库当前没有资料，删除后其索引与会话一并移除。`,
      confirmLabel: "确认删除",
      tone: "destructive",
      onConfirm: async () => {
        try {
          await api.deleteKnowledgeBase(item.knowledge_base_id);
          toast.success(`已删除「${item.name}」`);
          await load();
        } catch (reason) {
          toast.error(reason instanceof Error ? reason.message : "删除失败。");
          throw reason;
        }
      },
    });
  };
  const rowActions = (item: KnowledgeBase): RowAction[] => {
    const actions: RowAction[] = [{ label: "详情", onSelect: () => onOpen(`/knowledge-bases/${item.knowledge_base_id}`) }];
    if ((item.allowed_actions ?? ["detail"]).includes("edit")) {
      actions.push({ label: "编辑", onSelect: () => { setEditing(item); setName(item.name); setDescription(item.description); } });
    }
    if (item.current_user_permission === "admin") {
      actions.push({ label: "删除", tone: "destructive", onSelect: () => startDelete(item) });
    }
    return actions;
  };
  const columns: Column<KnowledgeBase>[] = [
    {
      key: "name", header: "知识库名称", width: "190px", truncate: false,
      render: (item) => (
        <span className="flex min-w-0 items-center gap-2">
          <Button variant="link" className="min-w-0 justify-start truncate font-medium text-ink no-underline hover:text-brand hover:no-underline" onClick={() => onOpen(`/knowledge-bases/${item.knowledge_base_id}`)}>
            {item.name}
          </Button>
          <Badge shape="type" tone={item.is_default ? "brand" : "neutral"} className="shrink-0">
            {item.is_default ? "默认知识库" : "独立知识库"}
          </Badge>
        </span>
      ),
    },
    { key: "description", header: "描述", width: "190px", render: (item) => <span title={item.description || "—"}>{item.description || "—"}</span> },
    { key: "document_count", header: "文档数量", width: "90px", numeric: true, render: (item) => item.document_count },
    { key: "source_file_bytes", header: "存储空间", width: "90px", numeric: true, render: (item) => formatBytes(item.source_file_bytes ?? 0) },
    {
      key: "status", header: "状态", width: "90px",
      render: (item) => { const itemStatus = item.index_status ?? (item.document_count ? "ready" : "empty"); return <Badge shape="status" tone={STATUS_TONE[itemStatus]}>{STATUS_LABEL[itemStatus]}</Badge>; },
    },
    { key: "permission", header: "权限", width: "90px", render: (item) => (item.current_user_permission === "admin" ? "管理员" : "可使用") },
    { key: "updated_at", header: "更新时间", width: "145px", render: (item) => new Date(item.updated_at).toLocaleString("zh-CN") },
    { key: "actions", header: "操作", width: "190px", align: "right", truncate: false, render: (item) => <RowActions rowLabel={item.name} actions={rowActions(item)} /> },
  ];
  const filtered = Boolean(search.trim() || status);
  return <section className="mx-auto max-w-[1440px] p-[26px_24px_52px] min-[1025px]:p-[20px_20px_40px]" aria-label="知识库管理">
    <Toolbar
      filters={<>
        <label><span className="sr-only">搜索知识库</span><Input size="sm" className="w-52" value={search} onChange={(event) => { setSearch(event.target.value); setPage(0); }} placeholder="搜索知识库名称" /></label>
        <Select size="sm" className="w-28" aria-label="状态筛选" value={status} onChange={(event) => { setStatus(event.target.value); setPage(0); }}><option value="">全部状态</option><option value="empty">空库</option><option value="processing">处理中</option><option value="ready">可用</option><option value="failed">失败</option></Select>
        <Select size="sm" className="w-28" aria-label="更新时间排序" value={sort} onChange={(event) => { setSort(event.target.value as typeof sort); setPage(0); }}><option value="updated_desc">最近更新</option><option value="updated_asc">最早更新</option></Select>
      </>}
      actions={isAdmin ? <Button variant="secondary" size="sm" onClick={() => { setShowTemplate(true); if (!template) void loadTemplate(); }}>知识库分类模板</Button> : null}
    />
    {error ? (
      <ErrorBanner>{error} <Button variant="ghost" size="sm" onClick={() => void load()}>重试</Button></ErrorBanner>
    ) : (
      <>
        <DataTable
          rows={items}
          columns={columns}
          rowKey={(item) => item.knowledge_base_id}
          label="知识库列表"
          emptyState={filtered
            ? { kind: "filtered", title: "没有符合条件的知识库", description: "调整搜索词或筛选条件后重试。" }
            : { kind: "empty", title: "还没有知识库", description: "创建一个知识库后即可上传资料并开始问答。" }}
        />
        {items !== null ? <Pagination page={page} hasNext={hasNext} onChange={setPage} label="知识库分页" /> : null}
      </>
    )}
    {showCreate ? <Dialog open title="新建知识库" description="知识库之间的资料、索引和会话相互隔离。" onClose={() => { if (!busy) onCloseCreate(); }}><BaseForm name={name} description={description} busy={busy} submitText="确认创建" applyTemplate={applyTemplate} template={template} onApplyTemplate={setApplyTemplate} onName={setName} onDescription={setDescription} onCancel={onCloseCreate} onSubmit={create}/></Dialog> : null}
    {editing ? <Dialog open title="编辑知识库" description="知识库类型不可修改。" onClose={closeEdit}><BaseForm name={name} description={description} busy={busy} submitText="保存" onName={setName} onDescription={setDescription} onCancel={closeEdit} onSubmit={saveEdit}/></Dialog> : null}
    {blocked ? <Dialog open title="无法删除" onClose={() => setBlocked(null)}>
        <div className="p-[20px_22px] text-[#626b7f] text-[14px] leading-[1.7]">
          {blocked.is_default ? (
            <>默认知识库不能删除，它是新资料的兜底归属。</>
          ) : blocked.document_count ? (
            <>「{blocked.name}」下还有 <strong className="text-[#242c40]">{blocked.document_count} 份资料</strong>。<p>删除知识库会连带删除它的全部资料、索引与会话，所以需要先清空。</p></>
          ) : (
            // 前两个分支之外的兜底：allowed_actions 挡住了删除，但既不是默认库也没有资料，
            // 说不出更具体的原因就照实说——deleteBlockReason() 末尾的兜底文案，不能空手关掉弹层。
            <>{deleteBlockReason(blocked)}</>
          )}
        </div>
        <DialogActions>
          <Button variant="secondary" onClick={() => setBlocked(null)}>知道了</Button>
          {!blocked.is_default && blocked.document_count ? <Button onClick={() => { const target = blocked; setBlocked(null); onOpen(`/knowledge-bases/${target.knowledge_base_id}`); }}>去清空资料</Button> : null}
        </DialogActions>
      </Dialog> : null}
    {confirmDialog}
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

function BaseForm({ name, description, busy, submitText, applyTemplate, template, onApplyTemplate, onName, onDescription, onCancel, onSubmit }: { name: string; description: string; busy: boolean; submitText: string; applyTemplate?: boolean; template?: CategoryTemplate | null; onApplyTemplate?: (value: boolean) => void; onName: (value: string) => void; onDescription: (value: string) => void; onCancel: () => void; onSubmit: (event: FormEvent) => void }) { const activeItems = template?.items.filter((item) => item.active) ?? []; const templateSummary = template === null ? "正在读取默认分类模板…" : activeItems.length ? `将复制 ${activeItems.length} 个有效分类：${activeItems.slice(0, 4).map((item) => item.name).join("、")}${activeItems.length > 4 ? "等" : ""}` : "当前模板无有效分类，新知识库的分类列表将为空"; return <form className="grid gap-[9px] pt-[20px] px-[22px]" onSubmit={onSubmit}><label className="text-[#4e576c] text-[13px] font-semibold" htmlFor="base-name">知识库名称</label><Input className="py-[10px]" autoFocus id="base-name" value={name} onChange={(event) => onName(event.target.value)} maxLength={80} required/><label className="text-[#4e576c] text-[13px] font-semibold" htmlFor="base-description">描述 <span className="text-[#939bad] text-[11px] font-normal">选填</span></label><textarea id="base-description" className="w-full text-[#242c41] border border-line-firm rounded-[7px] bg-white px-[11px] py-[10px] text-[14px] resize-y placeholder:text-[#a0a7b7]" value={description} onChange={(event) => onDescription(event.target.value)} maxLength={500} rows={4}/>{onApplyTemplate ? <div className="grid gap-[4px] border-t border-divider pt-[10px]"><Checkbox showLabel label="应用默认分类模板" checked={!!applyTemplate} onCheckedChange={onApplyTemplate}/><small className="text-[#7b8395] text-[11px]">{templateSummary}</small><small className="text-[#7b8395] text-[11px]">资料可以暂时没有分类，系统不会替它创建占位分类。</small></div> : null}<DialogActions><Button variant="secondary" loading={busy} onClick={onCancel}>取消</Button><Button type="submit" loading={busy}>{submitText}</Button></DialogActions></form>; }
