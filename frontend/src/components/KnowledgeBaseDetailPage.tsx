import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ConversationSummary, DataSource, DocumentCategory, DocumentIndexState, DocumentInfo, DocumentVersion, EvaluationReportSummary, GovernedOperation, IndexBuild, IndexVersion, IndexVersionCandidatePreview, IndexVersionComparison, IndexVersionCreationContext, KnowledgeBase, LifecycleEvent, User, ValidationReport } from "../types";
import { DocumentPanel } from "./DocumentPanel";
import { Dialog, DialogActions } from "./ui/Dialog";
import { PipelineStepper } from "./ui/PipelineStepper";
import { Button } from "./ui/Button";
import { Badge } from "./ui/Badge";
import { type Column, DataTable } from "./ui/DataTable";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input } from "./ui/Input";
import { type RowAction, RowActions } from "./ui/RowActions";
import { Select } from "./ui/Select";
import { Tabs, type TabItem } from "./ui/Tabs";
import { Toolbar } from "./ui/Toolbar";
import { KnowledgeBaseDataSourcesPanel } from "./KnowledgeBaseDataSourcesPanel";
import { IndexVersionCreationWizard } from "./IndexVersionCreationWizard";
import { IndexVersionDetailDialog } from "./IndexVersionDetailDialog";

const STATUS = { empty: "空库", processing: "处理中", ready: "可用", degraded: "部分异常", failed: "失败" } as const;
// 与 KnowledgeBasesPage 的 STATUS_TONE 同一套约定：同一个 index_status 取值域，
// 在两处渲染成不同颜色才是真正的不一致——见 CLAUDE.md 第二条。
const STATUS_TONE = { empty: "neutral", processing: "brand", ready: "success", degraded: "warning", failed: "danger" } as const;
const VERSION_STATUS = { pending: "等待索引", indexing: "索引中", ready: "可用", failed: "失败", superseded: "历史版本" } as const;
const GOVERNANCE_STATUS: Record<string, string> = { queued: "等待处理", preparing: "准备中", running: "处理中", building: "构建中", validating: "验证中", ready: "待激活", activating: "激活中", active: "当前生效", previous: "上一版本", retired: "已退役", cleaned: "已清理", build_failed: "构建失败", validation_failed: "验证失败", succeeded: "已完成", partial_failed: "部分失败", failed: "失败", cancel_requested: "正在取消", cancelled: "已取消", aborted: "已中止" };
const INDEX_CREATION_REASON: Record<string, string> = {
  legacy: "历史迁移", initial_build: "创建首个索引版本", config_changed: "配置已变更",
  document_snapshot_changed: "文档集合已变化", component_upgraded: "索引组件已升级",
  consistency_repair: "索引一致性修复", manual_rebuild: "主动创建回滚版本",
};
const OPERATION_TYPE_LABEL: Record<string, string> = { index_build: "索引构建", sync_run: "数据同步", file_upload: "文件上传", file_update: "文件更新", document_reprocess: "资料重新处理", index_validation: "索引验证", index_activation: "索引激活" };
const OPERATION_STAGE_LABEL: Record<string, string> = { queued: "等待处理", discover: "发现资源", fetch: "获取内容", normalize: "内容规范化", parse: "解析资料", parsing: "解析资料", chunk: "资料切片", chunking: "资料切片", enrich: "补充元数据与权限", vector: "构建向量索引", keyword: "构建关键词索引", metadata: "构建元数据索引", build: "构建索引", validating: "验证索引", validate: "验证索引", activate: "激活版本", retry: "正在重试", retry_wait: "等待重试", complete: "已完成", completed: "已完成", cancelled: "已取消", failed: "失败" };
const operationStage = (item: GovernedOperation) => item.current_stage === "failed" && item.error_message?.includes("没有可索引的文本") ? "parsing" : item.current_stage;
const CONFIG_FIELD_LABEL: Record<string, string> = {
  chunking_version: "切片策略", embedding_model: "向量模型",
  embedding_dimension: "向量维度", processing_options: "切片参数",
  parser_schema_version: "解析器版本",
  vector_index_schema_version: "Vector 索引结构", keyword_index_schema_version: "Keyword 索引结构",
  metadata_schema_version: "Metadata 结构", acl_schema_version: "ACL 结构",
  citation_schema_version: "Citation 结构", reranker_model: "Reranker 模型",
};
const INDEX_LANE_STATUS_LABEL: Record<string, string> = { pending: "等待处理", queued: "等待处理", building: "构建中", ready: "可用", succeeded: "已完成", failed: "失败", cancelled: "已取消" };
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

const INDEX_VERSION_COLUMNS: Column<IndexVersion>[] = [
  {
    key: "version",
    header: "版本",
    width: "24%",
    render: (item) => <span className="grid gap-0.5"><strong className="font-medium text-ink">{item.version_no ? `v${item.version_no}` : "Legacy"}</strong><small className="truncate text-ink-faint" title={item.index_version_id}>{item.index_version_id}</small><small className="truncate text-ink-faint">{INDEX_CREATION_REASON[item.creation_reason] || item.creation_reason}</small></span>,
  },
  {
    key: "status",
    header: "状态",
    width: "14%",
    render: (item) => (
      <Badge shape="status" tone={item.status === "build_failed" || item.status === "validation_failed" ? "danger" : item.status === "building" || item.status === "validating" ? "brand" : item.status === "cleaned" || item.status === "retired" ? "neutral" : "success"}>
        {GOVERNANCE_STATUS[item.status] || item.status}
      </Badge>
    ),
  },
  {
    key: "configuration",
    header: "索引配置",
    width: "34%",
    render: (item) => {
      const legacy = item.config_completeness === "unknown" || (item.parser_version === "legacy" && item.chunking_version === "legacy" && item.embedding_model === "legacy");
      const value = legacy
        ? "历史索引配置不完整，缺少组件版本快照，不能作为新门禁的可复现配置"
        : `${item.parser_version} · ${item.chunking_version} · ${item.embedding_model} · ${item.embedding_dimension} 维`;
      return <span className="grid gap-0.5" title={value}><span className="truncate">{legacy ? "历史索引配置" : value}</span>{legacy ? <small className="truncate text-warning">组件清单未知 · 仅保留历史</small> : null}</span>;
    },
  },
  {
    key: "created",
    header: "创建时间",
    width: "18%",
    render: (item) => <span className="whitespace-nowrap">{new Date(item.created_at).toLocaleString("zh-CN")}</span>,
  },
];

