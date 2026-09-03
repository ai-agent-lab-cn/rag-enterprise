import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ConversationSummary, DataSource, DocumentCategory, DocumentInfo, DocumentVersion, IndexVersion, KnowledgeBase, User } from "../types";
import { DocumentPanel } from "./DocumentPanel";
import { Dialog, DialogActions } from "./ui/Dialog";
import { Button } from "./ui/Button";
import { Badge } from "./ui/Badge";
import { type Column, DataTable } from "./ui/DataTable";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input } from "./ui/Input";
import { ListItemButton } from "./ui/ListItemButton";
import { type RowAction, RowActions } from "./ui/RowActions";
import { Select } from "./ui/Select";
import { Tabs, type TabItem } from "./ui/Tabs";
import { ParsingPanel } from "./ParsingPanel";
import { KnowledgeBaseDataSourcesPanel } from "./KnowledgeBaseDataSourcesPanel";

const STATUS = { empty: "空库", processing: "处理中", ready: "可用", failed: "失败" } as const;
// 与 KnowledgeBasesPage 的 STATUS_TONE 同一套约定：同一个 index_status 取值域，
// 在两处渲染成不同颜色才是真正的不一致——见 CLAUDE.md 第二条。
const STATUS_TONE = { empty: "neutral", processing: "brand", ready: "success", failed: "danger" } as const;
const VERSION_STATUS = { pending: "等待索引", indexing: "索引中", ready: "可用", failed: "失败", superseded: "历史版本" } as const;
const CATEGORY_ORIGIN_LABEL = {
  template_copy: "默认模板复制",
  manual: "手动创建",
  migration: "历史迁移",
} as const;
function formatBytes(value: number) { if (!value) return "0 KB"; const divisor = value >= 1024 ** 2 ? 1024 ** 2 : 1024; return `${(value / divisor).toFixed(1)} ${divisor === 1024 ? "KB" : "MB"}`; }

/** Document Version 行的状态徽章：解析未完成时优先展示解析阶段，其次才是索引状态。 */
function versionRowBadge(item: DocumentVersion) {
  if (item.parse_status === "failed" || item.status === "failed") {
    return { tone: "danger" as const, label: item.parse_status === "failed" ? item.parse_failure_code || "解析失败" : VERSION_STATUS.failed };
  }
  if (item.parse_status !== "ready" || item.status === "indexing" || item.status === "pending") {
    return { tone: "brand" as const, label: item.parse_status !== "ready" ? `解析${item.parse_status}` : VERSION_STATUS[item.status] };
  }
  return { tone: "success" as const, label: VERSION_STATUS[item.status] };
}

/* 五列都是「主值 + 小字副值」的两行结构，truncate 一律关掉——它会把副行连同主值
   一起压成单行并加省略号。宽度显式给：DataTable 是 table-fixed，均分会把
   embedding_model 这种长标识截断。 */
const INDEX_VERSION_COLUMNS: Column<IndexVersion>[] = [
  {
    key: "version",
    header: "版本",
    width: "24%",
    truncate: false,
    render: (item) => (
      <>
        <strong>{item.index_version_id}</strong>
        <small className="mt-[3px] block text-sm text-ink-faint">{item.config_fingerprint.slice(0, 12)}</small>
      </>
    ),
  },
  {
    key: "status",
    header: "状态",
    width: "12%",
    truncate: false,
    render: (item) => (
      <Badge shape="status" tone={item.status === "failed" ? "danger" : item.status === "building" ? "brand" : "success"}>
        {item.status}
      </Badge>
    ),
  },
  {
    key: "parser",
    header: "Parser / Chunking",
    width: "22%",
    truncate: false,
    render: (item) => (
      <>
        {item.parser_version}
        <small className="mt-[3px] block text-sm text-ink-faint">{item.chunking_version}</small>
      </>
    ),
  },
  {
    key: "embedding",
    header: "Embedding",
    width: "24%",
    truncate: false,
    render: (item) => (
      <>
        {item.embedding_model}
        <small className="mt-[3px] block text-sm text-ink-faint">{item.embedding_dimension} 维</small>
      </>
    ),
  },
  { key: "report", header: "质量报告", width: "18%", render: (item) => item.evaluation_report_id || "待验证" },
];

