import type {
  ApiErrorPayload,
  AuthToken,
  DocumentInfo,
  EvaluationReport,
  EvaluationReportSummary,
  AnswerEvaluationReport,
  AnswerEvaluationSummary,
  ConversationDetail,
  ConversationSummary,
  KnowledgeBase,
  QueryResult,
  User,
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
  listKnowledgeBases: () => request<KnowledgeBase[]>("/api/knowledge-bases"),
  getKnowledgeBase: (id: string) => request<KnowledgeBase>(`/api/knowledge-bases/${id}`),
  createKnowledgeBase: (name: string, description: string) =>
    request<KnowledgeBase>("/api/knowledge-bases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description }),
    }),
  updateKnowledgeBase: (id: string, name: string, description: string) =>
    request<KnowledgeBase>(`/api/knowledge-bases/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description }),
    }),
  deleteKnowledgeBase: (id: string) => request<void>(`/api/knowledge-bases/${id}`, { method: "DELETE" }),
  listKnowledgeBaseDocuments: (id: string) => request<DocumentInfo[]>(`/api/knowledge-bases/${id}/documents`),
  uploadKnowledgeBaseDocument: (id: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<DocumentInfo>(`/api/knowledge-bases/${id}/documents`, { method: "POST", body });
  },
  deleteKnowledgeBaseDocument: (knowledgeBaseId: string, documentId: string) =>
    request<void>(`/api/knowledge-bases/${knowledgeBaseId}/documents/${documentId}`, { method: "DELETE" }),
  queryKnowledgeBase: (id: string, question: string, conversationId?: string) =>
    request<QueryResult>(`/api/knowledge-bases/${id}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, retrieve_k: 10, rerank_k: 5, conversation_id: conversationId || null }),
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
};
