export interface DocumentInfo {
  knowledge_base_id: string;
  document_id: string;
  filename: string;
  chunk_count: number;
  status: string;
}

export interface DocumentVersion {
  document_version_id: string; document_id: string; filename: string; version_number: number;
  content_sha256: string; source_file_bytes: number; source_type: string;
  status: "pending" | "indexing" | "ready" | "failed" | "superseded";
  failure_reason: string | null; created_at: string; indexed_at: string | null; is_current: boolean;
}

export interface Source {
  knowledge_base_id: string;
  chunk_id: string;
  document_id: string;
  filename: string;
  page: number | null;
  paragraph: number;
  chunk_index: number;
  char_count: number;
  summary: string;
  text: string;
  retrieval_score: number;
  rerank_score: number;
  vector_score?: number | null;
  lexical_score?: number | null;
  retrieval_methods?: Array<"vector" | "lexical">;
  query_match_count?: number;
}

export interface QueryResult {
  answer: string;
  answer_status: "answered" | "insufficient_evidence" | "source_conflict" | "retrieval_only" | "generation_failed";
  error_code: string | null;
  error_message: string | null;
  sources: Source[];
  model: string;
  latency_ms: Record<string, number>;
  conversation_id: string | null;
  record_id: string | null;
  models: Record<string, string>;
  model_metadata: Record<string, string | number | boolean>;
  prompt_version: string | null;
  prompt_hash: string | null;
  query_metadata?: {
    strategy: "original" | "normalized" | "controlled_expansion";
    query_count: number;
    expansion_count: number;
    fallback_used: boolean;
  } | null;
}

export interface ApiErrorPayload {
  error?: { code?: string; message?: string };
}

export interface User {
  user_id: string;
  username: string;
  display_name: string;
  role: "admin" | "member";
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AuthToken {
  access_token: string;
  token_type: "bearer";
  expires_at: string;
  user: User;
}

export interface HealthStatus {
  status: string;
  version: string;
  collection_ready: boolean;
  generation_ready: boolean;
  models: Record<string, string>;
}

export interface ReadinessStatus {
  status: "ready" | "not_ready";
  checks: Record<string, "ok" | "failed">;
}

export interface SystemMetrics {
  generated_at: string;
  requests: Record<string, unknown>;
  rag: Record<string, number>;
  indexing: Record<string, number>;
}

export interface AuditEvent {
  event_id: string;
  occurred_at: string;
  action: string;
  actor_hash: string | null;
  actor_role: string | null;
  resource_type: string;
  resource_id: string | null;
  result: "success" | "denied" | "failed";
  request_id: string;
  metadata: Record<string, string | boolean | number>;
  previous_hash: string;
  event_hash: string;
}

export interface EvaluationMetric {
  value: number;
  threshold: number;
  baseline: number | null;
  passed: boolean;
  regressed: boolean;
}

export interface EvaluationReportSummary {
  report_id: string;
  dataset_id: string;
  dataset_version: string;
  commit: string;
  run_at: string;
  models: Record<string, string>;
  passed: boolean;
}

export interface EvaluationReport extends EvaluationReportSummary {
  parameters: Record<string, string | number | boolean>;
  query_count: number;
  recall_at_5: EvaluationMetric;
  vector_mrr: EvaluationMetric;
  rerank_mrr: EvaluationMetric;
  hybrid_mrr?: EvaluationMetric | null;
}

export interface KnowledgeBase {
  knowledge_base_id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  is_default: boolean;
  document_count: number;
  chunk_count: number;
  source_file_bytes: number;
  index_status: "empty" | "processing" | "ready" | "failed";
  current_user_permission: "admin" | "use";
  allowed_actions: Array<"detail" | "edit" | "delete">;
}

export interface DataSource {
  data_source_id: string; name: string; source_type: "file" | "object_storage" | "web" | "connector";
  knowledge_base_id: string; knowledge_base_name: string; enabled: boolean;
  upload_status: "idle" | "succeeded";
  index_status: "idle" | "queued" | "running" | "succeeded" | "failed";
  /** @deprecated 使用 index_status。 */
  sync_status: "idle" | "queued" | "running" | "succeeded" | "failed";
  document_count: number; source_file_bytes: number; last_indexed_at: string | null; last_synced_at: string | null;
  failure_reason: string | null; updated_at: string;
  allowed_actions: Array<"detail" | "edit" | "disable" | "enable" | "sync" | "delete">;
}

export interface ConversationSummary {
  conversation_id: string;
  knowledge_base_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  turn_count: number;
  last_status: string | null;
}

export interface AnswerRecord {
  record_id: string;
  conversation_id: string;
  knowledge_base_id: string;
  question: string;
  status: string;
  answer: string | null;
  sources: Source[];
  latency_ms: Record<string, number>;
  models: Record<string, string>;
  model_metadata: Record<string, string | number | boolean>;
  prompt_version: string | null;
  prompt_hash: string | null;
  query_metadata?: {
    strategy: "original" | "normalized" | "controlled_expansion";
    query_count: number;
    expansion_count: number;
    fallback_used: boolean;
  } | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
}

export interface ConversationDetail extends Omit<ConversationSummary, "turn_count" | "last_status"> {
  records: AnswerRecord[];
}

export interface AnswerEvaluationMetric extends EvaluationMetric {
  direction: "minimum" | "maximum";
}

export interface AnswerEvaluationSummary extends EvaluationReportSummary {
  prompt_version: string;
}

export interface AnswerEvaluationReport extends AnswerEvaluationSummary {
  prompt_hash: string;
  parameters: Record<string, string | number | boolean>;
  case_count: number;
  metrics: Record<string, AnswerEvaluationMetric | null>;
}
