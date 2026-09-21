import { type FormEvent, type MouseEvent, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ConversationSummary, DataSource, DocumentCategory, DocumentIndexState, DocumentInfo, DocumentVersion, EvaluationReportSummary, GovernedOperation, IndexBuild, IndexEvaluationRun, IndexEvaluationRunDetail, IndexVersion, IndexVersionCandidatePreview, IndexVersionComparison, IndexVersionCreationContext, KnowledgeBase, RAGPolicy, User, } from "../types";
import { DocumentPanel } from "./DocumentPanel";
import { IndexVersionDetailDialog } from "./IndexVersionDetailDialog";
import { KnowledgeBaseForm } from "./KnowledgeBaseForm";
import { OperationDetailDialog } from "./OperationDetailDialog";
import { Dialog, DialogActions } from "./ui/Dialog";
import { PipelineStepper } from "./ui/PipelineStepper";
import { ReleaseFlow } from "./ui/ReleaseFlow";
import { CANDIDATE_STATUSES, releaseStages } from "./releaseStages";
import { Button } from "./ui/Button";
import { Checkbox } from "./ui/Checkbox";
import { Badge } from "./ui/Badge";
import { type Column, DataTable } from "./ui/DataTable";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input } from "./ui/Input";
import { type RowAction, RowActions } from "./ui/RowActions";
import { Select } from "./ui/Select";
import { Skeleton } from "./ui/Skeleton";
import { Tabs, type TabItem } from "./ui/Tabs";
import { Toolbar } from "./ui/Toolbar";
import { useToast } from "./ui/Toast";
import { KnowledgeBaseDataSourcesPanel } from "./KnowledgeBaseDataSourcesPanel";
import { IndexVersionCreationWizard } from "./IndexVersionCreationWizard";
import { ValidationReportViewer } from "./ValidationReportViewer";
import { useConfirm } from "./ui/useConfirm";

const STATUS = { empty: "空库", processing: "处理中", ready: "可用", degraded: "部分异常", failed: "失败" } as const;
// 与 KnowledgeBasesPage 的 STATUS_TONE 同一套约定：同一个 index_status 取值域，
// 在两处渲染成不同颜色才是真正的不一致——见 CLAUDE.md 第二条。
const STATUS_TONE = { empty: "neutral", processing: "brand", ready: "success", degraded: "warning", failed: "danger" } as const;
const VERSION_STATUS = { pending: "等待索引", indexing: "索引中", ready: "可用", failed: "失败", superseded: "历史版本" } as const;
const GOVERNANCE_STATUS: Record<string, string> = { queued: "等待处理", preparing: "准备中", running: "处理中", building: "构建中", validating: "验证中", ready: "待激活", activating: "激活中", active: "当前生效", previous: "上一版本", retired: "已退役", cleaned: "已清理", build_failed: "构建失败", validation_failed: "验证未通过", succeeded: "已完成", partial_failed: "部分失败", failed: "失败", cancel_requested: "正在取消", cancelled: "已取消", aborted: "已中止" };
const INDEX_CREATION_REASON: Record<string, string> = {
  legacy: "历史迁移", initial_build: "创建首个索引版本", config_changed: "配置已变更",
  document_snapshot_changed: "文档集合已变化", component_upgraded: "索引组件已升级",
  consistency_repair: "索引一致性修复", manual_rebuild: "主动创建回滚版本",
};
// 只放 operations.operation_type 的 CHECK 约束（0036 之后）真实允许的五种。
// index_validation / index_activation 已被 0036 删除——验证与激活是单事务动作，
// 不产生 operation 行；留在这里会让读代码的人以为运行记录能显示它们。
const OPERATION_TYPE_LABEL: Record<string, string> = { index_build: "索引构建", index_evaluation: "正式评测", sync_run: "数据同步", file_upload: "文件上传", file_update: "文件更新", document_reprocess: "资料重新处理" };
const OPERATION_STAGE_LABEL: Record<string, string> = { queued: "等待处理", discover: "发现资源", fetch: "获取内容", normalize: "内容规范化", parse: "解析资料", parsing: "解析资料", chunk: "资料切片", chunking: "资料切片", enrich: "补充元数据与权限", vector: "构建向量索引", keyword: "构建关键词索引", metadata: "构建元数据索引", build: "构建索引", validating: "验证索引", validate: "验证索引", activate: "激活版本", retry: "正在重试", retry_wait: "等待重试", complete: "已完成", completed: "已完成", cancelled: "已取消", failed: "失败",
  // 正式评测的七个阶段，与 backend/app/index_evaluation_runs.py 的 EVALUATION_STAGES
  // 逐字对应；后端加阶段这里不加，运行记录的「当前阶段」就会显示成英文原文。
  prepare_dataset: "准备数据集", build_corpus: "构建评测语料", retrieve: "执行召回",
  rerank: "执行精排", calculate_metrics: "计算指标", persist_report: "沉淀报告" };
const operationStage = (item: GovernedOperation) => item.current_stage === "failed" && item.error_message?.includes("没有可索引的文本") ? "parsing" : item.current_stage;
const CONFIG_FIELD_LABEL: Record<string, string> = {
  chunking_version: "切片策略", embedding_model: "向量模型",
  embedding_dimension: "向量维度", processing_options: "切片参数",
  parser_schema_version: "解析器版本",
  vector_index_schema_version: "Vector 索引结构", keyword_index_schema_version: "Keyword 索引结构",
  metadata_schema_version: "Metadata 结构", acl_schema_version: "ACL 结构",
  citation_schema_version: "Citation 结构", reranker_model: "Reranker 模型",
};
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

/**
 * 该索引版本的配置快照是否不完整。
 *
 * 判断只有这一处：索引版本表和「当前索引定义」都用它。此前「当前索引定义」直接把
 * parser/chunking/embedding 拼出来展示，而同一个版本在下方表格里标着「组件清单未知」
 * ——同一份数据两种说法（CLAUDE.md 第二条）。
 */
function isLegacyConfig(item: IndexVersion) {
  return item.config_completeness === "unknown"
    || (item.parser_version === "legacy" && item.chunking_version === "legacy" && item.embedding_model === "legacy");
}

const INDEX_VERSION_COLUMNS: Column<IndexVersion>[] = [
  {
    key: "version",
    header: "版本",
    width: "22%",
    render: (item) => <span className="grid gap-0.5"><strong className="font-medium text-ink">{item.version_no ? `v${item.version_no}` : "Legacy"}</strong><small className="truncate text-ink-faint" title={item.index_version_id}>{item.index_version_id}</small><small className="truncate text-ink-faint">{INDEX_CREATION_REASON[item.creation_reason] || item.creation_reason}</small></span>,
  },
  {
    key: "status",
    header: "状态",
    width: "12%",
    render: (item) => (
      <Badge shape="status" tone={item.status === "build_failed" || item.status === "validation_failed" ? "danger" : item.status === "building" || item.status === "validating" ? "brand" : item.status === "cleaned" || item.status === "retired" ? "neutral" : "success"}>
        {GOVERNANCE_STATUS[item.status] || item.status}
      </Badge>
    ),
  },
  {
    key: "configuration",
    header: "索引配置",
    width: "22%",
    render: (item) => {
      const legacy = isLegacyConfig(item);
      const value = legacy
        ? "历史索引配置不完整，缺少组件版本快照，不能作为新门禁的可复现配置"
        : `${item.parser_version} · ${item.chunking_version} · ${item.embedding_model} · ${item.embedding_dimension} 维`;
      return <span className="grid gap-0.5" title={value}><span className="truncate">{legacy ? "历史索引配置" : value}</span>{legacy ? <small className="truncate text-warning">组件清单未知 · 仅保留历史</small> : null}</span>;
    },
  },
  {
    key: "created",
    header: "创建时间",
    width: "12%",
    render: (item) => <span className="whitespace-nowrap">{new Date(item.created_at).toLocaleString("zh-CN")}</span>,
  },
];


