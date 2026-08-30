import type {
  AcceptanceRun,
  CategoryTemplate,
  CategoryTemplateItem,
  ApiErrorPayload,
  AuthToken,
  DocumentInfo,
  DocumentCategory,
  DocumentVersion,
  ParsingPreview,
  EvaluationReport,
  EvaluationReportSummary,
  AnswerEvaluationReport,
  AnswerEvaluationSummary,
  EvaluationCenterOverview,
  GovernedBadCase,
  IndexVersion,
  PipelineEvaluation,
  ConversationDetail,
  ConversationSummary,
  KnowledgeBase,
  QueryResult,
  User,
  HealthStatus,
  ReadinessStatus,
  SystemMetrics,
  AuditEvent,
  DataSource,
  SyncRun,
  Citation,
} from "./types";

let accessToken: string | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function hasAccessToken() {
  return accessToken !== null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let requestInit = init;
  if (accessToken) {
    const headers = new Headers(init?.headers);
    headers.set("Authorization", `Bearer ${accessToken}`);
    requestInit = { ...init, headers };
  }
  const response = await fetch(url, requestInit);
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as ApiErrorPayload;
    if (response.status === 401 && !url.startsWith("/api/auth/")) {
      setAccessToken(null);
      window.dispatchEvent(new Event("rag-auth-expired"));
    }
    throw new Error(payload.error?.message ?? `请求失败（${response.status}）`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  getBootstrapStatus: () => request<{ required: boolean }>("/api/auth/bootstrap"),
  bootstrap: (username: string, password: string, displayName: string) =>
    request<AuthToken>("/api/auth/bootstrap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, display_name: displayName }),
    }),
  login: (username: string, password: string) =>
    request<AuthToken>("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  me: () => request<User>("/api/auth/me"),
  health: () => request<HealthStatus>("/api/health"),
  readiness: () => request<ReadinessStatus>("/api/health/ready"),
  systemMetrics: () => request<SystemMetrics>("/api/system/metrics"),
  listMembers: () => request<User[]>("/api/members?offset=0&limit=100"),
  createMember: (username: string, displayName: string, password: string, role: User["role"]) =>
    request<User>("/api/members", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, display_name: displayName, password, role }) }),
  updateMember: (userId: string, payload: Partial<Pick<User, "display_name" | "role" | "active">> & { password?: string }) =>
    request<User>(`/api/members/${userId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  listKnowledgeBaseMembers: (id: string) => request<User[]>(`/api/knowledge-bases/${id}/members?offset=0&limit=100`),
  grantKnowledgeBaseMember: (id: string, userId: string) => request<void>(`/api/knowledge-bases/${id}/members/${userId}`, { method: "PUT" }),
  revokeKnowledgeBaseMember: (id: string, userId: string) => request<void>(`/api/knowledge-bases/${id}/members/${userId}`, { method: "DELETE" }),
  listAuditEvents: (result = "", action = "") => {
    const params = new URLSearchParams({ offset: "0", limit: "100" });
    if (result) params.set("result", result);
    if (action) params.set("action", action);
    return request<AuditEvent[]>(`/api/audit/events?${params}`);
  },
  listDocuments: () => request<DocumentInfo[]>("/api/documents"),
  uploadDocument: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<DocumentInfo>("/api/documents", { method: "POST", body });
  },
  deleteDocument: (id: string) => request<void>(`/api/documents/${id}`, { method: "DELETE" }),
  query: (question: string, retrieveK = 10, rerankK = 5) =>
    request<QueryResult>("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, retrieve_k: retrieveK, rerank_k: rerankK }),
    }),
  listEvaluations: () => request<EvaluationReportSummary[]>("/api/evaluations"),
  getEvaluation: (reportId: string) =>
    request<EvaluationReport>(`/api/evaluations/${encodeURIComponent(reportId)}`),
  listKnowledgeBases: (options: { name?: string; status?: string; sort?: "updated_desc" | "updated_asc"; offset?: number; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (options.name) params.set("name", options.name);
    if (options.status) params.set("status", options.status);
    if (options.sort && options.sort !== "updated_desc") params.set("sort", options.sort);
    if (options.offset !== undefined) params.set("offset", String(options.offset));
    if (options.limit !== undefined) params.set("limit", String(options.limit));
    const query = params.toString();
    return request<KnowledgeBase[]>(`/api/knowledge-bases${query ? `?${query}` : ""}`);
  },
  getKnowledgeBase: (id: string) => request<KnowledgeBase>(`/api/knowledge-bases/${id}`),
  createKnowledgeBase: (name: string, description: string, applyDefaultCategoryTemplate = true) =>
    request<KnowledgeBase>("/api/knowledge-bases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description, apply_default_category_template: applyDefaultCategoryTemplate }),
    }),
  getDefaultCategoryTemplate: () => request<CategoryTemplate>("/api/category-templates/default"),
  createDefaultCategoryTemplateItem: (payload: { name: string; description: string; sort_order: number }) =>
    request<CategoryTemplateItem>("/api/category-templates/default/items", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  updateDefaultCategoryTemplateItem: (id: string, payload: { name: string; description: string; sort_order: number; active: boolean }) =>
    request<CategoryTemplateItem>(`/api/category-templates/default/items/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  deleteDefaultCategoryTemplateItem: (id: string) => request<void>(`/api/category-templates/default/items/${id}`, { method: "DELETE" }),
  updateKnowledgeBase: (id: string, name: string, description: string) =>
    request<KnowledgeBase>(`/api/knowledge-bases/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description }),
    }),
  deleteKnowledgeBase: (id: string) => request<void>(`/api/knowledge-bases/${id}`, { method: "DELETE" }),
  listDataSources: (offset = 0, limit = 20) => request<DataSource[]>(`/api/data-sources?offset=${offset}&limit=${limit}`),
  createDataSource: (knowledgeBaseId: string, payload: { name: string; source_type: "local_directory" | "object_storage"; configuration: Record<string, unknown>; default_category_id?: string | null; metadata_defaults?: Record<string, unknown> }) =>
    request<{ data_source_id: string }>(`/api/knowledge-bases/${knowledgeBaseId}/data-sources`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  updateDataSource: (id: string, payload: { name: string; configuration: Record<string, unknown>; default_category_id?: string | null; metadata_defaults?: Record<string, unknown> }) =>
    request<void>(`/api/data-sources/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  testDataSource: (id: string) => request<{ ok: boolean; discovered_count: number; message: string }>(`/api/data-sources/${id}/test`, { method: "POST" }),
  syncDataSource: (id: string) => request<{ index_job_id: string; sync_run_id: string; data_source_id: string }>(`/api/data-sources/${id}/sync`, { method: "POST" }),
  retryDataSource: (id: string) => request<{ index_job_id: string; sync_run_id: string; data_source_id: string }>(`/api/data-sources/${id}/retry`, { method: "POST" }),
  listDataSourceSyncRuns: (id: string) => request<SyncRun[]>(`/api/data-sources/${id}/sync-runs?limit=50`),
  setDataSourceEnabled: (id: string, enabled: boolean) => request<void>(`/api/data-sources/${id}/enabled?enabled=${enabled}`, { method: "PUT" }),
  deleteDataSource: (id: string) => request<void>(`/api/data-sources/${id}`, { method: "DELETE" }),
  listKnowledgeBaseDocuments: (id: string) => request<DocumentInfo[]>(`/api/knowledge-bases/${id}/documents`),
  listKnowledgeBaseCategories: (id: string) => request<DocumentCategory[]>(`/api/knowledge-bases/${id}/categories`),
  createKnowledgeBaseCategory: (id: string, payload: { name: string; description: string; sort_order: number }) =>
    request<DocumentCategory>(`/api/knowledge-bases/${id}/categories`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  updateKnowledgeBaseCategory: (id: string, categoryId: string, payload: { name: string; description: string; sort_order: number; active: boolean }) =>
    request<DocumentCategory>(`/api/knowledge-bases/${id}/categories/${categoryId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  deleteKnowledgeBaseCategory: (id: string, categoryId: string) =>
    request<void>(`/api/knowledge-bases/${id}/categories/${categoryId}`, { method: "DELETE" }),
  batchAssignDocumentCategory: (id: string, documentIds: string[], categoryId: string) =>
    request<{ updated: number }>(`/api/knowledge-bases/${id}/documents/categories`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ document_ids: documentIds, category_id: categoryId }) }),
  listKnowledgeBaseDocumentVersions: (id: string) => request<DocumentVersion[]>(`/api/knowledge-bases/${id}/document-versions?offset=0&limit=100`),
  listKnowledgeBaseIndexVersions: (id: string) => request<IndexVersion[]>(`/api/knowledge-bases/${id}/index-versions`),
  getDocumentParsingPreview: (id: string, versionId: string) => request<ParsingPreview>(`/api/knowledge-bases/${id}/document-versions/${versionId}/parsing`),
  getCitation: (knowledgeBaseId: string, chunkId: string) => request<Citation>(`/api/knowledge-bases/${knowledgeBaseId}/citations/${encodeURIComponent(chunkId)}`),
  reprocessDocumentVersion: (id: string, versionId: string, chunkSize: number, chunkOverlap: number) => request<{ index_job_id: string }>(`/api/knowledge-bases/${id}/document-versions/${versionId}/reprocess`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ chunk_size: chunkSize, chunk_overlap: chunkOverlap }) }),
  uploadKnowledgeBaseDocument: (id: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<DocumentInfo>(`/api/knowledge-bases/${id}/documents`, { method: "POST", body });
  },
  deleteKnowledgeBaseDocument: (knowledgeBaseId: string, documentId: string) =>
    request<void>(`/api/knowledge-bases/${knowledgeBaseId}/documents/${documentId}`, { method: "DELETE" }),
  updateKnowledgeBaseDocumentMetadata: (
    knowledgeBaseId: string,
    documentId: string,
    metadata: { category: string; tags: string[] },
  ) => request<DocumentInfo>(
    `/api/knowledge-bases/${knowledgeBaseId}/documents/${documentId}/metadata`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(metadata) },
  ),
  updateKnowledgeBaseDocumentAcl: (
    knowledgeBaseId: string,
    documentId: string,
    policy: { allow_user_ids: string[]; deny_user_ids: string[] },
  ) => request<{ version: number; allow_user_ids: string[]; deny_user_ids: string[] }>(
    `/api/knowledge-bases/${knowledgeBaseId}/documents/${documentId}/acl`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(policy) },
  ),
  updateDataSourceAcl: (
    dataSourceId: string,
    policy: { allow_user_ids: string[]; deny_user_ids: string[] },
  ) => request<{ version: number; allow_user_ids: string[]; deny_user_ids: string[] }>(
    `/api/data-sources/${dataSourceId}/acl`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(policy) },
  ),
  queryKnowledgeBase: (
    id: string,
    question: string,
    conversationId?: string,
    filters?: { category_ids?: string[]; categories?: string[]; tags?: string[]; source_types?: string[] },
  ) =>
    request<QueryResult>(`/api/knowledge-bases/${id}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, retrieve_k: 10, rerank_k: 5, conversation_id: conversationId || null, ...(filters ? { filters } : {}) }),
    }),
  listConversations: (id: string) =>
    request<ConversationSummary[]>(`/api/knowledge-bases/${id}/conversations`),
  getConversation: (knowledgeBaseId: string, conversationId: string) =>
    request<ConversationDetail>(`/api/knowledge-bases/${knowledgeBaseId}/conversations/${conversationId}`),
  deleteConversation: (knowledgeBaseId: string, conversationId: string) =>
    request<void>(`/api/knowledge-bases/${knowledgeBaseId}/conversations/${conversationId}`, { method: "DELETE" }),
  listAnswerEvaluations: () => request<AnswerEvaluationSummary[]>("/api/evaluations/answers/reports"),
  getAnswerEvaluation: (reportId: string) =>
    request<AnswerEvaluationReport>(`/api/evaluations/answers/reports/${encodeURIComponent(reportId)}`),
  getEvaluationCenterOverview: () => request<EvaluationCenterOverview>("/api/evaluation-center/overview"),
  getPipelineEvaluation: (knowledgeBaseId?: string) => request<PipelineEvaluation>(
    `/api/evaluation-center/pipeline${knowledgeBaseId ? `?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}` : ""}`,
  ),
  listGovernedBadCases: (filters?: { knowledge_base_id?: string; status?: string; severity?: string; failure_stage?: string }) => {
    const query = new URLSearchParams(Object.entries(filters ?? {}).filter((entry): entry is [string, string] => Boolean(entry[1])));
    return request<GovernedBadCase[]>(`/api/evaluation-center/bad-cases${query.size ? `?${query}` : ""}`);
  },
  updateGovernedBadCase: (caseId: string, update: { status: GovernedBadCase["status"]; severity?: GovernedBadCase["severity"]; root_cause?: string; assignee?: string; fix_commit?: string; regression_passed?: boolean }) => request<GovernedBadCase>(
    `/api/evaluation-center/bad-cases/${encodeURIComponent(caseId)}`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(update) },
  ),
  listAcceptanceRuns: (knowledgeBaseId?: string) => request<AcceptanceRun[]>(`/api/evaluation-center/acceptance-runs${knowledgeBaseId ? `?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}` : ""}`),
  startAcceptanceRun: (knowledgeBaseId: string) => request<AcceptanceRun>("/api/evaluation-center/acceptance-runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ knowledge_base_id: knowledgeBaseId }) }),
};