export function KnowledgeBaseDetailPage({ id, onOpen }: { id: string; onOpen: (path: string) => void }) {
  const requestedTab = new URLSearchParams(window.location.search).get("tab");
  const [activeTab, setActiveTab] = useState<"documents" | "data_sources" | "categories" | "versions" | "members" | "conversations">(requestedTab === "data_sources" ? "data_sources" : "documents");
  const [base, setBase] = useState<KnowledgeBase | null>(null); const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [versions, setVersions] = useState<DocumentVersion[]>([]); const [members, setMembers] = useState<User[]>([]);
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [indexVersions, setIndexVersions] = useState<IndexVersion[]>([]);
  const [operations, setOperations] = useState<GovernedOperation[]>([]);
  const [indexBuilds, setIndexBuilds] = useState<IndexBuild[]>([]);
  const [buildDocuments, setBuildDocuments] = useState<DocumentIndexState[]>([]);
  const [selectedBuild, setSelectedBuild] = useState<IndexBuild | null>(null);
  const [versionDetail, setVersionDetail] = useState<IndexVersion | null>(null);
  const [versionReports, setVersionReports] = useState<ValidationReport[]>([]);
  const [versionEvents, setVersionEvents] = useState<LifecycleEvent[]>([]);
  const [operationDetail, setOperationDetail] = useState<GovernedOperation | null>(null);
  const [activationTarget, setActivationTarget] = useState<IndexVersion | null>(null);
  const [validationTarget, setValidationTarget] = useState<IndexVersion | null>(null);
  const [cleanupTarget, setCleanupTarget] = useState<IndexVersion | null>(null);
  const [cancelBuildTarget, setCancelBuildTarget] = useState<IndexVersion | null>(null);
  const [retireTarget, setRetireTarget] = useState<IndexVersion | null>(null);
  const [rollbackTarget, setRollbackTarget] = useState<IndexVersion | null>(null);
  const [versionComparison, setVersionComparison] = useState<IndexVersionComparison | null>(null);
  const [creationContext, setCreationContext] = useState<IndexVersionCreationContext | null>(null);
  const [creationContextLoading, setCreationContextLoading] = useState(false);
  const [creationIdempotencyKey, setCreationIdempotencyKey] = useState("");
  const [reportId, setReportId] = useState("");
  const [evaluationReports, setEvaluationReports] = useState<EvaluationReportSummary[]>([]);
  const [buildDetailLoading, setBuildDetailLoading] = useState(false);
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
  const [taskTypeFilter, setTaskTypeFilter] = useState("");
  const [taskStatusFilter, setTaskStatusFilter] = useState("");
  const openCreationWizard = async () => {
    setCreationContextLoading(true); setError("");
    try {
      const context = await api.getIndexVersionCreationContext(id);
      setCreationIdempotencyKey(globalThis.crypto?.randomUUID?.() || `index-version-${Date.now()}`);
      setCreationContext(context);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取索引定义与文档范围。");
    } finally { setCreationContextLoading(false); }
  };
  const previewIndexVersion = (payload: Parameters<typeof api.previewIndexVersion>[1]) => api.previewIndexVersion(id, payload);
  const createIndexVersion = async (preview: IndexVersionCandidatePreview) => {
    setBusy(true); setError("");
    try {
      await api.createIndexVersion(id, preview, creationIdempotencyKey);
      setCreationContext(null);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "索引版本创建失败。");
      throw reason;
    } finally { setBusy(false); }
  };
  const openRollback = async (item: IndexVersion) => {
    setRollbackTarget(item); setVersionComparison(null); setError("");
    try { setVersionComparison(await api.compareIndexVersion(id, item.index_version_id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取回滚差异。"); }
  };
  const openVersionDetail = useCallback(async (item: IndexVersion) => {
    setVersionDetail(item);
    // 报告拉取失败不该挡住详情本身——配置与指纹是本地已有的数据，仍然该看得到。
    try {
      const [reports, events] = await Promise.all([
        api.listIndexVersionValidations(id, item.index_version_id),
        api.listIndexVersionEvents(id, item.index_version_id),
      ]);
      setVersionReports(reports);
      setVersionEvents(events);
    } catch (reason) {
      // 静默吞掉会让「治理数据拉取失败」看起来像「这个版本没有治理数据」——两者
      // 在页面上长得一模一样，而含义相反。配置与指纹是本地已有的，详情本身仍要能看。
      setVersionReports([]);
      setVersionEvents([]);
      console.error("索引版本治理数据拉取失败", reason);
    }
  }, [id]);

  const load = useCallback(async () => { const detail = await api.getKnowledgeBase(id); const admin = detail.current_user_permission === "admin"; const [docs, history, versionItems, indexVersionItems, buildItems, operationItems, memberItems, sourceItems, categoryItems, reports] = await Promise.all([api.listKnowledgeBaseDocuments(id), api.listConversations(id), api.listKnowledgeBaseDocumentVersions(id), admin ? api.listKnowledgeBaseIndexVersions(id) : Promise.resolve([]), admin ? api.listKnowledgeBaseIndexBuilds(id) : Promise.resolve([]), admin ? api.listKnowledgeBaseOperations(id) : Promise.resolve([]), admin ? api.listKnowledgeBaseMembers(id) : Promise.resolve([]), admin ? api.listDataSources(0, 100) : Promise.resolve([]), api.listKnowledgeBaseCategories(id), admin ? api.listEvaluations() : Promise.resolve([])]); setBase(detail); setDocuments(docs); setConversations(history); setVersions(versionItems); setIndexVersions(indexVersionItems); setIndexBuilds(buildItems); setOperations(operationItems); setMembers(memberItems); setDataSources(sourceItems.filter((item) => item.knowledge_base_id === id)); setCategories(categoryItems); setEvaluationReports(reports); }, [id]);
  useEffect(() => { Promise.resolve().then(load).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取知识库。")); }, [load]);
  useEffect(() => {
    const activeBuild = indexBuilds.some((item) => ["queued", "building"].includes(item.status));
    const activeOperation = operations.some((item) => ["queued", "running"].includes(item.status));
    if (!activeBuild && !activeOperation) return;
    const timer = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(timer);
  }, [indexBuilds, operations, load]);
  // 失败文件名收集齐后 throw 出去，交给 DocumentPanel 的 toast 展示——它是持续显示
  // 的错误提示（见 ui/Toast.tsx），完整文件名列表已经在消息里，页面横幅只会重复。
  // load() 必须在 throw 之前跑完：批量上传里已经成功的那些，不能因为个别失败就不刷新出来。
  const upload = async (files: File[]) => { setBusy(true); setError(""); setUploadProgress({ completed: 0, total: files.length }); const failed: string[] = []; try { for (const [index, file] of files.entries()) { try { await api.uploadKnowledgeBaseDocument(id, file); } catch { failed.push(file.name); } finally { setUploadProgress({ completed: index + 1, total: files.length }); } } await load(); if (failed.length) throw new Error(`以下文件上传失败：${failed.join("、")}`); } finally { setBusy(false); setUploadProgress(null); } };
  const updateFile = async (_document: DocumentInfo, file: File) => { await api.uploadKnowledgeBaseDocument(id, file); await load(); };
  const retryProcessing = async (documentVersionId: string, chunkSize: number, chunkOverlap: number) => { await api.reprocessDocumentVersion(id, documentVersionId, chunkSize, chunkOverlap); await load(); };
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
  const documentVersionColumns: Column<DocumentVersion>[] = [
    { key: "document", header: "资料", width: "28%", truncate: false, render: (item) => <span className="flex min-w-0 items-center gap-2"><strong className="truncate font-medium text-ink">{item.filename}</strong><Badge shape="type" tone="neutral" className="shrink-0">V{item.version_number}</Badge>{item.is_current ? <Badge shape="status" tone="success" className="shrink-0">当前版本</Badge> : null}</span> },
    { key: "file", header: "文件", width: "12%", render: (item) => formatBytes(item.source_file_bytes) },
    { key: "parser", header: "解析与切片", width: "25%", render: (item) => `${item.parser_name || "旧版解析"} ${item.parser_version || "legacy"} · ${item.chunking_version || "旧版切片"}` },
    { key: "chunks", header: "结构", width: "13%", render: (item) => `${item.node_count} 节点 / ${item.parsed_chunk_count} Chunk` },
    { key: "created", header: "创建时间", width: "14%", render: (item) => new Date(item.created_at).toLocaleString("zh-CN") },
    { key: "status", header: "状态", render: (item) => { const badge = versionRowBadge(item); return <span className="grid gap-1"><Badge shape="status" tone={badge.tone}>{badge.label}</Badge>{item.failure_reason ? <small className="truncate text-danger-text" title={item.failure_reason}>{item.failure_reason}</small> : null}</span>; } },
  ];
  const dataSourceAclColumns: Column<DataSource>[] = [
    { key: "name", header: "数据源", width: "32%", render: (item) => <strong className="font-medium text-ink">{item.name}</strong> },
    { key: "version", header: "ACL 版本", width: "14%", numeric: true, render: (item) => item.acl_version },
    { key: "allow", header: "Allow", width: "14%", numeric: true, render: (item) => item.allow_user_ids.length },
    { key: "deny", header: "Deny", width: "14%", numeric: true, render: (item) => item.deny_user_ids.length },
    { key: "actions", header: "操作", width: "26%", align: "right", truncate: false, render: (item) => <Button variant="ghost" size="sm" onClick={() => openAcl({ kind: "source", id: item.data_source_id, name: item.name, version: item.acl_version, allow: item.allow_user_ids, deny: item.deny_user_ids })}>配置</Button> },
  ];
  const documentAclColumns: Column<DocumentInfo>[] = [
    { key: "name", header: "资料", width: "30%", render: (item) => <strong className="font-medium text-ink">{item.filename}</strong> },
    { key: "sensitivity", header: "敏感级别", width: "14%", render: (item) => item.sensitivity },
    { key: "version", header: "ACL 版本", width: "12%", numeric: true, render: (item) => item.acl_version },
    { key: "allow", header: "Allow", width: "10%", numeric: true, render: (item) => item.allow_user_ids.length },
    { key: "deny", header: "Deny", width: "10%", numeric: true, render: (item) => item.deny_user_ids.length },
    { key: "actions", header: "操作", width: "24%", align: "right", truncate: false, render: (item) => <Button variant="ghost" size="sm" onClick={() => openAcl({ kind: "document", id: item.document_id, name: item.filename, version: item.acl_version, allow: item.allow_user_ids, deny: item.deny_user_ids })}>配置</Button> },
  ];
  const conversationColumns: Column<ConversationSummary>[] = [
    { key: "title", header: "会话", width: "52%", truncate: false, render: (item) => <button type="button" className="max-w-full truncate border-0 bg-transparent p-0 text-left font-medium text-brand hover:underline" onClick={() => onOpen(`/chat/${item.conversation_id}?knowledge_base_id=${id}`)}>{item.title}</button> },
    { key: "turns", header: "轮次", width: "16%", numeric: true, render: (item) => `${item.turn_count} 轮` },
    { key: "updated", header: "更新时间", width: "32%", render: (item) => new Date(item.updated_at).toLocaleString("zh-CN") },
  ];
  const openIndexBuild = async (item: IndexBuild) => {
    setSelectedBuild(item); setBuildDocuments([]); setBuildDetailLoading(true);
    try { setBuildDocuments(await api.listIndexBuildDocuments(id, item.index_build_id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "构建详情读取失败。"); }
    finally { setBuildDetailLoading(false); }
  };
  const governedIndexVersionColumns: Column<IndexVersion>[] = [
    ...INDEX_VERSION_COLUMNS,
    { key: "actions", header: "操作", width: "16%", align: "right", truncate: false, render: (item) => <RowActions rowLabel={item.index_version_id} actions={[
      { label: "详情", onSelect: () => void openVersionDetail(item) },
      ...(item.status === "building" ? [{ label: "取消构建", tone: "destructive" as const, onSelect: () => { setCancelBuildTarget(item); setError(""); } } as RowAction] : []),
      ...(["validating", "validation_failed"].includes(item.status) ? [{ label: item.status === "validation_failed" ? "重新验证" : "执行验证", onSelect: () => { setValidationTarget(item); setReportId(""); setError(""); } } as RowAction] : []),
      ...(["build_failed", "validating", "validation_failed"].includes(item.status) ? [{ label: "重新构建", onSelect: async () => { setBusy(true); setError(""); try { await api.retryIndexVersionBuild(id, item.index_version_id); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "索引重新构建失败。"); } finally { setBusy(false); } } } as RowAction] : []),
      ...(item.status === "ready" ? [{ label: "激活", onSelect: () => { setActivationTarget(item); setError(""); } } as RowAction] : []),
      ...(item.status === "previous" ? [{ label: "回滚", onSelect: () => void openRollback(item) } as RowAction, { label: "退役", tone: "destructive" as const, onSelect: () => { setRetireTarget(item); setError(""); } } as RowAction] : []),
      ...(["retired", "build_failed", "validation_failed"].includes(item.status) ? [{ label: "清理", tone: "destructive" as const, onSelect: () => { setCleanupTarget(item); setError(""); } } as RowAction] : []),
    ]}/> },
  ];
  // 弹层打开时错误只显示在弹层内：Radix 给背景内容加了 aria-hidden，
  // 顶部横幅在弹层背后，既看不见也不会被屏幕阅读器读到。
  const dialogOpen = Boolean(categoryForm || deletingCategory || aclTarget || activationTarget || validationTarget || cleanupTarget || cancelBuildTarget || retireTarget || rollbackTarget || creationContext || versionDetail || operationDetail);
  const compatibleEvaluationReports = validationTarget
    ? evaluationReports.filter((report) =>
        report.passed && report.config_fingerprint === validationTarget.config_fingerprint)
    : [];

  const fileSourceIds = new Set(dataSources.filter((item) => item.source_type === "file").map((item) => item.data_source_id));
  const uploadedDocuments = documents.filter((item) => !item.data_source_id || fileSourceIds.has(item.data_source_id) || item.source_type === "upload");
  const externalDataSources = dataSources.filter((item) => item.source_type !== "file");
  const taskRecords = operations.filter((item) =>
    (!taskTypeFilter || item.operation_type === taskTypeFilter)
    && (!taskStatusFilter || item.status === taskStatusFilter));

  const tabs: TabItem[] = [
    { value: "documents", label: "资料", count: uploadedDocuments.length },
    ...(base?.current_user_permission === "admin" ? [{ value: "data_sources", label: "数据源", count: externalDataSources.length }] : []),
    { value: "categories", label: "分类管理", count: categories.length },
    { value: "versions", label: "版本治理", count: indexVersions.length },
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
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge shape="status" tone={STATUS_TONE[base.index_status]} className="w-fit">{STATUS[base.index_status]}</Badge>
          {base.index_config_drift.length ? <Badge shape="status" tone="warning" className="w-fit">配置已变更</Badge> : null}
        </div>
        {base.index_config_drift.length ? <small className="text-[10px] leading-[1.5] text-warning">当前索引建于旧配置：{base.index_config_drift.map((item) => CONFIG_FIELD_LABEL[item.field] || item.field).join("、")}已变更，重建后生效</small> : null}
      </div>
      <div className="grid min-h-16 min-w-0 content-center gap-[5px] px-3 py-[9px] max-md:even:border-r-0">
        <span className="text-[10px] text-[#8b92a4]">更新时间</span>
        <strong className="truncate text-[12px] text-[#2d3549]">{new Date(base.updated_at).toLocaleString("zh-CN")}</strong>
      </div>
    </section>
    <Tabs items={tabs} value={activeTab} onChange={(value) => setActiveTab(value as typeof activeTab)} label="知识库详情">
      {activeTab === "documents" ? <DocumentPanel knowledgeBaseId={id} documents={uploadedDocuments} versions={versions} categories={categories} operations={operations} loading={busy} uploadProgress={uploadProgress} onUpload={upload} onUpdateFile={updateFile} onDelete={remove} onUpdateMetadata={updateMetadata} onBatchCategory={batchCategory} onReclassify={reclassify} onRetryProcessing={retryProcessing} canManage={base.current_user_permission === "admin"}/> : null}
      {activeTab === "data_sources" && base.current_user_permission === "admin" ? <KnowledgeBaseDataSourcesPanel knowledgeBaseId={id} items={externalDataSources} categories={categories} onRefresh={load}/> : null}
      {activeTab === "categories" ? <section className="grid gap-3">
        {templateCategories.length ? <section aria-label="默认模板分类" className="flex flex-wrap items-center gap-2 border-b border-divider pb-3">
          <span className="mr-1 text-sm font-medium text-ink-muted">默认模板分类：</span>｜
          {templateCategories.map((item) => <span key={item.category_id} className="max-w-44 truncate" title={`${item.name} · 创建知识库时复制的初始分类`}><Badge shape="type" tone="neutral">{item.name}</Badge>｜</span>)}
          <small className="text-sm text-ink-faint">只读 · 创建知识库时复制</small>
        </section> : null}
        <div className="flex flex-wrap items-center justify-between gap-2"><p className="m-0 text-sm text-ink-faint">以下是本知识库独立维护的分类，不会同步到默认模板。</p><Button size="sm" loading={busy} onClick={openCategoryCreator}>＋ 新建分类</Button></div>
        <DataTable rows={managedCategories} columns={categoryColumns} rowKey={(item) => item.category_id} label="分类管理列表" emptyState={{ kind: "empty", title: "暂无知识库独立分类", description: "新建分类后，可用于当前知识库的资料归类和问答筛选。" }}/>
      </section> : null}
      {activeTab === "versions" ? <section className="grid gap-3">
        <div className="mt-[18px] flex flex-wrap items-center justify-between gap-3"><div><h3 className="m-0 text-[16px] font-bold text-ink">索引版本</h3><p className="m-0 mt-1 text-sm text-ink-faint">Definition → Version → Build → Validate → Activate；数据同步任务在下方独立展示。</p></div>{base.current_user_permission === "admin" ? <Button size="sm" loading={busy || creationContextLoading} onClick={() => void openCreationWizard()}>创建索引版本</Button> : null}</div><DataTable label="索引版本" rows={indexVersions} rowKey={(item) => item.index_version_id} columns={governedIndexVersionColumns} emptyState={{ kind: "empty", title: "还没有索引版本", description: "创建首个索引版本后，将按快照构建、验证并等待激活。" }}/>
        {creationContext ? <IndexVersionCreationWizard open context={creationContext} busy={busy} onClose={() => { if (!busy) setCreationContext(null); }} onPreview={previewIndexVersion} onCreate={createIndexVersion}/> : null}
        {cancelBuildTarget ? <Dialog open title="取消索引构建" description={cancelBuildTarget.index_version_id} onClose={() => { setCancelBuildTarget(null); setError(""); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<p className="text-sm text-ink-muted">将停止尚未完成的文档任务，并把 Version 标记为 build_failed。已完成的候选分块不会上线，之后可选择重新构建或清理。</p><DialogActions><Button variant="secondary" loading={busy} onClick={() => setCancelBuildTarget(null)}>继续构建</Button><Button variant="destructive" loading={busy} onClick={async () => { setBusy(true); setError(""); try { await api.cancelIndexVersionBuild(id, cancelBuildTarget.index_version_id); setCancelBuildTarget(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "取消索引构建失败。"); } finally { setBusy(false); } }}>确认取消</Button></DialogActions></Dialog> : null}
        {validationTarget ? <Dialog open title={validationTarget.status === "validation_failed" ? "重新验证索引版本" : "验证索引版本"} description={validationTarget.index_version_id} onClose={() => { setValidationTarget(null); setError(""); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<label className="grid gap-2 text-sm text-ink-muted">正式质量报告<Select value={reportId} onChange={(event) => setReportId(event.target.value)}><option value="">{compatibleEvaluationReports.length ? "选择配置指纹匹配的已通过报告" : "暂无配置指纹匹配的已通过报告"}</option>{compatibleEvaluationReports.map((report) => <option key={report.report_id} value={report.report_id}>{report.report_id} · 已通过 · 指纹匹配</option>)}</Select></label>{compatibleEvaluationReports.length ? null : <p className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">请先使用本版本配置运行正式检索评测。其他版本或旧版无配置指纹的报告不能用于放行。</p>}<p className="text-sm text-ink-faint">本次只执行完整性、技术与检索质量三层门禁；通过后状态变为 ready（待激活），不会自动切换线上版本。</p><DialogActions><Button variant="secondary" loading={busy} onClick={() => setValidationTarget(null)}>取消</Button><Button loading={busy} blockedReason={reportId.trim() ? undefined : "请选择与本版本配置匹配的质量报告"} onClick={async () => { setBusy(true); setError(""); try { await api.createIndexVersionValidation(id, validationTarget.index_version_id, reportId.trim()); setValidationTarget(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "索引验证失败。"); } finally { setBusy(false); } }}>执行三层验证</Button></DialogActions></Dialog> : null}
        {activationTarget ? <Dialog open title="激活索引版本" description={activationTarget.index_version_id} onClose={() => { setActivationTarget(null); setError(""); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<p className="text-sm text-ink-muted">该版本已通过三层门禁，激活只执行原子指针切换。当前 active 将变为 previous，不会重新运行验证。</p><p className="break-all text-sm text-ink-faint">绑定验证报告：{activationTarget.validation_report_id || "缺失"}</p><DialogActions><Button variant="secondary" loading={busy} onClick={() => setActivationTarget(null)}>取消</Button><Button loading={busy} onClick={async () => { setBusy(true); setError(""); try { await api.activateIndexVersion(id, activationTarget.index_version_id); setActivationTarget(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "索引激活失败。"); } finally { setBusy(false); } }}>确认激活</Button></DialogActions></Dialog> : null}
        {rollbackTarget ? <Dialog open size="md" title="回滚上一索引版本" description={rollbackTarget.index_version_id} onClose={() => { setRollbackTarget(null); setVersionComparison(null); setError(""); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}{versionComparison ? <><dl className="grid grid-cols-3 gap-3 text-sm max-sm:grid-cols-1"><div><dt className="text-ink-faint">目标物理范围</dt><dd className="m-0 mt-1">{versionComparison.actual_scope.documents} 份 / {versionComparison.actual_scope.chunks} Chunks</dd></div><div><dt className="text-ink-faint">配置差异</dt><dd className="m-0 mt-1">{versionComparison.config_diff.length} 项</dd></div><div><dt className="text-ink-faint">文档快照差异</dt><dd className="m-0 mt-1">+{versionComparison.document_diff.added} / -{versionComparison.document_diff.removed} / 更新 {versionComparison.document_diff.updated}</dd></div></dl><p className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">{versionComparison.content_snapshot_note}</p>{versionComparison.config_diff.length ? <ul className="m-0 grid max-h-36 gap-1 overflow-y-auto p-0 text-sm">{versionComparison.config_diff.map((item) => <li key={item.field} className="list-none rounded border border-divider p-2"><strong>{CONFIG_FIELD_LABEL[item.field] || item.field}</strong><span className="ml-2 break-all text-ink-faint">{JSON.stringify(item.baseline) || "—"} → {JSON.stringify(item.target) || "—"}</span></li>)}</ul> : <p className="text-sm text-ink-faint">与当前 active 配置一致。</p>}<dl className="grid grid-cols-2 gap-3 text-sm max-sm:grid-cols-1"><div><dt className="text-ink-faint">目标验证报告</dt><dd className="m-0 mt-1">{String(versionComparison.validation_comparison.target?.status || "无正式报告")}</dd></div><div><dt className="text-ink-faint">当前 active 验证报告</dt><dd className="m-0 mt-1">{String(versionComparison.validation_comparison.baseline?.status || "无正式报告")}</dd></div><div><dt className="text-ink-faint">回滚后可检索资料</dt><dd className="m-0 mt-1">{versionComparison.current_content.retrievable_documents} 份 / {versionComparison.current_content.retrievable_chunks} Chunks</dd></div><div><dt className="text-ink-faint">当前内容差异</dt><dd className="m-0 mt-1">+{versionComparison.current_content.diff.added} / -{versionComparison.current_content.diff.removed} / 更新 {versionComparison.current_content.diff.updated}</dd></div></dl></> : <p className="text-sm text-ink-faint">正在读取版本差异…</p>}<DialogActions><Button variant="secondary" loading={busy} onClick={() => setRollbackTarget(null)}>取消</Button><Button loading={busy} blockedReason={versionComparison ? undefined : "版本差异尚未读取完成"} onClick={async () => { setBusy(true); setError(""); try { await api.rollbackIndexVersion(id, Boolean(versionComparison?.current_content.requires_confirmation)); setRollbackTarget(null); setVersionComparison(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "索引回滚失败。"); } finally { setBusy(false); } }}>确认回滚</Button></DialogActions></Dialog> : null}
        {retireTarget ? <Dialog open title="退役上一索引版本" description={retireTarget.index_version_id} onClose={() => { setRetireTarget(null); setError(""); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<p className="text-sm text-ink-muted">退役后将失去一键回滚到该版本的能力，但物理索引仍保留；需要另行执行 Cleanup 才会删除。</p><DialogActions><Button variant="secondary" loading={busy} onClick={() => setRetireTarget(null)}>取消</Button><Button variant="destructive" loading={busy} onClick={async () => { setBusy(true); setError(""); try { await api.retireIndexVersion(id, retireTarget.index_version_id); setRetireTarget(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "索引退役失败。"); } finally { setBusy(false); } }}>确认退役</Button></DialogActions></Dialog> : null}
        {cleanupTarget ? <Dialog open title="清理索引物理内容" description={cleanupTarget.index_version_id} onClose={() => { setCleanupTarget(null); setError(""); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<p className="text-sm text-ink-muted">将删除该版本全部 Chunks 与专属 HNSW 索引。版本记录和生命周期事件会保留，状态变为 cleaned，且不能再激活或回滚。</p><DialogActions><Button variant="secondary" loading={busy} onClick={() => setCleanupTarget(null)}>取消</Button><Button variant="destructive" loading={busy} onClick={async () => { setBusy(true); setError(""); try { await api.cleanupIndexVersion(id, cleanupTarget.index_version_id); setCleanupTarget(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "索引清理失败。"); } finally { setBusy(false); } }}>确认清理</Button></DialogActions></Dialog> : null}
        <h3 className="mt-[18px] mb-0 text-[16px] font-bold text-ink">资料版本</h3><DataTable label="资料版本" rows={versions} rowKey={(item) => item.document_version_id} columns={documentVersionColumns} emptyState={{ kind: "empty", title: "还没有资料版本", description: "上传并解析资料后，这里会显示资料版本。" }}/>
        <h3 className="mt-[18px] mb-0 text-[16px] font-bold text-ink">任务记录</h3>
        <Toolbar filters={<><label className="flex items-center gap-2 text-md">任务类型<Select size="sm" className="w-36" aria-label="任务类型筛选" value={taskTypeFilter} onChange={(event) => setTaskTypeFilter(event.target.value)}><option value="">全部类型</option>{[...new Set(operations.map((item) => item.operation_type))].map((value) => <option key={value} value={value}>{OPERATION_TYPE_LABEL[value] || value}</option>)}</Select></label><label className="flex items-center gap-2 text-md">状态<Select size="sm" className="w-32" aria-label="任务状态筛选" value={taskStatusFilter} onChange={(event) => setTaskStatusFilter(event.target.value)}><option value="">全部状态</option>{[...new Set(operations.map((item) => item.status))].map((value) => <option key={value} value={value}>{GOVERNANCE_STATUS[value] || value}</option>)}</Select></label></>}/>
        <DataTable label="任务记录" rows={taskRecords} rowKey={(item) => item.operation_id} columns={[
          { key: "type", header: "任务类型", width: "120px", render: (item) => OPERATION_TYPE_LABEL[item.operation_type] || item.operation_type },
          { key: "stage", header: "当前阶段", width: "110px", render: (item) => OPERATION_STAGE_LABEL[operationStage(item)] || operationStage(item) },
          { key: "progress", header: "进度", width: "520px", truncate: false, render: (item) => <PipelineStepper kind={item.operation_type} currentStage={operationStage(item)} status={item.status} progressPercent={item.progress_percent} label={`${OPERATION_TYPE_LABEL[item.operation_type] || item.operation_type}进度`} failureReason={item.error_message}/> },
          { key: "count", header: "处理数量", width: "90px", render: (item) => `${item.completed_count}/${item.total_count}` },
          { key: "status", header: "状态", width: "100px", render: (item) => <Badge shape="status" tone={item.status === "failed" || item.status === "aborted" ? "danger" : item.status === "succeeded" ? "success" : "brand"}>{GOVERNANCE_STATUS[item.status] || item.status}</Badge> },
          { key: "updated", header: "更新时间", width: "150px", render: (item) => new Date(item.updated_at).toLocaleString("zh-CN") },
          { key: "actions", header: "操作", width: "72px", align: "right", truncate: false, render: (item) => { const build = indexBuilds.find((candidate) => candidate.operation_id === item.operation_id); return <Button variant="ghost" size="sm" onClick={() => { if (build) void openIndexBuild(build); else setOperationDetail(item); }}>详情</Button>; } },
        ]} emptyState={taskTypeFilter || taskStatusFilter ? { kind: "filtered", title: "没有符合条件的任务", description: "调整任务类型或状态筛选后重试。" } : { kind: "empty", title: "暂无任务记录", description: "索引构建、同步或资料更新后保留任务记录。" }}/>
        {selectedBuild ? <section className="grid gap-2 border-t border-divider pt-3"><div className="flex flex-wrap items-center justify-between gap-2"><div><h4 className="m-0 text-sm">索引构建详情 · {selectedBuild.index_build_id}</h4><small className="text-sm text-ink-faint">目标版本 {selectedBuild.index_version_id} · 第 {selectedBuild.attempt_no} 次构建 · {selectedBuild.succeeded_documents}/{selectedBuild.total_documents} 份完成 · {selectedBuild.failed_documents} 份失败{buildDetailLoading ? " · 读取中" : ""}</small></div><Button variant="ghost" size="sm" onClick={() => setSelectedBuild(null)}>收起</Button></div><DataTable label="资料索引状态" rows={buildDocuments} rowKey={(item) => item.document_id} columns={[
          { key: "document", header: "资料", width: "50%", render: (item) => <strong>{item.filename}</strong> },
          { key: "chunks", header: "切片数", width: "10%", numeric: true, render: (item) => item.chunk_count },
          { key: "status", header: "索引状态", width: "40%", render: (item) => <Badge shape="status" tone={item.overall_status === "failed" ? "danger" : item.overall_status === "ready" ? "success" : "brand"}>{INDEX_LANE_STATUS_LABEL[item.overall_status] || item.overall_status}</Badge> },
        ]} emptyState={{ kind: "empty", title: "暂无资料状态", description: "旧构建批次未记录单资料状态。" }}/></section> : null}
        {versionDetail ? <IndexVersionDetailDialog version={versionDetail} reports={versionReports} events={versionEvents} onClose={() => { setVersionDetail(null); setVersionReports([]); setVersionEvents([]); }} /> : null}
        {operationDetail ? <Dialog open size="md" title="运行任务详情" description={OPERATION_TYPE_LABEL[operationDetail.operation_type] || operationDetail.operation_type} onClose={() => setOperationDetail(null)}><dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm max-sm:grid-cols-1"><div><dt className="text-ink-faint">当前阶段</dt><dd className="m-0 mt-1">{OPERATION_STAGE_LABEL[operationDetail.current_stage] || operationDetail.current_stage}</dd></div><div><dt className="text-ink-faint">状态</dt><dd className="m-0 mt-1">{GOVERNANCE_STATUS[operationDetail.status] || operationDetail.status}</dd></div><div><dt className="text-ink-faint">处理数量</dt><dd className="m-0 mt-1">{operationDetail.completed_count}/{operationDetail.total_count}</dd></div><div><dt className="text-ink-faint">失败数量</dt><dd className="m-0 mt-1">{operationDetail.failed_count}</dd></div><div><dt className="text-ink-faint">资料</dt><dd className="m-0 mt-1 break-all">{operationDetail.document_id || "—"}</dd></div><div><dt className="text-ink-faint">数据源</dt><dd className="m-0 mt-1 break-all">{operationDetail.data_source_id || "—"}</dd></div>{operationDetail.error_message ? <div className="col-span-2 max-sm:col-span-1"><dt className="text-ink-faint">失败原因</dt><dd className="m-0 mt-1 text-danger-text">{operationDetail.error_message}</dd></div> : null}</dl><DialogActions><Button variant="secondary" onClick={() => setOperationDetail(null)}>关闭</Button></DialogActions></Dialog> : null}
      </section> : null}
      {activeTab === "members" ? <section className="grid gap-3">{base.current_user_permission === "admin" ? <><p className="m-0 text-[12px] text-ink-faint">Deny 优先；未配置时继承知识库成员权限。ACL 更新后立即影响下一次检索。</p><h3 className="mt-2 mb-0 text-[13px] text-[#151a31]">数据源 ACL</h3><DataTable label="数据源 ACL" rows={dataSources} rowKey={(item) => item.data_source_id} columns={dataSourceAclColumns} emptyState={{ kind: "empty", title: "暂无数据源 ACL", description: "当前知识库没有独立数据源。" }}/><h3 className="mt-2 mb-0 text-[13px] text-[#151a31]">文档 ACL</h3><DataTable label="文档 ACL" rows={documents} rowKey={(item) => item.document_id} columns={documentAclColumns} emptyState={{ kind: "empty", title: "暂无文档 ACL", description: "当前知识库没有资料。" }}/></> : <p className="text-md text-[#737c90] leading-[1.6]">你拥有该知识库的使用权限；ACL 策略仅管理员可见。</p>}</section> : null}
      {activeTab === "conversations" ? <DataTable label="会话列表" rows={conversations} rowKey={(item) => item.conversation_id} columns={conversationColumns} emptyState={{ kind: "empty", title: "还没有会话", description: "在此知识库发起问答后，会话将显示在这里。" }}/> : null}
    </Tabs>
  </> : null}{deletingCategory ? <Dialog open title="删除分类" onClose={() => { if (!busy) setDeletingCategory(null); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<div className="p-[20px_22px] text-[#626b7f] text-[14px] leading-[1.7]">{deletingCategory.document_count > 0 ? <>「{deletingCategory.name}」下还有 <strong className="text-[#242c40]">{deletingCategory.document_count} 份资料</strong>。<p>删除分类<strong className="text-[#242c40]">不会删除资料</strong>，它们会变成「无分类」，仍然可以被检索，之后可以重新分类。</p></> : <>确认删除分类「{deletingCategory.name}」吗？</>}</div><DialogActions><Button variant="secondary" loading={busy} onClick={() => setDeletingCategory(null)}>取消</Button><Button variant="destructive" loading={busy} onClick={() => void deleteCategory(deletingCategory)}>仍要删除</Button></DialogActions></Dialog> : null}{categoryForm ? <Dialog open title={categoryForm.mode === "create" ? "新建分类" : "编辑分类"} description={categoryForm.mode === "create" ? "分类可随时改名、停用或删除" : "修改后立即用于资料筛选"} onClose={() => { if (!busy) setCategoryForm(null); }}><form className="grid gap-3.5" onSubmit={(event) => { event.preventDefault(); void saveCategory(); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<label className="grid gap-[7px] text-[12px] text-ink-muted">名称<Input className="min-h-[40px]" value={categoryDraft.name} maxLength={64} autoFocus onChange={(event) => { setCategoryDraft((current) => ({ ...current, name: event.target.value })); setError(""); }}/></label><label className="grid gap-[7px] text-[12px] text-ink-muted">描述<textarea value={categoryDraft.description} maxLength={300} rows={3} onChange={(event) => setCategoryDraft((current) => ({ ...current, description: event.target.value }))}/></label><label className="grid gap-[7px] text-[12px] text-ink-muted">排序<Input className="min-h-[40px]" type="number" min={0} max={10000} value={categoryDraft.sort_order} onChange={(event) => setCategoryDraft((current) => ({ ...current, sort_order: Number(event.target.value) }))}/></label><DialogActions><Button variant="secondary" loading={busy} onClick={() => setCategoryForm(null)}>取消</Button><Button type="submit" loading={busy}>{categoryForm.mode === "create" ? "创建" : "保存"}</Button></DialogActions></form></Dialog> : null}{aclTarget ? <Dialog open size="md" title="配置 ACL" description={`${aclTarget.name} · 当前版本 ${aclTarget.version}`} onClose={() => { if (!savingAcl) setAclTarget(null); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<div className="grid max-h-[360px] overflow-y-auto border-t border-line">{members.length ? members.map((member) => <label className="flex min-h-14 items-center justify-between gap-4 border-b border-divider" key={member.user_id}><span className="grid gap-0.5"><strong>{member.display_name}</strong><small className="text-sm text-ink-faint">{member.username}</small></span><Select size="sm" className="w-28" aria-label={`${member.display_name} ACL`} value={aclDraft[member.user_id] || "inherit"} onChange={(event) => setAclDraft((current) => ({ ...current, [member.user_id]: event.target.value as "inherit" | "allow" | "deny" }))}><option value="inherit">继承</option><option value="allow">Allow</option><option value="deny">Deny</option></Select></label>) : <p className="text-md text-[#737c90] leading-[1.6]">知识库尚未授权成员，无需配置细粒度 ACL。</p>}</div><DialogActions><Button variant="secondary" loading={savingAcl} onClick={() => setAclTarget(null)}>取消</Button><Button loading={savingAcl} blockedReason={members.length ? undefined : "知识库尚未授权成员"} onClick={() => void saveAcl()}>保存并立即生效</Button></DialogActions></Dialog> : null}</section>;
}