export function KnowledgeBaseDetailPage({ id, onOpen, initialVersionId }: {
  id: string;
  onOpen: (path: string) => void;
  /**
   * 深链 `/knowledge-bases/{kb}/index-versions/{version}` 带来的版本 ID。
   *
   * 有它时直接落在索引治理 Tab 并打开详情弹框；关闭弹框把地址恢复成
   * `/knowledge-bases/{kb}`，不留一个打不开任何东西的 URL。
   */
  initialVersionId?: string;
}) {
  const requestedTab = new URLSearchParams(window.location.search).get("tab");
  const [activeTab, setActiveTab] = useState<"documents" | "data_sources" | "categories" | "versions" | "members" | "conversations">(
    initialVersionId ? "versions" : requestedTab === "data_sources" ? "data_sources" : requestedTab === "versions" ? "versions" : "documents",
  );
  const [base, setBase] = useState<KnowledgeBase | null>(null); const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [editingBase, setEditingBase] = useState(false);
  const [baseNameDraft, setBaseNameDraft] = useState("");
  const [baseDescriptionDraft, setBaseDescriptionDraft] = useState("");
  const [baseEditError, setBaseEditError] = useState("");
  const [savingBase, setSavingBase] = useState(false);
  const baseEditTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [versions, setVersions] = useState<DocumentVersion[]>([]); const [members, setMembers] = useState<User[]>([]);
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [indexVersions, setIndexVersions] = useState<IndexVersion[]>([]);
  const [operations, setOperations] = useState<GovernedOperation[]>([]);
  const [indexBuilds, setIndexBuilds] = useState<IndexBuild[]>([]);
  const [buildDocuments, setBuildDocuments] = useState<DocumentIndexState[] | null>(null);
  const [selectedOperation, setSelectedOperation] = useState<GovernedOperation | null>(null);
  const [selectedEvaluation, setSelectedEvaluation] = useState<IndexEvaluationRunDetail | null>(null);
  const [evaluationDetailLoading, setEvaluationDetailLoading] = useState(false);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(initialVersionId ?? null);
  const [evaluationRuns, setEvaluationRuns] = useState<IndexEvaluationRun[]>([]);
  const [evaluationTarget, setEvaluationTarget] = useState<IndexVersion | null>(null);
  const [evaluationDataset, setEvaluationDataset] = useState("corpus_v2");
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
  const [ragPolicy, setRagPolicy] = useState<RAGPolicy | null>(null);
  const [ragPolicyDraft, setRagPolicyDraft] = useState<RAGPolicy | null>(null);
  const [ragPolicyOpen, setRagPolicyOpen] = useState(false);
  const [savingRagPolicy, setSavingRagPolicy] = useState(false);
  const [ragPolicyError, setRagPolicyError] = useState("");
  const [aclTarget, setAclTarget] = useState<{ kind: "document" | "source"; id: string; name: string; version: number; allow: string[]; deny: string[] } | null>(null);
  const [aclDraft, setAclDraft] = useState<Record<string, "inherit" | "allow" | "deny">>({});
  const [savingAcl, setSavingAcl] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{ completed: number; total: number } | null>(null);
  const [taskTypeFilter, setTaskTypeFilter] = useState("");
  const [taskStatusFilter, setTaskStatusFilter] = useState("");
  const toast = useToast();
  const { confirm: confirmConversation, dialog: conversationConfirmDialog } = useConfirm();
  const openBaseEditor = (event: MouseEvent<HTMLButtonElement>) => {
    if (!base) return;
    baseEditTriggerRef.current = event.currentTarget;
    setBaseNameDraft(base.name);
    setBaseDescriptionDraft(base.description);
    setBaseEditError("");
    setEditingBase(true);
  };
  const closeBaseEditor = () => {
    if (savingBase) return;
    setEditingBase(false);
    setBaseEditError("");
  };
  const saveBase = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (savingBase) return;
    const normalizedName = baseNameDraft.trim();
    const normalizedDescription = baseDescriptionDraft.trim();
    if (!normalizedName) {
      setBaseEditError("请输入知识库名称。");
      return;
    }
    if (normalizedName.length > 80) {
      setBaseEditError("知识库名称不能超过 80 个字符。");
      return;
    }
    if (normalizedDescription.length > 500) {
      setBaseEditError("描述不能超过 500 个字符。");
      return;
    }
    setSavingBase(true);
    setBaseEditError("");
    try {
      const updated = await api.updateKnowledgeBase(id, normalizedName, normalizedDescription);
      setBase(updated);
      setEditingBase(false);
      toast.success("基础信息已更新");
    } catch (reason) {
      setBaseEditError(reason instanceof Error ? reason.message : "保存失败。");
    } finally {
      setSavingBase(false);
    }
  };
  const openRagPolicy = async () => {
    setRagPolicyError("");
    try {
      const policy = ragPolicy ?? await api.getRagPolicy(id);
      setRagPolicy(policy); setRagPolicyDraft({ ...policy, allowed_domains: [...policy.allowed_domains] }); setRagPolicyOpen(true);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取 RAG 策略。"); }
  };
  const saveRagPolicy = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!ragPolicyDraft || savingRagPolicy) return;
    setSavingRagPolicy(true); setRagPolicyError("");
    try {
      const updated = await api.updateRagPolicy(id, {
        rollout_stage: ragPolicyDraft.rollout_stage,
        web_search_enabled: ragPolicyDraft.web_search_enabled,
        allowed_domains: ragPolicyDraft.allowed_domains.map((item) => item.trim()).filter(Boolean),
        intent_confidence_threshold: ragPolicyDraft.intent_confidence_threshold,
        minimum_evidence_count: ragPolicyDraft.minimum_evidence_count,
        max_web_results: ragPolicyDraft.max_web_results,
      });
      setRagPolicy(updated); setRagPolicyDraft(updated); setRagPolicyOpen(false); toast.success("RAG 策略已更新");
    } catch (reason) { setRagPolicyError(reason instanceof Error ? reason.message : "RAG 策略保存失败。"); }
    finally { setSavingRagPolicy(false); }
  };
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
  const createIndexVersion = async (preview: IndexVersionCandidatePreview, excludedDocumentsAcknowledged: boolean) => {
    setBusy(true); setError("");
    try {
      const result = await api.createIndexVersion(id, preview, creationIdempotencyKey, excludedDocumentsAcknowledged);
      setCreationContext(null);
      toast.success(`索引版本 ${result.index_version_id} 已创建 · 构建 ${result.index_build_id} 已启动`);
      setSelectedVersionId(result.index_version_id);
      try {
        await load();
      } catch (refreshError) {
        toast.error(refreshError instanceof Error ? `版本已创建，但页面刷新失败：${refreshError.message}` : "版本已创建，但页面刷新失败。请手动刷新。");
      }
      return result;
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

  const load = useCallback(async () => { const detail = await api.getKnowledgeBase(id); const admin = detail.current_user_permission === "admin"; const [docs, history, versionItems, indexVersionItems, buildItems, operationItems, memberItems, sourceItems, categoryItems, reports] = await Promise.all([api.listKnowledgeBaseDocuments(id), api.listConversations(id), api.listKnowledgeBaseDocumentVersions(id), admin ? api.listKnowledgeBaseIndexVersions(id) : Promise.resolve([]), admin ? api.listKnowledgeBaseIndexBuilds(id) : Promise.resolve([]), admin ? api.listKnowledgeBaseOperations(id) : Promise.resolve([]), admin ? api.listKnowledgeBaseMembers(id) : Promise.resolve([]), admin ? api.listDataSources(0, 100) : Promise.resolve([]), api.listKnowledgeBaseCategories(id), admin ? api.listEvaluations() : Promise.resolve([])]); setBase(detail); setDocuments(docs); setConversations(history); setVersions(versionItems); setIndexVersions(indexVersionItems); setIndexBuilds(buildItems); setOperations(operationItems); setMembers(memberItems); setDataSources(sourceItems.filter((item) => item.knowledge_base_id === id)); setCategories(categoryItems); setEvaluationReports(reports);
    // 评测运行取整个知识库的，不只取候选版本那一份：运行记录里列的是 Operation，
    // 要把某一行翻译成评测详情得按 operation_id 在这份列表里找。**版本一旦激活就不再是
    // 候选**，只取候选的话那次评测的详情会永远打不开——页面照样列着这条记录，点开却
    // 只有「读取不到这次评测的明细」。
    //
    // 单独 try/catch 而不是并进上面的 Promise.all：它是次要数据，拿不到时该退化成
    // 「没有评测记录」，而不是让整个知识库详情页因为一个评测接口出问题就打不开。
    if (admin) {
      try { setEvaluationRuns(await api.listKnowledgeBaseEvaluationRuns(id)); }
      catch { setEvaluationRuns([]); }
      try { setRagPolicy(await api.getRagPolicy(id)); }
      catch { setRagPolicy(null); }
    } else {
      setEvaluationRuns([]);
      setRagPolicy(null);
    }
  }, [id]);
  useEffect(() => { Promise.resolve().then(load).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取知识库。")); }, [load]);
  useEffect(() => {
    // 只在真的有长任务在跑时轮询。终态后停下来——常驻请求既没有新信息，
    // 也会让「页面一直在动」掩盖掉真正需要注意的状态变化。
    const activeBuild = indexBuilds.some((item) => ["queued", "building"].includes(item.status));
    const activeOperation = operations.some((item) => ["queued", "running"].includes(item.status));
    const activeEvaluation = evaluationRuns.some((item) => ["queued", "running"].includes(item.status));
    if (!activeBuild && !activeOperation && !activeEvaluation) return;
    const timer = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(timer);
  }, [indexBuilds, operations, evaluationRuns, load]);
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
  const openConversation = (item: ConversationSummary) => {
    onOpen(`/chat/${item.conversation_id}?knowledge_base_id=${id}`);
  };
  const deleteConversation = (item: ConversationSummary) => {
    confirmConversation({
      title: "删除会话",
      consequence: `删除「${item.title}」后，该会话的全部问答记录都无法恢复，知识库资料不会受到影响。`,
      confirmLabel: "确认删除",
      tone: "destructive",
      onConfirm: async () => {
        try {
          await api.deleteConversation(id, item.conversation_id);
          setConversations((current) => current.filter((conversation) => conversation.conversation_id !== item.conversation_id));
          toast.success(`已删除会话「${item.title}」`);
        } catch (reason) {
          const message = reason instanceof Error ? reason.message : "会话删除失败。";
          toast.error(message);
          throw reason;
        }
      },
    });
  };
  const conversationColumns: Column<ConversationSummary>[] = [
    { key: "title", header: "会话", width: "42%", truncate: false, render: (item) => <Button variant="link" className="block max-w-full truncate text-left font-medium" title={item.title} onClick={() => openConversation(item)}>{item.title}</Button> },
    { key: "turns", header: "轮次", width: "12%", numeric: true, render: (item) => `${item.turn_count} 轮` },
    { key: "updated", header: "更新时间", width: "26%", render: (item) => new Date(item.updated_at).toLocaleString("zh-CN") },
    { key: "actions", header: "操作", width: "20%", align: "right", truncate: false, render: (item) => <span className="inline-flex items-center justify-end gap-1"><Button variant="ghost" size="sm" onClick={() => openConversation(item)}>查看</Button><Button variant="ghost" size="sm" className="text-danger-text hover:bg-danger-subtle" onClick={() => deleteConversation(item)}>删除</Button></span> },
  ];
  /**
   * 打开运行记录详情。
   *
   * 只设一个 state，明细按 `operation_type` 现取——此前「详情」按钮找得到 Index Build
   * 就在表格下方展开一块区域、找不到才弹框，同一个按钮通往两种 UI（CLAUDE.md 第二条）。
   */
  const openOperationDetail = async (item: GovernedOperation) => {
    setSelectedOperation(item);
    setBuildDocuments(null);
    setSelectedEvaluation(null);
    setError("");
    if (item.operation_type === "index_build") {
      const build = indexBuilds.find((candidate) => candidate.operation_id === item.operation_id);
      if (!build) { setBuildDocuments([]); return; }
      setBuildDetailLoading(true);
      try { setBuildDocuments(await api.listIndexBuildDocuments(id, build.index_build_id)); }
      catch (reason) { setError(reason instanceof Error ? reason.message : "构建详情读取失败。"); setBuildDocuments([]); }
      finally { setBuildDetailLoading(false); }
      return;
    }
    if (item.operation_type === "index_evaluation") {
      const run = evaluationRuns.find((candidate) => candidate.operation_id === item.operation_id);
      // 对不上就说出来。静默 return 的后果是弹框开着、内容区写「读取不到这次评测的
      // 明细」，而用户无从知道是权限、是接口失败还是这条记录本就没有评测运行。
      if (!run) { setError("这条运行记录没有对应的正式评测明细，可能评测记录已被清理。"); return; }
      setEvaluationDetailLoading(true);
      try { setSelectedEvaluation(await api.getIndexEvaluationRun(id, run.evaluation_run_id)); }
      catch (reason) { setError(reason instanceof Error ? reason.message : "评测详情读取失败。"); }
      finally { setEvaluationDetailLoading(false); }
    }
  };

  const runEvaluation = async (version: IndexVersion, datasetId: string) => {
    setBusy(true); setError("");
    try { await api.createIndexEvaluationRun(id, version.index_version_id, datasetId); setEvaluationTarget(null); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "正式评测创建失败。"); }
    finally { setBusy(false); }
  };
  const retryEvaluation = async (runId: string) => {
    setBusy(true); setError("");
    try { await api.retryIndexEvaluationRun(id, runId); setSelectedOperation(null); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "重新运行评测失败。"); }
    finally { setBusy(false); }
  };
  const cancelEvaluation = async (runId: string) => {
    setBusy(true); setError("");
    try { await api.cancelIndexEvaluationRun(id, runId); setSelectedOperation(null); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "取消评测失败。"); }
    finally { setBusy(false); }
  };
  /**
   * 质量状态：这个版本有没有可用于发布的正式质量报告。
   *
   * 判据与验证弹层的 compatibleEvaluationReports 完全一致（通过 + 配置指纹相同），
   * 两处分叉就会出现「列表说已通过、弹层里选不到报告」。
   * 历史版本单独一档：它们没有配置指纹，不是「缺报告」而是**无法参与新门禁**，
   * 显示成「—」会让人以为跑一次评测就能发布（实施计划第 27、41 节）。
   */
  const qualityColumn: Column<IndexVersion> = {
    key: "quality",
    header: "质量状态",
    width: "16%",
    render: (item) => {
      if (isLegacyConfig(item) || !item.config_fingerprint) {
        return <span className="text-ink-faint">历史版本</span>;
      }
      // 已经上线过的版本看它**发布时绑定的验证报告**，不看当前有没有匹配的评测报告：
      // 评测报告是候选版本发布前要跑的东西，拿它去判断线上版本，会让一个正常服务的
      // active 版本显示「缺少可用报告」——看起来像线上索引出了问题（实测过这个误报）。
      if (!CANDIDATE_STATUSES.has(item.status)) {
        return item.validation_report_id
          ? <span className="text-success">发布时已验证</span>
          : <span className="text-ink-faint">未记录验证报告</span>;
      }
      // 判据是 official（受控正式运行）而不是 passed（达到冻结阈值）。两者此前被绑在
      // 一起，「跑完没达标」的报告根本不标 official，页面只能说「缺少可用报告」——
      // 用户被引导去重跑评测，而真正该看的是哪项指标没到线。
      const report = evaluationReports.find((item2) => item2.official && item2.config_fingerprint === item.config_fingerprint);
      if (report) {
        return report.passed
          ? <span className="text-success">正式评测已通过</span>
          : <span className="text-warning">已有报告 · 未达阈值</span>;
      }
      const running = evaluationRuns.some((run) => run.index_version_id === item.index_version_id && ["queued", "running"].includes(run.status));
      if (running) return <span className="text-brand">正式评测进行中</span>;
      return <span className="text-warning">缺少可用报告</span>;
    },
  };

  /** 与该版本配置指纹一致的正式报告。official 是判据，passed 只作展示。 */
  const matchingReportFor = (item: IndexVersion) =>
    evaluationReports.find((report) => report.official && report.config_fingerprint === item.config_fingerprint) ?? null;
  /** 该版本当前未完成的评测运行。 */
  const activeEvaluationFor = (item: IndexVersion) =>
    evaluationRuns.find((run) => run.index_version_id === item.index_version_id && ["queued", "running"].includes(run.status)) ?? null;
  const failedEvaluationFor = (item: IndexVersion) =>
    evaluationRuns.find((run) => run.index_version_id === item.index_version_id && run.status === "failed") ?? null;

  /**
   * 候选版本的评测动作。
   *
   * 规则来自实施计划 Task 9 Step 2：
   *   validating + 无匹配报告 + 无进行中运行 → 运行正式评测
   *   queued / running                      → 查看评测进度
   *   failed                                → 重新运行评测
   * 已经有匹配报告时这一格不出现动作——那时该做的是三层验证，不是再跑一次评测。
   */
  const evaluationActions = (item: IndexVersion): RowAction[] => {
    if (!["validating", "validation_failed"].includes(item.status)) return [];
    const running = activeEvaluationFor(item);
    if (running) {
      const operation = operations.find((candidate) => candidate.operation_id === running.operation_id);
      return [{ label: "查看评测进度", onSelect: () => { if (operation) void openOperationDetail(operation); } }];
    }
    const failed = failedEvaluationFor(item);
    if (failed && !matchingReportFor(item)) {
      return [{ label: "重新运行评测", onSelect: () => void retryEvaluation(failed.evaluation_run_id) }];
    }
    if (matchingReportFor(item)) return [];
    return [{
      label: "运行正式评测",
      onSelect: () => { setEvaluationTarget(item); setEvaluationDataset("corpus_v2"); setError(""); },
      blockedReason: item.config_completeness === "unknown"
        ? "历史版本没有完整配置快照，无法运行可用于发布的正式评测"
        : undefined,
    }];
  };

  // 列序：版本 → 状态 → 索引配置 → 质量状态 → 创建时间 → 操作（实施计划 Step 4）。
  // 质量状态紧跟配置，因为它判定的正是「这份配置有没有通过评测」。
  const governedIndexVersionColumns: Column<IndexVersion>[] = [
    ...INDEX_VERSION_COLUMNS.filter((column) => column.key !== "created"),
    qualityColumn,
    ...INDEX_VERSION_COLUMNS.filter((column) => column.key === "created"),
    { key: "actions", header: "操作", width: "18%", align: "right", truncate: false, render: (item) => <RowActions rowLabel={item.index_version_id} actions={[
      { label: "详情", onSelect: () => setSelectedVersionId(item.index_version_id) },
      ...(item.status === "building" ? [{ label: "取消构建", tone: "destructive" as const, onSelect: () => { setCancelBuildTarget(item); setError(""); } } as RowAction] : []),
      // 动作顺序对应真实业务顺序：没有匹配报告先跑评测，有报告才谈验证。
      // 评测进行中时给的是「查看评测进度」而不是再排一次——后端也会以 409 拒绝。
      ...(evaluationActions(item)),
      ...(["validating", "validation_failed"].includes(item.status) && matchingReportFor(item) ? [{ label: item.status === "validation_failed" ? "重新验证" : "执行三层验证", onSelect: () => { setValidationTarget(item); setReportId(matchingReportFor(item)?.report_id ?? ""); setError(""); } } as RowAction] : []),
      ...(["build_failed", "validating", "validation_failed"].includes(item.status) ? [{ label: "重新构建", onSelect: async () => { setBusy(true); setError(""); try { await api.retryIndexVersionBuild(id, item.index_version_id); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "索引重新构建失败。"); } finally { setBusy(false); } } } as RowAction] : []),
      ...(item.status === "ready" ? [{ label: "激活", onSelect: () => { setActivationTarget(item); setError(""); } } as RowAction] : []),
      ...(item.status === "previous" ? [{ label: "回滚", onSelect: () => void openRollback(item) } as RowAction, { label: "退役", tone: "destructive" as const, onSelect: () => { setRetireTarget(item); setError(""); } } as RowAction] : []),
      ...(["retired", "build_failed", "validation_failed"].includes(item.status) ? [{ label: "清理", tone: "destructive" as const, onSelect: () => { setCleanupTarget(item); setError(""); } } as RowAction] : []),
    ]}/> },
  ];
  // 弹层打开时错误只显示在弹层内：Radix 给背景内容加了 aria-hidden，
  // 顶部横幅在弹层背后，既看不见也不会被屏幕阅读器读到。
  const dialogOpen = Boolean(ragPolicyOpen || editingBase || categoryForm || deletingCategory || aclTarget || activationTarget || validationTarget || cleanupTarget || cancelBuildTarget || retireTarget || rollbackTarget || creationContext || selectedOperation || selectedVersionId || evaluationTarget);
  // 可选报告的判据是 official + 指纹一致，与质量状态列、releaseStages 同源。
  // 不再要求 passed：未达阈值的受控报告也是三层验证的合法证据，是否可发布由验证决定。
  const compatibleEvaluationReports = validationTarget
    ? evaluationReports.filter((report) =>
        report.official && report.config_fingerprint === validationTarget.config_fingerprint)
    : [];

  const fileSourceIds = new Set(dataSources.filter((item) => item.source_type === "file").map((item) => item.data_source_id));
  const uploadedDocuments = documents.filter((item) => !item.data_source_id || fileSourceIds.has(item.data_source_id) || item.source_type === "upload");
  const externalDataSources = dataSources.filter((item) => item.source_type !== "file");
  const activeIndexVersion = indexVersions.find((item) => item.status === "active") ?? null;
  /** 正在走发布流程的候选版本。质量报告的「发布用途」按它的配置指纹判断。 */
  const activeCandidate = indexVersions.find((item) => CANDIDATE_STATUSES.has(item.status)) ?? null;
  /** 正式质量报告：受控正式运行产出的都列出来，达标与否作为一列如实显示。 */
  const releaseReports = evaluationReports.filter((report) => report.official);
  /**
   * 一份正式报告相对当前版本的位置。
   *
   * 判据只有配置指纹，**不看 `passed`**：能不能放行由三层验证给结论，未达阈值的正式
   * 报告照样是发布证据（后端 `create_scoped_index_validation()` 就是这么做的）。这里
   * 曾经先过滤 `passed`，于是一份 `official=true, passed=false` 的报告在页面上写着
   * 「不可用于发布」，而它实际上刚刚放行了线上那个版本。
   *
   * 没有候选版本时回退到线上版本比较：否则所有报告——包括刚用来激活当前版本的那一份
   * ——都会被说成「配置已变化」，而配置根本没变。
   */
  const reportScope = (report: EvaluationReportSummary): "candidate" | "active" | "changed" => {
    if (!report.config_fingerprint) return "changed";
    if (activeCandidate) {
      return report.config_fingerprint === activeCandidate.config_fingerprint ? "candidate" : "changed";
    }
    return activeIndexVersion && report.config_fingerprint === activeIndexVersion.config_fingerprint
      ? "active"
      : "changed";
  };

  /**
   * 索引治理页的运行记录只列索引治理自己的任务——**只有 `index_build` 一种。**
   *
   * `operations.operation_type` 的 CHECK 约束（`0036_prune_dead_enum_values.sql` 之后）
   * 只允许 index_build / sync_run / file_upload / file_update / document_reprocess，
   * 其中后四种属资料与数据同步，放进这一页会让「索引现在进行到哪一步」被无关任务淹没
   * （实施计划第 13 节）。
   *
   * **不要往这个数组里加 `index_validation` / `index_activation`。** 它们曾在枚举里，
   * 被 0036 删掉了，理由写在那个迁移里——「验证与激活都是单事务动作，没有进度可跟踪，
   * 不需要 operation 行」。我第一版就把这两个死值写进来了，运行记录看着支持三种类型、
   * 实际永远只出现一种；而那个迁移开头正好在数落这类错误：声明了却没有产生者的枚举
   * 会让人据此设计前端、写监控告警。
   *
   * V39 之后多了一种：`index_evaluation`。它与 index_build 一样是有真实阶段的长任务
   * （建语料 → 召回 → 精排 → 算指标），由 Evaluation Worker 写入 operations，
   * 因此进得来。回滚/退役/清理仍然不在这里——它们是单事务动作，没有 operation 行。
   */
  const INDEX_GOVERNANCE_OPERATIONS = ["index_build", "index_evaluation"];
  const governanceOperations = operations.filter((item) => INDEX_GOVERNANCE_OPERATIONS.includes(item.operation_type));
  const taskRecords = governanceOperations.filter((item) =>
    (!taskTypeFilter || item.operation_type === taskTypeFilter)
    && (!taskStatusFilter || item.status === taskStatusFilter));

  const tabs: TabItem[] = [
    { value: "documents", label: "资料", count: uploadedDocuments.length },
    ...(base?.current_user_permission === "admin" ? [{ value: "data_sources", label: "数据源", count: externalDataSources.length }] : []),
    { value: "categories", label: "分类管理", count: categories.length },
    // 「索引治理」而不是「版本治理」：这个 Tab 治理的是索引版本的构建、验证与发布。
    // 资料版本已按实施计划第 2 节归到「资料」Tab，标题为「全部资料版本」。
    { value: "versions", label: "索引治理", count: indexVersions.length },
    { value: "members", label: "权限边界", count: members.length },
    { value: "conversations", label: "会话", count: conversations.length },
  ];

  return <section className="mx-auto max-w-[1440px] p-[26px_24px_52px] min-[1025px]:p-[20px_20px_40px]"><div className="mb-[22px] flex items-center justify-between gap-4 max-md:flex-col max-md:items-stretch"><Button variant="link" className="max-md:self-start" onClick={() => onOpen("/knowledge-bases")}>← 返回知识库</Button>{base ? <div className="flex flex-wrap items-center justify-end gap-2">{base.current_user_permission === "admin" ? <Button variant="secondary" size="sm" onClick={() => void openRagPolicy()}>RAG 策略</Button> : null}{base.current_user_permission === "admin" && (base.allowed_actions ?? []).includes("edit") ? <Button variant="secondary" size="sm" onClick={openBaseEditor}>编辑基础信息</Button> : null}<Button size="sm" onClick={() => onOpen(`/chat?knowledge_base_id=${id}`)}>在此知识库提问 →</Button></div> : null}</div>{error && !dialogOpen ? <ErrorBanner>{error}</ErrorBanner> : null}{/* 加载中整页空白会让人以为知识库打不开——骨架按真实高度占位，读屏由 role="status" 播报。 */}{!base && !error ? <div className="grid gap-4"><span role="status" className="sr-only">正在读取知识库</span><Skeleton className="h-7 w-48"/><Skeleton className="h-[104px] w-full"/><Skeleton className="h-10 w-full"/><Skeleton className="h-64 w-full"/></div> : null}{base ? <>
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
      {activeTab === "documents" ? <section className="grid gap-3">
        <DocumentPanel knowledgeBaseId={id} documents={uploadedDocuments} versions={versions} categories={categories} operations={operations} loading={busy} uploadProgress={uploadProgress} onUpload={upload} onUpdateFile={updateFile} onDelete={remove} onUpdateMetadata={updateMetadata} onBatchCategory={batchCategory} onReclassify={reclassify} onRetryProcessing={retryProcessing} canManage={base.current_user_permission === "admin"}/>
        {/* 「全部资料版本」这个标题不是装饰：这张表有意不跟随上方的分类/状态筛选
            （筛选属于资料列表），标题与副标题就是它不受筛选影响的可见说明——
            否则筛掉的文件名仍出现在这里，用户会以为筛选失效。 */}
        <div><h3 className="m-0 text-[16px] font-bold text-ink">全部资料版本</h3><p className="m-0 mt-1 text-sm text-ink-faint">列出该知识库的每一份资料版本，不受上方分类与状态筛选影响。</p></div><DataTable label="全部资料版本" rows={versions} rowKey={(item) => item.document_version_id} columns={documentVersionColumns} emptyState={{ kind: "empty", title: "还没有资料版本", description: "上传并解析资料后，这里会显示资料版本。" }}/>
      </section> : null}
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
      {/* 区块间距只有一个来源：section 的 gap-5（20px，计划第 31 节要 20~24px）。
          此前是 gap-3(12px) 叠加各区块自己的 mt-[18px]，实际 30px 且改一处影响另一处。 */}
      {activeTab === "versions" ? <section className="grid gap-5">
        {/* 页面头部：一句话说清这个 Tab 管什么，不放 Dashboard 式 KPI
            （实施计划第 4 节明确列了「版本总数/运行中/失败/报告」这类计数不是主任务）。 */}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="m-0 text-[16px] font-bold text-ink">索引治理</h3>
            <p className="m-0 mt-1 text-sm text-ink-faint">管理索引版本的构建、质量验证、发布与回滚。</p>
          </div>
          {base.current_user_permission === "admin" ? <Button size="sm" loading={busy || creationContextLoading} onClick={() => void openCreationWizard()}>创建索引版本</Button> : null}
        </div>

        {/* 发布流程摘要。回答「候选版本进行到哪一步」「为什么还不能发布」——
            每个格子的 note 就是它停在这里的原因，鼠标悬停与读屏都能拿到。 */}
        <ReleaseFlow stages={releaseStages(indexVersions, evaluationReports, base.index_config_drift)}/>

        {/* 当前索引定义。配置摘要取当前生效版本；有没有变更直接用后端算好的
            index_config_drift，不在前端重算——两套判断一旦分叉就会出现
            「说配置变了却列不出变了什么」（CLAUDE.md 第一条的同源原则）。 */}
        <section className="grid gap-1 border-t border-divider pt-3">
          <h4 className="m-0 text-md font-semibold text-ink">当前索引定义</h4>
          {!activeIndexVersion
            ? <p className="m-0 text-sm text-ink-faint">还没有生效的索引版本，创建并激活后这里显示线上配置。</p>
            : isLegacyConfig(activeIndexVersion)
              ? <p className="m-0 text-sm text-warning">历史索引配置不可完整追溯：该版本创建于当前索引治理机制启用之前，没有留下组件版本快照。</p>
              : <p className="m-0 text-sm text-ink-muted">{activeIndexVersion.parser_version} · {activeIndexVersion.chunking_version} · {activeIndexVersion.embedding_model} · {activeIndexVersion.embedding_dimension} 维</p>}
          {base.index_config_drift.length
            ? <p className="m-0 text-sm text-warning">配置已更新 {base.index_config_drift.length} 项（{base.index_config_drift.map((item) => CONFIG_FIELD_LABEL[item.field] || item.field).join("、")}），新建索引版本后生效。</p>
            : <p className="m-0 text-sm text-ink-faint">当前配置与最新索引版本一致。</p>}
        </section>

        <h4 className="mt-2 mb-0 text-md font-semibold text-ink">索引版本</h4>
        <DataTable label="索引版本" rows={indexVersions} rowKey={(item) => item.index_version_id} columns={governedIndexVersionColumns} emptyState={{ kind: "empty", title: "还没有索引版本", description: "创建首个索引版本后，将按快照构建、验证并等待激活。" }}/>
        {creationContext ? <IndexVersionCreationWizard key={creationContext.definition.config_fingerprint ?? creationContext.scenario} open context={creationContext} busy={busy} onClose={() => { if (!busy) setCreationContext(null); }} onPreview={previewIndexVersion} onCreate={createIndexVersion}/> : null}
        {cancelBuildTarget ? <Dialog open title="取消索引构建" description={cancelBuildTarget.index_version_id} onClose={() => { setCancelBuildTarget(null); setError(""); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<p className="text-sm text-ink-muted">将停止尚未完成的文档任务，并把 Version 标记为 build_failed。已完成的候选分块不会上线，之后可选择重新构建或清理。</p><DialogActions><Button variant="secondary" loading={busy} onClick={() => setCancelBuildTarget(null)}>继续构建</Button><Button variant="destructive" loading={busy} onClick={async () => { setBusy(true); setError(""); try { await api.cancelIndexVersionBuild(id, cancelBuildTarget.index_version_id); setCancelBuildTarget(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "取消索引构建失败。"); } finally { setBusy(false); } }}>确认取消</Button></DialogActions></Dialog> : null}
        {validationTarget ? <Dialog open title={validationTarget.status === "validation_failed" ? "重新验证索引版本" : "验证索引版本"} description={validationTarget.index_version_id} onClose={() => { setValidationTarget(null); setError(""); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}{compatibleEvaluationReports.length ? <><label className="grid gap-2 text-sm text-ink-muted">正式质量报告<Select value={reportId} onChange={(event) => setReportId(event.target.value)}><option value="">选择配置指纹匹配的正式报告</option>{compatibleEvaluationReports.map((report) => <option key={report.report_id} value={report.report_id}>{report.report_id} · {report.passed ? "已达阈值" : "未达阈值"} · 指纹匹配</option>)}</Select></label>{/* 阈值结论如实显示，但它不是放行判据——放行由下面三层门禁给结论。
        这句话必须写在弹框里：只显示「未达阈值」而不解释它是否影响发布，用户会以为选了也没用。 */}
      <p className="my-3 text-sm text-ink-faint">报告的阈值结论只作参考；最终是否可发布由完整性、技术与检索质量三层验证决定。</p></> : <div className="my-3 grid gap-2 rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning"><strong>还没有用本版本配置跑过正式评测。</strong><span>三层验证需要一份配置指纹与本版本一致的正式报告。其他版本或旧版无配置指纹的报告不能用于放行。</span></div>}<p className="text-sm text-ink-faint">本次只执行完整性、技术与检索质量三层门禁；通过后状态变为 ready（待激活），不会自动切换线上版本。</p><DialogActions><Button variant="secondary" loading={busy} onClick={() => setValidationTarget(null)}>取消</Button>{compatibleEvaluationReports.length ? <Button loading={busy} blockedReason={reportId.trim() ? undefined : "请选择与本版本配置匹配的质量报告"} onClick={async () => { setBusy(true); setError(""); try { await api.createIndexVersionValidation(id, validationTarget.index_version_id, reportId.trim()); setValidationTarget(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "索引验证失败。"); } finally { setBusy(false); } }}>执行三层验证</Button> : <Button loading={busy} onClick={() => { const target = validationTarget; setValidationTarget(null); setEvaluationTarget(target); setEvaluationDataset("corpus_v2"); }}>运行正式评测</Button>}</DialogActions></Dialog> : null}
        {evaluationTarget ? <Dialog open title="运行正式检索评测" description={evaluationTarget.index_version_id} onClose={() => { setEvaluationTarget(null); setError(""); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<label className="grid gap-2 text-sm text-ink-muted">评测数据集<Select value={evaluationDataset} onChange={(event) => setEvaluationDataset(event.target.value)}><option value="corpus_v2">corpus_v2 · 语料级检索评测</option><option value="corpus_v2_paraphrased">corpus_v2_paraphrased · 同义改写评测</option></Select></label><p className="my-3 text-sm text-ink-muted">评测会在隔离的评测数据库里重建这一版配置的临时语料，跑完整的召回与精排，产出一份可用于三层验证的正式报告。它由独立的 Evaluation Worker 执行，不占用索引构建队列。</p><p className="m-0 text-sm text-ink-faint">任务创建后可在下方「运行记录」里查看进度；跑完之后回到本版本执行三层验证。</p><DialogActions><Button variant="secondary" loading={busy} onClick={() => setEvaluationTarget(null)}>取消</Button><Button loading={busy} onClick={() => void runEvaluation(evaluationTarget, evaluationDataset)}>创建评测任务</Button></DialogActions></Dialog> : null}
        {activationTarget ? <Dialog open title="激活索引版本" description={activationTarget.index_version_id} onClose={() => { setActivationTarget(null); setError(""); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<p className="text-sm text-ink-muted">该版本已通过三层门禁，激活只执行原子指针切换。当前 active 将变为 previous，不会重新运行验证。</p><p className="flex flex-wrap items-center gap-1 text-sm text-ink-faint">验证报告：{activationTarget.validation_report_id ? <><span className="text-success">已通过 ·</span><ValidationReportViewer knowledgeBaseId={id} versionId={activationTarget.index_version_id} reportId={activationTarget.validation_report_id}/></> : "尚未执行"}</p><DialogActions><Button variant="secondary" loading={busy} onClick={() => setActivationTarget(null)}>取消</Button><Button loading={busy} onClick={async () => { setBusy(true); setError(""); try { await api.activateIndexVersion(id, activationTarget.index_version_id); setActivationTarget(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "索引激活失败。"); } finally { setBusy(false); } }}>确认激活</Button></DialogActions></Dialog> : null}
        {rollbackTarget ? <Dialog open size="md" title="回滚上一索引版本" description={rollbackTarget.index_version_id} onClose={() => { setRollbackTarget(null); setVersionComparison(null); setError(""); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}{versionComparison ? <><dl className="grid grid-cols-3 gap-3 text-sm max-sm:grid-cols-1"><div><dt className="text-ink-faint">目标物理范围</dt><dd className="m-0 mt-1">{versionComparison.actual_scope.documents} 份 / {versionComparison.actual_scope.chunks} Chunks</dd></div><div><dt className="text-ink-faint">配置差异</dt><dd className="m-0 mt-1">{versionComparison.config_diff.length} 项</dd></div><div><dt className="text-ink-faint">文档快照差异</dt><dd className="m-0 mt-1">+{versionComparison.document_diff.added} / -{versionComparison.document_diff.removed} / 更新 {versionComparison.document_diff.updated}</dd></div></dl><p className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">{versionComparison.content_snapshot_note}</p>{versionComparison.config_diff.length ? <ul className="m-0 grid max-h-36 gap-1 overflow-y-auto p-0 text-sm">{versionComparison.config_diff.map((item) => <li key={item.field} className="list-none rounded border border-divider p-2"><strong>{CONFIG_FIELD_LABEL[item.field] || item.field}</strong><span className="ml-2 break-all text-ink-faint">{JSON.stringify(item.baseline) || "—"} → {JSON.stringify(item.target) || "—"}</span></li>)}</ul> : <p className="text-sm text-ink-faint">与当前 active 配置一致。</p>}<dl className="grid grid-cols-2 gap-3 text-sm max-sm:grid-cols-1"><div><dt className="text-ink-faint">目标验证报告</dt><dd className="m-0 mt-1">{String(versionComparison.validation_comparison.target?.status || "无正式报告")}</dd></div><div><dt className="text-ink-faint">当前 active 验证报告</dt><dd className="m-0 mt-1">{String(versionComparison.validation_comparison.baseline?.status || "无正式报告")}</dd></div><div><dt className="text-ink-faint">回滚后可检索资料</dt><dd className="m-0 mt-1">{versionComparison.current_content.retrievable_documents} 份 / {versionComparison.current_content.retrievable_chunks} Chunks</dd></div><div><dt className="text-ink-faint">当前内容差异</dt><dd className="m-0 mt-1">+{versionComparison.current_content.diff.added} / -{versionComparison.current_content.diff.removed} / 更新 {versionComparison.current_content.diff.updated}</dd></div></dl></> : <p className="text-sm text-ink-faint">正在读取版本差异…</p>}<DialogActions><Button variant="secondary" loading={busy} onClick={() => setRollbackTarget(null)}>取消</Button><Button loading={busy} blockedReason={versionComparison ? undefined : "版本差异尚未读取完成"} onClick={async () => { setBusy(true); setError(""); try { await api.rollbackIndexVersion(id, Boolean(versionComparison?.current_content.requires_confirmation)); setRollbackTarget(null); setVersionComparison(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "索引回滚失败。"); } finally { setBusy(false); } }}>确认回滚</Button></DialogActions></Dialog> : null}
        {retireTarget ? <Dialog open title="退役上一索引版本" description={retireTarget.index_version_id} onClose={() => { setRetireTarget(null); setError(""); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<p className="text-sm text-ink-muted">退役后将失去一键回滚到该版本的能力，但物理索引仍保留；需要另行执行 Cleanup 才会删除。</p><DialogActions><Button variant="secondary" loading={busy} onClick={() => setRetireTarget(null)}>取消</Button><Button variant="destructive" loading={busy} onClick={async () => { setBusy(true); setError(""); try { await api.retireIndexVersion(id, retireTarget.index_version_id); setRetireTarget(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "索引退役失败。"); } finally { setBusy(false); } }}>确认退役</Button></DialogActions></Dialog> : null}
        {cleanupTarget ? <Dialog open title="清理索引物理内容" description={cleanupTarget.index_version_id} onClose={() => { setCleanupTarget(null); setError(""); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<p className="text-sm text-ink-muted">将删除该版本全部 Chunks 与专属 HNSW 索引。版本记录和生命周期事件会保留，状态变为 cleaned，且不能再激活或回滚。</p><DialogActions><Button variant="secondary" loading={busy} onClick={() => setCleanupTarget(null)}>取消</Button><Button variant="destructive" loading={busy} onClick={async () => { setBusy(true); setError(""); try { await api.cleanupIndexVersion(id, cleanupTarget.index_version_id); setCleanupTarget(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "索引清理失败。"); } finally { setBusy(false); } }}>确认清理</Button></DialogActions></Dialog> : null}
        {/* 正式质量评测。直接展开在索引版本下方而不是另开 Tab（实施计划第 11 节）：
            「候选版本质量是否达标」是发布决策的一部分，藏进 Tab 就得多点一次才能判断。
            「发布用途」这一列把 config_fingerprint 的匹配结果翻译成人话——主 UI 不出现
            Fingerprint Match / mismatch 这类工程术语（第 11、29 节）。 */}
        <div className="grid gap-1">
          <h3 className="m-0 text-[16px] font-bold text-ink">正式质量评测</h3>
          <p className="m-0 text-sm text-ink-faint">用于确认候选索引的检索质量，并作为发布验证依据。</p>
        </div>
        <DataTable label="正式质量评测" rows={releaseReports} rowKey={(item) => item.report_id} density="compact" columns={[
          { key: "report", header: "报告", width: "26%", render: (item: EvaluationReportSummary) => <span className="grid justify-items-start gap-0.5"><Button variant="link" className="h-auto max-w-full justify-start truncate px-0 py-0 text-left" title={item.report_id} onClick={() => onOpen(`/evaluation?view=reports&report=${encodeURIComponent(item.report_id)}`)}>{item.report_id}</Button><small className="text-ink-faint">{item.dataset_id} · {item.dataset_version}</small></span> },
          { key: "config", header: "评测配置", width: "20%", render: (item: EvaluationReportSummary) => {
            if (!item.config_fingerprint) return <span className="text-ink-faint">历史报告</span>;
            const scope = reportScope(item);
            if (scope === "candidate") return <span className="text-ink">与当前版本一致</span>;
            if (scope === "active") return <span className="text-ink">与线上版本一致</span>;
            return <span className="text-ink-faint">配置已变化</span>;
          } },
          { key: "result", header: "结果", width: "14%", truncate: false, render: (item: EvaluationReportSummary) => <Badge shape="status" tone={item.passed ? "success" : "danger"}>{item.passed ? "已通过" : "未通过"}</Badge> },
          { key: "usage", header: "发布用途", width: "22%", render: (item: EvaluationReportSummary) => {
            if (!item.config_fingerprint) return <span className="text-ink-faint">不可用于发布 · 无配置快照</span>;
            const scope = reportScope(item);
            if (scope === "candidate") return <span className="text-success">可用于发布</span>;
            if (scope === "active") return <span className="text-ink-faint">已用于当前线上版本</span>;
            return <span className="text-ink-faint">不可用于发布 · 配置不匹配</span>;
          } },
          { key: "run_at", header: "时间", width: "18%", render: (item: EvaluationReportSummary) => <span className="whitespace-nowrap">{new Date(item.run_at).toLocaleString("zh-CN")}</span> },
        ]} emptyState={{ kind: "empty", title: "缺少可用于发布的质量报告", description: "当前版本尚未完成符合发布要求的正式检索评测。请先运行正式评测，通过后即可继续发布验证。" }}/>

        <h3 className="m-0 text-[16px] font-bold text-ink">运行记录</h3>
        <Toolbar filters={<><label className="flex items-center gap-2 text-md">任务类型<Select size="sm" className="w-36" aria-label="任务类型筛选" value={taskTypeFilter} onChange={(event) => setTaskTypeFilter(event.target.value)}><option value="">全部类型</option>{[...new Set(governanceOperations.map((item) => item.operation_type))].map((value) => <option key={value} value={value}>{OPERATION_TYPE_LABEL[value] || value}</option>)}</Select></label><label className="flex items-center gap-2 text-md">状态<Select size="sm" className="w-32" aria-label="任务状态筛选" value={taskStatusFilter} onChange={(event) => setTaskStatusFilter(event.target.value)}><option value="">全部状态</option>{[...new Set(governanceOperations.map((item) => item.status))].map((value) => <option key={value} value={value}>{GOVERNANCE_STATUS[value] || value}</option>)}</Select></label></>}/>
        <DataTable label="运行记录" rows={taskRecords} rowKey={(item) => item.operation_id} columns={[
          { key: "type", header: "任务类型", width: "120px", render: (item) => OPERATION_TYPE_LABEL[item.operation_type] || item.operation_type },
          { key: "stage", header: "当前阶段", width: "110px", render: (item) => OPERATION_STAGE_LABEL[operationStage(item)] || operationStage(item) },
          { key: "progress", header: "进度", width: "520px", truncate: false, render: (item) => <PipelineStepper kind={item.operation_type} currentStage={operationStage(item)} status={item.status} progressPercent={item.progress_percent} label={`${OPERATION_TYPE_LABEL[item.operation_type] || item.operation_type}进度`} failureReason={item.error_message}/> },
          { key: "count", header: "处理数量", width: "90px", render: (item) => `${item.completed_count}/${item.total_count}` },
          { key: "status", header: "状态", width: "100px", render: (item) => <Badge shape="status" tone={item.status === "failed" || item.status === "aborted" ? "danger" : item.status === "succeeded" ? "success" : "brand"}>{GOVERNANCE_STATUS[item.status] || item.status}</Badge> },
          { key: "updated", header: "更新时间", width: "150px", render: (item) => new Date(item.updated_at).toLocaleString("zh-CN") },
          { key: "actions", header: "操作", width: "72px", align: "right", truncate: false, render: (item) => <Button variant="ghost" size="sm" onClick={() => void openOperationDetail(item)}>详情</Button> },
        ]} emptyState={taskTypeFilter || taskStatusFilter ? { kind: "filtered", title: "没有符合条件的运行记录", description: "调整任务类型或状态筛选后重试。" } : { kind: "empty", title: "暂无运行记录", description: "创建索引版本或运行正式评测后，这里会保留记录。" }}/>
        {selectedOperation ? <OperationDetailDialog
          operation={selectedOperation}
          build={indexBuilds.find((item) => item.operation_id === selectedOperation.operation_id) ?? null}
          buildDocuments={buildDocuments}
          buildLoading={buildDetailLoading}
          evaluationRun={selectedEvaluation}
          evaluationLoading={evaluationDetailLoading}
          busy={busy}
          onClose={() => { setSelectedOperation(null); setSelectedEvaluation(null); setBuildDocuments(null); }}
          onRetryEvaluation={(runId) => void retryEvaluation(runId)}
          onCancelEvaluation={(runId) => void cancelEvaluation(runId)}
          onOpenVersion={(versionId) => { setSelectedOperation(null); setSelectedVersionId(versionId); }}
          onOpenDocuments={() => { setSelectedOperation(null); setActiveTab("documents"); }}
          onDeleteDocument={(documentId) => { void (async () => { setBusy(true); setError(""); try { await api.deleteKnowledgeBaseDocument(id, documentId); setSelectedOperation(null); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "删除失效资料失败。"); } finally { setBusy(false); } })(); }}
        /> : null}
      </section> : null}
      {activeTab === "members" ? <section className="grid gap-3">{base.current_user_permission === "admin" ? <><p className="m-0 text-[12px] text-ink-faint">Deny 优先；未配置时继承知识库成员权限。ACL 更新后立即影响下一次检索。</p><h3 className="mt-2 mb-0 text-[13px] text-[#151a31]">数据源 ACL</h3><DataTable label="数据源 ACL" rows={dataSources} rowKey={(item) => item.data_source_id} columns={dataSourceAclColumns} emptyState={{ kind: "empty", title: "暂无数据源 ACL", description: "当前知识库没有独立数据源。" }}/><h3 className="mt-2 mb-0 text-[13px] text-[#151a31]">文档 ACL</h3><DataTable label="文档 ACL" rows={documents} rowKey={(item) => item.document_id} columns={documentAclColumns} emptyState={{ kind: "empty", title: "暂无文档 ACL", description: "当前知识库没有资料。" }}/></> : <p className="text-md text-[#737c90] leading-[1.6]">你拥有该知识库的使用权限；ACL 策略仅管理员可见。</p>}</section> : null}
      {activeTab === "conversations" ? <DataTable label="会话列表" rows={conversations} rowKey={(item) => item.conversation_id} columns={conversationColumns} emptyState={{ kind: "empty", title: "还没有会话", description: "在此知识库发起问答后，会话将显示在这里。" }}/> : null}
    </Tabs>
  </> : null}{ragPolicyOpen && ragPolicyDraft ? <Dialog open size="md" title="RAG 策略" description="选择受控管线发布阶段，并限制 Web 检索范围。" onClose={() => { if (!savingRagPolicy) setRagPolicyOpen(false); }}><form className="grid gap-4" onSubmit={(event) => void saveRagPolicy(event)}>{ragPolicyError ? <ErrorBanner>{ragPolicyError}</ErrorBanner> : null}<label className="grid gap-2 text-sm text-ink-muted">发布阶段<Select value={ragPolicyDraft.rollout_stage} onChange={(event) => setRagPolicyDraft((current) => current ? { ...current, rollout_stage: event.target.value as RAGPolicy["rollout_stage"] } : current)}><option value="shadow">Shadow · 只记录路由，执行当前默认管线</option><option value="canary">Canary · 当前知识库执行差异管线</option><option value="full">Full · 正式启用受控模块编排</option></Select></label><Checkbox label="启用受控 Web 检索" showLabel checked={ragPolicyDraft.web_search_enabled} onCheckedChange={(checked) => setRagPolicyDraft((current) => current ? { ...current, web_search_enabled: checked } : current)}/><label className="grid gap-2 text-sm text-ink-muted">可信域名白名单<Input value={ragPolicyDraft.allowed_domains.join(", ")} placeholder="docs.example.com, support.example.com" onChange={(event) => setRagPolicyDraft((current) => current ? { ...current, allowed_domains: event.target.value.split(/[，,]/) } : current)}/><small className="text-ink-faint">只填写域名；Web 检索仅访问这些域及其子域。</small></label><div className="grid grid-cols-3 gap-3 max-sm:grid-cols-1"><label className="grid gap-2 text-sm text-ink-muted">意图置信度<Input type="number" min={0.5} max={1} step={0.05} value={ragPolicyDraft.intent_confidence_threshold} onChange={(event) => setRagPolicyDraft((current) => current ? { ...current, intent_confidence_threshold: Number(event.target.value) } : current)}/></label><label className="grid gap-2 text-sm text-ink-muted">最少证据<Input type="number" min={1} max={10} value={ragPolicyDraft.minimum_evidence_count} onChange={(event) => setRagPolicyDraft((current) => current ? { ...current, minimum_evidence_count: Number(event.target.value) } : current)}/></label><label className="grid gap-2 text-sm text-ink-muted">Web 结果上限<Input type="number" min={1} max={5} value={ragPolicyDraft.max_web_results} onChange={(event) => setRagPolicyDraft((current) => current ? { ...current, max_web_results: Number(event.target.value) } : current)}/></label></div><div className="rounded-md border border-divider bg-canvas p-3 text-xs leading-6 text-ink-faint">管线顺序由系统内置 Profile 管理，管理员不能自由增删或重新排序模块。Web 内容不会自动写入知识库。</div><DialogActions><Button variant="secondary" loading={savingRagPolicy} onClick={() => setRagPolicyOpen(false)}>取消</Button><Button type="submit" loading={savingRagPolicy}>保存策略</Button></DialogActions></form></Dialog> : null}{editingBase && base ? <Dialog open title="编辑知识库" description="修改知识库名称和描述，保存后立即生效。" onClose={closeBaseEditor} returnFocusRef={baseEditTriggerRef}>{baseEditError ? <ErrorBanner>{baseEditError}</ErrorBanner> : null}<KnowledgeBaseForm name={baseNameDraft} description={baseDescriptionDraft} busy={savingBase} submitText="保存" onName={(value) => { setBaseNameDraft(value); setBaseEditError(""); }} onDescription={(value) => { setBaseDescriptionDraft(value); setBaseEditError(""); }} onCancel={closeBaseEditor} onSubmit={saveBase}/></Dialog> : null}{selectedVersionId ? <IndexVersionDetailDialog
    open
    knowledgeBaseId={id}
    versionId={selectedVersionId}
    onClose={() => {
      setSelectedVersionId(null);
      // 深链进来时地址停在 /index-versions/{v}，关掉弹框却不改地址的话，刷新会再次
      // 打开它，而用户以为自己已经关掉了。
      if (window.location.pathname.includes("/index-versions/")) onOpen(`/knowledge-bases/${id}`);
    }}
    onActionComplete={() => void load()}
    onOpen={onOpen}
  /> : null}{deletingCategory ? <Dialog open title="删除分类" onClose={() => { if (!busy) setDeletingCategory(null); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<div className="p-[20px_22px] text-[#626b7f] text-[14px] leading-[1.7]">{deletingCategory.document_count > 0 ? <>「{deletingCategory.name}」下还有 <strong className="text-[#242c40]">{deletingCategory.document_count} 份资料</strong>。<p>删除分类<strong className="text-[#242c40]">不会删除资料</strong>，它们会变成「无分类」，仍然可以被检索，之后可以重新分类。</p></> : <>确认删除分类「{deletingCategory.name}」吗？</>}</div><DialogActions><Button variant="secondary" loading={busy} onClick={() => setDeletingCategory(null)}>取消</Button><Button variant="destructive" loading={busy} onClick={() => void deleteCategory(deletingCategory)}>仍要删除</Button></DialogActions></Dialog> : null}{categoryForm ? <Dialog open title={categoryForm.mode === "create" ? "新建分类" : "编辑分类"} description={categoryForm.mode === "create" ? "分类可随时改名、停用或删除" : "修改后立即用于资料筛选"} onClose={() => { if (!busy) setCategoryForm(null); }}><form className="grid gap-3.5" onSubmit={(event) => { event.preventDefault(); void saveCategory(); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<label className="grid gap-[7px] text-[12px] text-ink-muted">名称<Input className="min-h-[40px]" value={categoryDraft.name} maxLength={64} autoFocus onChange={(event) => { setCategoryDraft((current) => ({ ...current, name: event.target.value })); setError(""); }}/></label><label className="grid gap-[7px] text-[12px] text-ink-muted">描述<textarea value={categoryDraft.description} maxLength={300} rows={3} onChange={(event) => setCategoryDraft((current) => ({ ...current, description: event.target.value }))}/></label><label className="grid gap-[7px] text-[12px] text-ink-muted">排序<Input className="min-h-[40px]" type="number" min={0} max={10000} value={categoryDraft.sort_order} onChange={(event) => setCategoryDraft((current) => ({ ...current, sort_order: Number(event.target.value) }))}/></label><DialogActions><Button variant="secondary" loading={busy} onClick={() => setCategoryForm(null)}>取消</Button><Button type="submit" loading={busy}>{categoryForm.mode === "create" ? "创建" : "保存"}</Button></DialogActions></form></Dialog> : null}{aclTarget ? <Dialog open size="md" title="配置 ACL" description={`${aclTarget.name} · 当前版本 ${aclTarget.version}`} onClose={() => { if (!savingAcl) setAclTarget(null); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<div className="grid max-h-[360px] overflow-y-auto border-t border-line">{members.length ? members.map((member) => <label className="flex min-h-14 items-center justify-between gap-4 border-b border-divider" key={member.user_id}><span className="grid gap-0.5"><strong>{member.display_name}</strong><small className="text-sm text-ink-faint">{member.username}</small></span><Select size="sm" className="w-28" aria-label={`${member.display_name} ACL`} value={aclDraft[member.user_id] || "inherit"} onChange={(event) => setAclDraft((current) => ({ ...current, [member.user_id]: event.target.value as "inherit" | "allow" | "deny" }))}><option value="inherit">继承</option><option value="allow">Allow</option><option value="deny">Deny</option></Select></label>) : <p className="text-md text-[#737c90] leading-[1.6]">知识库尚未授权成员，无需配置细粒度 ACL。</p>}</div><DialogActions><Button variant="secondary" loading={savingAcl} onClick={() => setAclTarget(null)}>取消</Button><Button loading={savingAcl} blockedReason={members.length ? undefined : "知识库尚未授权成员"} onClick={() => void saveAcl()}>保存并立即生效</Button></DialogActions></Dialog> : null}{conversationConfirmDialog}</section>;
}