export function KnowledgeBaseDetailPage({ id, onOpen }: { id: string; onOpen: (path: string) => void }) {
  const [activeTab, setActiveTab] = useState<"documents" | "data_sources" | "categories" | "parsing" | "versions" | "members" | "conversations">("documents");
  const [base, setBase] = useState<KnowledgeBase | null>(null); const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [versions, setVersions] = useState<DocumentVersion[]>([]); const [members, setMembers] = useState<User[]>([]);
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [indexVersions, setIndexVersions] = useState<IndexVersion[]>([]);
  const [categories, setCategories] = useState<DocumentCategory[]>([]);
  // 新建与编辑共用一个弹层：同一个对象的两种操作长得一样、字段一致，用户在一处
  // 学会的填法能直接用在另一处。null 表示不显示。
  const [categoryForm, setCategoryForm] = useState<{ mode: "create" } | { mode: "edit"; item: DocumentCategory } | null>(null);
  const [deletingCategory, setDeletingCategory] = useState<DocumentCategory | null>(null);
  const [categoryDraft, setCategoryDraft] = useState({ name: "", description: "", sort_order: 100 });
  const [conversations, setConversations] = useState<ConversationSummary[]>([]); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const [aclTarget, setAclTarget] = useState<{ kind: "document" | "source"; id: string; name: string; version: number; allow: string[]; deny: string[] } | null>(null);
  const [aclDraft, setAclDraft] = useState<Record<string, "inherit" | "allow" | "deny">>({});
  const [savingAcl, setSavingAcl] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{ completed: number; total: number } | null>(null);
  const load = useCallback(async () => { const detail = await api.getKnowledgeBase(id); const [docs, history, versionItems, indexVersionItems, memberItems, sourceItems, categoryItems] = await Promise.all([api.listKnowledgeBaseDocuments(id), api.listConversations(id), api.listKnowledgeBaseDocumentVersions(id), api.listKnowledgeBaseIndexVersions(id), detail.current_user_permission === "admin" ? api.listKnowledgeBaseMembers(id) : Promise.resolve([]), detail.current_user_permission === "admin" ? api.listDataSources(0, 100) : Promise.resolve([]), api.listKnowledgeBaseCategories(id)]); setBase(detail); setDocuments(docs); setConversations(history); setVersions(versionItems); setIndexVersions(indexVersionItems); setMembers(memberItems); setDataSources(sourceItems.filter((item) => item.knowledge_base_id === id)); setCategories(categoryItems); }, [id]);
  useEffect(() => { Promise.resolve().then(load).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取知识库。")); }, [load]);
  // 失败文件名收集齐后 throw 出去，交给 DocumentPanel 的 toast 展示——它是持续显示
  // 的错误提示（见 ui/Toast.tsx），完整文件名列表已经在消息里，页面横幅只会重复。
  // load() 必须在 throw 之前跑完：批量上传里已经成功的那些，不能因为个别失败就不刷新出来。
  const upload = async (files: File[]) => { setBusy(true); setError(""); setUploadProgress({ completed: 0, total: files.length }); const failed: string[] = []; try { for (const [index, file] of files.entries()) { try { await api.uploadKnowledgeBaseDocument(id, file); } catch { failed.push(file.name); } finally { setUploadProgress({ completed: index + 1, total: files.length }); } } await load(); if (failed.length) throw new Error(`以下文件上传失败：${failed.join("、")}`); } finally { setBusy(false); setUploadProgress(null); } };
  // 不再吞异常：删除失败要让 DocumentPanel 里的 useConfirm 接住，在弹层内展示错误
  // 并保持弹层打开供重试，而不是被这里的 catch 拦下、只留一条和成功 toast 互相矛盾的页面横幅。
  const remove = async (documentId: string) => { setError(""); await api.deleteKnowledgeBaseDocument(id, documentId); await load(); };
  const updateMetadata = async (documentId: string, category: string, tags: string[]) => { setError(""); try { const updated = await api.updateKnowledgeBaseDocumentMetadata(id, documentId, { category, tags }); setDocuments((items) => items.map((item) => item.document_id === documentId ? updated : item)); } catch (reason) { setError(reason instanceof Error ? reason.message : "元数据更新失败。"); throw reason; } };
  const reclassify = async (documentIds: string[]) => { setError(""); try { await api.reclassifyDocuments(id, documentIds); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "重新分类失败。"); throw reason; } };
  const batchCategory = async (documentIds: string[], categoryId: string) => { setBusy(true); setError(""); try { await api.batchAssignDocumentCategory(id, documentIds, categoryId); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "批量归类失败。"); throw reason; } finally { setBusy(false); } };
  const toggleCategory = async (item: DocumentCategory) => { setBusy(true); try { await api.updateKnowledgeBaseCategory(id, item.category_id, { name: item.name, description: item.description, sort_order: item.sort_order, active: !item.active }); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "更新分类失败。"); } finally { setBusy(false); } };
  const openCategoryEditor = (item: DocumentCategory) => { setError(""); setCategoryForm({ mode: "edit", item }); setCategoryDraft({ name: item.name, description: item.description, sort_order: item.sort_order }); };
  // 排序默认排到末尾：写死 100 会让每个新分类都和模板分类挤在同一档。
  const openCategoryCreator = () => { setError(""); setCategoryForm({ mode: "create" }); setCategoryDraft({ name: "", description: "", sort_order: categories.reduce((max, item) => Math.max(max, item.sort_order), 0) + 100 }); };
  // 必填校验用「点击后报错」而不是禁用提交按钮：CLAUDE.md 第一条点名过这处不一致——
  // 分类模板弹框（CategoryTemplateModal）那半已经是这样，这里跟它保持一致。
  const saveCategory = async () => { if (!categoryForm) return; const name = categoryDraft.name.trim(); if (!name) { setError("请输入分类名称。"); return; } const payload = { name, description: categoryDraft.description.trim(), sort_order: categoryDraft.sort_order }; setBusy(true); setError(""); try { if (categoryForm.mode === "create") await api.createKnowledgeBaseCategory(id, payload); else await api.updateKnowledgeBaseCategory(id, categoryForm.item.category_id, { ...payload, active: categoryForm.item.active }); await load(); setCategoryForm(null); } catch (reason) { setError(reason instanceof Error ? reason.message : categoryForm.mode === "create" ? "创建分类失败。" : "更新分类失败。"); } finally { setBusy(false); } };
  const deleteCategory = async (item: DocumentCategory) => { setBusy(true); try { await api.deleteKnowledgeBaseCategory(id, item.category_id); setDeletingCategory(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "删除分类失败。"); } finally { setBusy(false); } };
  const categoryActions = (item: DocumentCategory): RowAction[] => [
    { label: "编辑", blockedReason: busy ? "正在处理分类" : undefined, onSelect: () => openCategoryEditor(item) },
    { label: item.active ? "停用" : "启用", blockedReason: busy ? "正在处理分类" : undefined, onSelect: () => void toggleCategory(item) },
    { label: "删除", tone: "destructive", blockedReason: busy ? "正在处理分类" : undefined, onSelect: () => setDeletingCategory(item) },
  ];
  const categoryColumns: Column<DocumentCategory>[] = [
    { key: "name", header: "分类名称", width: "16%", render: (item) => <strong className="font-medium text-ink">{item.name}</strong> },
    { key: "description", header: "描述", width: "24%", render: (item) => <span title={item.description || "—"}>{item.description || "—"}</span> },
    { key: "documents", header: "资料数量", width: "10%", numeric: true, render: (item) => item.document_count },
    { key: "sort", header: "排序", width: "8%", numeric: true, render: (item) => item.sort_order },
    { key: "origin", header: "初始来源", width: "14%", render: (item) => <Badge shape="type" tone="neutral">{CATEGORY_ORIGIN_LABEL[item.origin_type]}</Badge> },
    { key: "status", header: "状态", width: "10%", render: (item) => <Badge shape="status" tone={item.active ? "success" : "neutral"}>{item.active ? "启用" : "停用"}</Badge> },
    { key: "actions", header: "操作", width: "18%", align: "right", truncate: false, render: (item) => <RowActions rowLabel={item.name} actions={categoryActions(item)} /> },
  ];
  const templateCategories = categories.filter((item) => item.origin_type === "template_copy");
  const managedCategories = categories.filter((item) => item.origin_type !== "template_copy");
  const openAcl = (target: typeof aclTarget) => { if (!target) return; const draft: Record<string, "inherit" | "allow" | "deny"> = {}; members.forEach((member) => { draft[member.user_id] = target.deny.includes(member.user_id) ? "deny" : target.allow.includes(member.user_id) ? "allow" : "inherit"; }); setAclDraft(draft); setAclTarget(target); };
  const saveAcl = async () => { if (!aclTarget) return; setSavingAcl(true); setError(""); const policy = { allow_user_ids: Object.entries(aclDraft).filter(([, value]) => value === "allow").map(([userId]) => userId), deny_user_ids: Object.entries(aclDraft).filter(([, value]) => value === "deny").map(([userId]) => userId) }; try { if (aclTarget.kind === "document") await api.updateKnowledgeBaseDocumentAcl(id, aclTarget.id, policy); else await api.updateDataSourceAcl(aclTarget.id, policy); await load(); setAclTarget(null); } catch (reason) { setError(reason instanceof Error ? reason.message : "ACL 更新失败。"); } finally { setSavingAcl(false); } };
  // 弹层打开时错误只显示在弹层内：Radix 给背景内容加了 aria-hidden，
  // 顶部横幅在弹层背后，既看不见也不会被屏幕阅读器读到。
  const dialogOpen = Boolean(categoryForm || deletingCategory || aclTarget);

  const tabs: TabItem[] = [
    { value: "documents", label: "资料", count: documents.length },
    { value: "data_sources", label: "数据源", count: dataSources.length },
    { value: "categories", label: "分类管理", count: categories.length },
    { value: "parsing", label: "解析与切片", count: versions.filter((item) => item.parse_status === "ready").length },
    { value: "versions", label: "版本治理", count: versions.length },
    { value: "members", label: "权限边界", count: members.length },
    { value: "conversations", label: "会话", count: conversations.length },
  ];

  return <section className="mx-auto max-w-[1440px] p-[26px_24px_52px] min-[1025px]:p-[20px_20px_40px]"><div className="flex items-center justify-between gap-4 mb-[22px]"><Button variant="link" onClick={() => onOpen("/knowledge-bases")}>← 返回知识库</Button>{base ? <Button size="sm" onClick={() => onOpen(`/chat?knowledge_base_id=${id}`)}>在此知识库提问 →</Button> : null}</div>{error && !dialogOpen ? <ErrorBanner>{error}</ErrorBanner> : null}{base ? <>
    <section className="mb-3.5 grid grid-cols-[1.1fr_1.5fr_0.7fr_0.7fr_1fr] overflow-hidden rounded-[10px] border border-line bg-surface max-md:grid-cols-2">
      <div className="grid min-h-16 min-w-0 content-center gap-[5px] border-r border-divider px-3 py-[9px] max-md:border-r-0 max-md:border-b max-md:even:border-r-0">
        <span className="text-[10px] text-[#8b92a4]">名称</span>
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <strong className="min-w-0 truncate text-[12px] text-[#2d3549]">{base.name}</strong>
          <Badge shape="type" tone="brand" className="shrink-0">{base.is_default ? "默认知识库" : "独立知识库"}</Badge>
        </span>
      </div>
      <div className="grid min-h-16 min-w-0 content-center gap-[5px] border-r border-divider px-3 py-[9px] max-md:border-r-0 max-md:border-b">
        <span className="text-[10px] text-[#8b92a4]">描述</span>
        <strong className="truncate text-[12px] text-[#2d3549]">{base.description || "—"}</strong>
      </div>
      <div className="grid min-h-16 min-w-0 content-center gap-[5px] border-r border-divider px-3 py-[9px] max-md:border-r-0 max-md:border-b max-md:even:border-r-0">
        <span className="text-[10px] text-[#8b92a4]">文件占用</span>
        <strong className="truncate text-[12px] text-[#2d3549]">{formatBytes(base.source_file_bytes)}</strong>
      </div>
      <div className="grid min-h-16 min-w-0 content-center gap-[5px] border-r border-divider px-3 py-[9px] max-md:border-r-0 max-md:border-b">
        <span className="text-[10px] text-[#8b92a4]">索引状态</span>
        <Badge shape="status" tone={STATUS_TONE[base.index_status]} className="w-fit">{STATUS[base.index_status]}</Badge>
      </div>
      <div className="grid min-h-16 min-w-0 content-center gap-[5px] px-3 py-[9px] max-md:even:border-r-0">
        <span className="text-[10px] text-[#8b92a4]">更新时间</span>
        <strong className="truncate text-[12px] text-[#2d3549]">{new Date(base.updated_at).toLocaleString("zh-CN")}</strong>
      </div>
    </section>
    <Tabs items={tabs} value={activeTab} onChange={(value) => setActiveTab(value as typeof activeTab)} label="知识库详情">
      {activeTab === "documents" ? <DocumentPanel documents={documents} categories={categories} loading={busy} uploadProgress={uploadProgress} onUpload={upload} onDelete={remove} onUpdateMetadata={updateMetadata} onBatchCategory={batchCategory} onReclassify={reclassify}/> : null}
      {activeTab === "data_sources" ? <KnowledgeBaseDataSourcesPanel knowledgeBaseId={id} items={dataSources} categories={categories} onRefresh={load}/> : null}
      {activeTab === "categories" ? <section className="grid gap-3">
        {templateCategories.length ? <section aria-label="默认模板分类" className="flex flex-wrap items-center gap-2 border-b border-divider pb-3">
          <span className="mr-1 text-sm font-medium text-ink-muted">默认模板分类：</span>｜
          {templateCategories.map((item) => <span key={item.category_id} className="max-w-44 truncate" title={`${item.name} · 创建知识库时复制的初始分类`}><Badge shape="type" tone="neutral">{item.name}</Badge>｜</span>)}
          <small className="text-sm text-ink-faint">只读 · 创建知识库时复制</small>
        </section> : null}
        <div className="flex flex-wrap items-center justify-between gap-2"><p className="m-0 text-sm text-ink-faint">以下是本知识库独立维护的分类，不会同步到默认模板。</p><Button size="sm" loading={busy} onClick={openCategoryCreator}>＋ 新建分类</Button></div>
        <DataTable rows={managedCategories} columns={categoryColumns} rowKey={(item) => item.category_id} label="分类管理列表" emptyState={{ kind: "empty", title: "暂无知识库独立分类", description: "新建分类后，可用于当前知识库的资料归类和问答筛选。" }}/>
      </section> : null}
      {activeTab === "parsing" ? <ParsingPanel knowledgeBaseId={id} versions={versions} canManage={base.current_user_permission === "admin"} onRefresh={load}/> : null}
      {activeTab === "versions" ? <section className="grid"><h3 className="mt-[18px] mb-2 text-[16px] font-bold text-ink">Index Version</h3><DataTable label="Index Version" rows={indexVersions} rowKey={(item) => item.index_version_id} columns={INDEX_VERSION_COLUMNS} emptyState={{ kind: "empty", title: "还没有 Index Version。", description: "重建索引后这里会列出每一次的解析、切片与向量配置。" }}/><h3 className="mt-[18px] mb-2 text-[16px] font-bold text-ink">Document Version</h3>{versions.length ? <div className="grid">{versions.map((item) => { const badge = versionRowBadge(item); return <article className="grid grid-cols-[minmax(180px,1fr)_minmax(180px,0.8fr)_auto] items-center gap-3 border-t border-divider py-[11px]" key={item.document_version_id}><div className="grid min-w-0 gap-1"><b className="truncate text-[12px]">{item.filename}</b><span className="truncate text-[10px] text-[#81899c]">V{item.version_number}{item.is_current ? <em className="ml-1.5 not-italic text-brand">当前版本</em> : null}</span></div><div className="grid min-w-0 gap-1"><span className="truncate text-[10px] text-[#81899c]">{formatBytes(item.source_file_bytes)} · {item.content_sha256.slice(0, 10)}</span><span className="truncate text-[10px] text-[#81899c]">{item.parser_name || "旧版解析"} {item.parser_version || "legacy"} · {item.chunking_version || "旧版切片"} · {item.node_count} 节点 / {item.parsed_chunk_count} Chunk</span><span className="truncate text-[10px] text-[#81899c]">{new Date(item.created_at).toLocaleString("zh-CN")}</span></div><Badge shape="status" tone={badge.tone}>{badge.label}</Badge>{item.failure_reason ? <small className="col-span-full truncate text-[10px] text-danger-text" title={item.failure_reason}>{item.failure_reason}</small> : null}</article>; })}</div> : <p className="text-md text-[#737c90] leading-[1.6]">还没有 Document Version。</p>}</section> : null}
      {activeTab === "members" ? <section className="grid gap-3">{base.current_user_permission === "admin" ? <><p className="m-0 text-[12px] text-ink-faint">Deny 优先；未配置时继承知识库成员权限。ACL 更新后立即影响下一次检索。</p><h3 className="mt-2 text-[13px] text-[#151a31]">数据源 ACL</h3>{dataSources.length ? <div className="border-t border-line">{dataSources.map((item) => <div className="flex min-h-[54px] items-center justify-between gap-4 border-b border-divider px-0.5 py-2" key={item.data_source_id}><span className="grid min-w-0 gap-[3px]"><strong className="truncate text-[13px] text-ink">{item.name}</strong><small className="text-[11px] text-ink-faint">版本 {item.acl_version} · Allow {item.allow_user_ids.length} · Deny {item.deny_user_ids.length}</small></span><Button variant="ghost" size="sm" onClick={() => openAcl({ kind: "source", id: item.data_source_id, name: item.name, version: item.acl_version, allow: item.allow_user_ids, deny: item.deny_user_ids })}>配置</Button></div>)}</div> : <p className="text-md text-[#737c90] leading-[1.6]">当前知识库没有独立数据源。</p>}<h3 className="mt-2 text-[13px] text-[#151a31]">文档 ACL</h3>{documents.length ? <div className="border-t border-line">{documents.map((item) => <div className="flex min-h-[54px] items-center justify-between gap-4 border-b border-divider px-0.5 py-2" key={item.document_id}><span className="grid min-w-0 gap-[3px]"><strong className="truncate text-[13px] text-ink">{item.filename}</strong><small className="text-[11px] text-ink-faint">版本 {item.acl_version} · {item.sensitivity} · Allow {item.allow_user_ids.length} · Deny {item.deny_user_ids.length}</small></span><Button variant="ghost" size="sm" onClick={() => openAcl({ kind: "document", id: item.document_id, name: item.filename, version: item.acl_version, allow: item.allow_user_ids, deny: item.deny_user_ids })}>配置</Button></div>)}</div> : <p className="text-md text-[#737c90] leading-[1.6]">当前知识库没有文档。</p>}</> : <p className="text-md text-[#737c90] leading-[1.6]">你拥有该知识库的使用权限；ACL 策略仅管理员可见。</p>}</section> : null}
      {activeTab === "conversations" ? <section>{conversations.length ? <div className="grid">{conversations.map((item) => <ListItemButton className="flex items-center justify-between gap-3.5 border-t border-divider bg-surface px-1 py-3.5 text-[#293148] hover:bg-canvas" key={item.conversation_id} onClick={() => onOpen(`/chat/${item.conversation_id}?knowledge_base_id=${id}`)}><span className="grid min-w-0 gap-1"><b className="text-[14px]">{item.title}</b><small className="text-[13px] text-[#7f879a]">{new Date(item.updated_at).toLocaleString("zh-CN")}</small></span><em className="shrink-0 text-[13px] not-italic text-[#7f879a]">{item.turn_count} 轮</em></ListItemButton>)}</div> : <p className="text-md text-[#737c90] leading-[1.6]">还没有会话。</p>}</section> : null}
    </Tabs>
  </> : null}{deletingCategory ? <Dialog open title="删除分类" onClose={() => { if (!busy) setDeletingCategory(null); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<div className="p-[20px_22px] text-[#626b7f] text-[14px] leading-[1.7]">{deletingCategory.document_count > 0 ? <>「{deletingCategory.name}」下还有 <strong className="text-[#242c40]">{deletingCategory.document_count} 份资料</strong>。<p>删除分类<strong className="text-[#242c40]">不会删除资料</strong>，它们会变成「无分类」，仍然可以被检索，之后可以重新分类。</p></> : <>确认删除分类「{deletingCategory.name}」吗？</>}</div><DialogActions><Button variant="secondary" loading={busy} onClick={() => setDeletingCategory(null)}>取消</Button><Button variant="destructive" loading={busy} onClick={() => void deleteCategory(deletingCategory)}>仍要删除</Button></DialogActions></Dialog> : null}{categoryForm ? <Dialog open title={categoryForm.mode === "create" ? "新建分类" : "编辑分类"} description={categoryForm.mode === "create" ? "分类可随时改名、停用或删除" : "修改后立即用于资料筛选"} onClose={() => { if (!busy) setCategoryForm(null); }}><form className="grid gap-3.5" onSubmit={(event) => { event.preventDefault(); void saveCategory(); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<label className="grid gap-[7px] text-[12px] text-ink-muted">名称<Input className="min-h-[40px]" value={categoryDraft.name} maxLength={64} autoFocus onChange={(event) => { setCategoryDraft((current) => ({ ...current, name: event.target.value })); setError(""); }}/></label><label className="grid gap-[7px] text-[12px] text-ink-muted">描述<textarea value={categoryDraft.description} maxLength={300} rows={3} onChange={(event) => setCategoryDraft((current) => ({ ...current, description: event.target.value }))}/></label><label className="grid gap-[7px] text-[12px] text-ink-muted">排序<Input className="min-h-[40px]" type="number" min={0} max={10000} value={categoryDraft.sort_order} onChange={(event) => setCategoryDraft((current) => ({ ...current, sort_order: Number(event.target.value) }))}/></label><DialogActions><Button variant="secondary" loading={busy} onClick={() => setCategoryForm(null)}>取消</Button><Button type="submit" loading={busy}>{categoryForm.mode === "create" ? "创建" : "保存"}</Button></DialogActions></form></Dialog> : null}{aclTarget ? <Dialog open size="md" title="配置 ACL" description={`${aclTarget.name} · 当前版本 ${aclTarget.version}`} onClose={() => { if (!savingAcl) setAclTarget(null); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<div className="grid max-h-[360px] overflow-y-auto border-t border-line">{members.length ? members.map((member) => <label className="flex min-h-14 items-center justify-between gap-4 border-b border-divider" key={member.user_id}><span className="grid gap-0.5"><strong>{member.display_name}</strong><small className="text-sm text-ink-faint">{member.username}</small></span><Select size="sm" className="w-28" aria-label={`${member.display_name} ACL`} value={aclDraft[member.user_id] || "inherit"} onChange={(event) => setAclDraft((current) => ({ ...current, [member.user_id]: event.target.value as "inherit" | "allow" | "deny" }))}><option value="inherit">继承</option><option value="allow">Allow</option><option value="deny">Deny</option></Select></label>) : <p className="text-md text-[#737c90] leading-[1.6]">知识库尚未授权成员，无需配置细粒度 ACL。</p>}</div><DialogActions><Button variant="secondary" loading={savingAcl} onClick={() => setAclTarget(null)}>取消</Button><Button loading={savingAcl} blockedReason={members.length ? undefined : "知识库尚未授权成员"} onClick={() => void saveAcl()}>保存并立即生效</Button></DialogActions></Dialog> : null}</section>;
}
