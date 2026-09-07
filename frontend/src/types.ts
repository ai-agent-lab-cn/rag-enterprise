export interface DocumentInfo {
  knowledge_base_id: string;
  document_id: string;
  data_source_id?: string | null;
  filename: string;
  chunk_count: number;
  status: string;
  index_failure_reason?: string | null;
  /** null 表示没有分类。它与 classification_status 是两回事：没有分类不等于分类失败。 */
  category: string | null;
  category_id: string | null;
  tags: string[];
  source_type: string;
  created_at: string | null;
  source_system: string;
  external_resource_id: string | null;
  owner_user_id: string | null;
  department: string | null;
  sensitivity: "public" | "internal" | "confidential" | "restricted";
  valid_from: string | null;
  valid_to: string | null;
  retrieval_status: "searchable" | "expired" | "deleted";
  acl_version: number;
  allow_user_ids: string[];
  deny_user_ids: string[];
  classification_status: "pending" | "auto_assigned" | "review_required" | "manual" | "failed";
  classification_confidence: number | null;
  suggested_category_id: string | null;
  classification_model: string | null;
  classified_at: string | null;
  classification_failure_code: ClassificationFailureCode | null;
  classification_failure_reason: string | null;
  classification_failed_at: string | null;
  classification_retry_count: number;
  classification_next_retry_at: string | null;
}

/** 前三个可自动重试，后四个是配置或响应本身有问题，必须由人介入。 */
export type ClassificationFailureCode =
  | "MODEL_UNAVAILABLE"
  | "MODEL_TIMEOUT"
  | "UNKNOWN_ERROR"
  | "INVALID_RESPONSE"
  | "CATEGORY_NOT_FOUND"
  | "CATEGORY_INACTIVE"
  | "NO_ACTIVE_CATEGORY";

export interface DocumentCategory {
  category_id: string;
  knowledge_base_id: string;
  name: string;
  description: string;
  sort_order: number;
  active: boolean;
  is_system: boolean;
  origin_type: "template_copy" | "manual" | "migration";
  document_count: number;
  created_at: string;
  updated_at: string;
}

export interface CategoryTemplateItem {
  template_item_id: string;
  template_id: string;
  name: string;
  description: string;
  sort_order: number;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface CategoryTemplate {
  template_id: string;
  name: string;
  description: string;
  active: boolean;
  item_count: number;
  items: CategoryTemplateItem[];
  created_at: string | null;
  updated_at: string;
}

export interface DocumentVersion {
  document_version_id: string; document_id: string; filename: string; version_number: number;
  content_sha256: string; source_file_bytes: number; source_type: string;
  status: "pending" | "indexing" | "ready" | "failed" | "superseded";
  failure_reason: string | null; created_at: string; indexed_at: string | null; is_current: boolean;
  parser_name: string | null; parser_version: string | null; chunking_version: string | null;
  processing_options: Record<string, unknown>;
  parse_status: "pending" | "parsing" | "chunking" | "ready" | "failed";
  parse_failure_code: string | null; node_count: number; parsed_chunk_count: number;
}

export interface ParsingLocation {
  page_number?: number | null; heading_path?: string[]; paragraph_index?: number | null;
  sheet_name?: string | null; row_start?: number | null; row_end?: number | null;
  column_start?: number | null; column_end?: number | null; source_url?: string | null;
}

export interface ParsingNode {
  node_id: string; node_type: string; text: string; level: number;
  location: ParsingLocation; children: ParsingNode[];
}

export interface ParsingChunk {
  chunk_id: string; chunk_index: number; content: string;
  metadata: Record<string, unknown> & { node_id?: string; heading_path?: string[]; page?: number; sheet_name?: string; row_start?: number; row_end?: number };
}

export interface ParsingPreview {
  document_version_id: string; document_id: string; filename: string; version_number: number;
  status: DocumentVersion["status"]; parse_status: DocumentVersion["parse_status"];
  parse_failure_code: string | null; failure_reason: string | null;
  parser_name: string | null; parser_version: string | null; chunking_version: string | null;
  processing_options: Record<string, unknown>; is_current: boolean;
  tree: ParsingNode[]; chunks: ParsingChunk[];
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
  // V5 之前保存的历史回答没有这两个字段，缺失表示通路未知，不得当作向量召回展示。
  retrieval_channels?: string[];
  lexical_score?: number | null;
  retrieval_methods?: Array<"vector" | "lexical">;
  query_match_count?: number;
  document_version_id?: string | null;
  content_sha256?: string | null;
  heading_path?: string[];
  sheet_name?: string | null;
  row_start?: number | null;
  row_end?: number | null;
  column_start?: number | null;
  column_end?: number | null;
  source_url?: string | null;
  external_resource_id?: string | null;
}

export interface Citation extends Source {
  document_version_id: string;
  content_sha256: string;
}

export interface GenerationGovernance {
  minimum_evidence_count: number; evidence_count: number;
  acl_revalidated: boolean; current_version_revalidated: boolean; retrieval_status_revalidated: boolean;
  citation_indices: number[]; citation_valid: boolean; claim_citation_coverage: boolean;
  outcome_reason: string | null;
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
  generation_governance?: GenerationGovernance | null;
  query_metadata?: {
    strategy: "original" | "normalized" | "controlled_expansion";
    query_count: number;
    expansion_count: number;
    fallback_used: boolean;
    retrieved_candidate_count: number;
    fused_candidate_count: number;
    returned_source_count: number;
    filter_match_count: number | null;
    uncategorized_candidate_count?: number;
    applied_filters?: {
      category_ids: string[]; categories: string[]; tags: string[]; source_types: string[];
      created_from: string | null; created_to: string | null;
    } | null;
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

export type GenerationProvider = "deepseek" | "gemini" | "kimi";
export type GenerationModelStatus =
  | "unconfigured"
  | "available"
  | "region_unsupported"
  | "quota_exhausted"
  | "auth_failed"
  | "rate_limited"
  | "timeout"
  | "model_not_found"
  | "unavailable";

export interface GenerationModelItem {
  provider: GenerationProvider;
  display_name: string;
  model_name: string;
  configured: boolean;
  active: boolean;
  status: GenerationModelStatus;
  status_code: string | null;
  status_message: string;
  checked_at: string | null;
  balance_status: "unknown" | "available" | "unsupported" | "error";
  balance_amount: number | null;
  balance_currency: string | null;
  balance_limit: number | null;
  balance_percent: number | null;
  balance_checked_at: string | null;
}

export interface GenerationModels {
  active_provider: GenerationProvider;
  items: GenerationModelItem[];
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
  config_fingerprint?: string | null;
}

export interface EvaluationReport extends EvaluationReportSummary {
  parameters: Record<string, string | number | boolean>;
  query_count: number;
  recall_at_5: EvaluationMetric;
  recall_at_10?: EvaluationMetric | null;
  vector_mrr: EvaluationMetric;
  rerank_mrr: EvaluationMetric;
  hybrid_mrr?: EvaluationMetric | null;
  rerank_recall_at_5?: EvaluationMetric | null;
  ndcg_at_5?: EvaluationMetric | null;
  ndcg_at_10?: EvaluationMetric | null;
  metadata_filter_accuracy?: EvaluationMetric | null;
  acl_leak_count?: number | null;
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
  index_status: "empty" | "processing" | "ready" | "degraded" | "failed";
  /** 当前生效的索引不是用现在的配置建的；逐项列出差异，空数组表示没有漂移。 */
  index_config_drift: Array<{ field: string; active: unknown; current: unknown }>;
  current_user_permission: "admin" | "use";
  allowed_actions: Array<"detail" | "edit" | "delete">;
}

export interface DataSource {
  data_source_id: string; name: string; source_type: "file" | "local_directory" | "object_storage" | "web" | "connector";
  knowledge_base_id: string; knowledge_base_name: string; enabled: boolean;
  sync_enabled?: boolean; retrieval_enabled?: boolean;
  upload_status: "idle" | "succeeded";
  index_status: "idle" | "queued" | "running" | "succeeded" | "failed";
  /** @deprecated 使用 index_status。 */
  sync_status: "idle" | "queued" | "running" | "succeeded" | "failed" | "aborted";
  sync_progress_percent?: number;
  sync_current_stage?: string | null;
  configuration?: Record<string, unknown>;
  default_category_id?: string | null;
  metadata_defaults?: Record<string, unknown>;
  document_count: number; source_file_bytes: number; last_indexed_at: string | null; last_synced_at: string | null;
  failure_reason: string | null; updated_at: string;
  acl_version: number; allow_user_ids: string[]; deny_user_ids: string[];
  allowed_actions: Array<"detail" | "edit" | "disable" | "enable" | "disable_retrieval" | "enable_retrieval" | "update_file" | "delete" | "test" | "sync">;
}

export interface SyncRun {
  sync_run_id: string;
  data_source_id: string;
  status: "queued" | "discovering" | "syncing" | "indexing" | "succeeded" | "partial_failed" | "aborted" | "failed";
  stage: string;
  added_count: number;
  updated_count: number;
  deleted_count: number;
  skipped_count: number;
  failed_count: number;
  retry_count: number;
  operation_id: string | null;
  input_cursor: string | null;
  discovered_cursor: string | null;
  committed_cursor: string | null;
  total_count: number;
  completed_count: number;
  processing_count: number;
  dead_letter_count: number;
  error_code: string | null;
  failure_reason: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SyncResourceRun {
  sync_resource_run_id: string; sync_run_id: string; external_resource_id: string;
  operation: string; status: string; current_stage: string;
  document_id: string | null; document_version_id: string | null; index_build_id: string | null;
  attempt_count: number; max_attempts: number; error_code: string | null; error_message: string | null;
  started_at: string | null; finished_at: string | null; created_at: string; updated_at: string;
}

export interface GovernedOperation {
  operation_id: string; operation_type: string; knowledge_base_id: string;
  data_source_id: string | null; document_id: string | null; document_version_id: string | null;
  status: string; current_stage: string; progress_mode: string; progress_percent: number | null;
  total_count: number; completed_count: number; processing_count: number; failed_count: number;
  error_code: string | null; error_message: string | null;
  started_at: string | null; finished_at: string | null; created_at: string; updated_at: string;
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
  answer_status?: QueryResult["answer_status"] | null;
  generation_governance?: GenerationGovernance | null;
  query_metadata?: {
    strategy: "original" | "normalized" | "controlled_expansion";
    query_count: number;
    expansion_count: number;
    fallback_used: boolean;
    retrieved_candidate_count: number;
    fused_candidate_count: number;
    returned_source_count: number;
    filter_match_count: number | null;
    uncategorized_candidate_count?: number;
    applied_filters?: {
      category_ids: string[]; categories: string[]; tags: string[]; source_types: string[];
      created_from: string | null; created_to: string | null;
    } | null;
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

export interface EvaluationCenterOverview {
  passed: boolean;
  report_count: number;
  retrieval_report: EvaluationReportSummary | null;
  answer_report: AnswerEvaluationSummary | null;
}

export interface PipelineEvaluation {
  run_count: number;
  added_count: number;
  updated_count: number;
  deleted_count: number;
  skipped_count: number;
  failed_count: number;
  retry_count: number;
  failure_rate: number;
  average_duration_ms: number;
}

export interface GovernedBadCase {
  case_id: string;
  source_type: "online" | "evaluation" | "manual";
  source_record_id: string;
  knowledge_base_id: string;
  dataset_version: string | null;
  question: string;
  expected_source_ids: string[];
  actual_source_ids: string[];
  expected_answer_status: string | null;
  actual_answer_status: string | null;
  actual_answer: string | null;
  failure_stage: string;
  root_cause: string | null;
  category: string;
  severity: "low" | "medium" | "high" | "critical";
  assignee: string | null;
  fix_commit: string | null;
  status: "new" | "confirmed" | "fixing" | "resolved" | "regression_added" | "ignored";
  regression_added: boolean;
  created_at: string;
  confirmed_at: string | null;
  resolved_at: string | null;
  updated_at: string;
}

export interface AcceptanceStep {
  step_key: string;
  title: string;
  status: "passed" | "failed" | "blocked";
  summary: string;
  evidence: Record<string, unknown>;
}

export interface AcceptanceRun {
  acceptance_run_id: string;
  knowledge_base_id: string | null;
  status: "passed" | "failed" | "blocked";
  commit_sha: string;
  schema_version: number;
  steps: AcceptanceStep[];
  limitations: string[];
  created_by: string | null;
  created_at: string;
}

export interface IndexVersion {
  index_version_id: string;
  /** 与后端 index_versions_status_check 同一套取值。ready 只由三层门禁通过产生。 */
  status:
    | "building" | "validating" | "ready" | "active" | "previous"
    | "retired" | "cleaned" | "build_failed" | "validation_failed";
  chunking_version: string;
  parser_version: string;
  embedding_model: string;
  embedding_dimension: number;
  processing_options: Record<string, unknown>;
  config_fingerprint: string;
  evaluation_report_id: string | null;
  validation_report_id: string | null;
  document_snapshot_id: string | null;
  rebuild_batch_id: string | null;
  version_no: number | null;
  creation_reason: IndexVersionCreationReason | "legacy";
  force_reason: string | null;
  requested_by: string | null;
  config_snapshot: Record<string, unknown>;
  component_manifest: Record<string, unknown>;
  release_fingerprint: string | null;
  config_completeness: "complete" | "unknown";
  legacy_migrated: boolean;
  created_at: string;
  activated_at: string | null;
  retired_at: string | null;
  cleaned_at: string | null;
}

export type IndexVersionCreationReason =
  | "initial_build"
  | "config_changed"
  | "document_snapshot_changed"
  | "component_upgraded"
  | "consistency_repair"
  | "manual_rebuild";

export interface IndexDefinitionView {
  chunking: { version: string; chunk_size: number; chunk_overlap: number };
  parser: { schema_version: string };
  embedding: { model: string | null; dimension: number | null };
  components: Record<string, string>;
  processing_options: Record<string, unknown>;
  config_fingerprint: string | null;
  capabilities: Array<{ field: string; editable: boolean; value: unknown; source?: string; reason: string | null }>;
}

export interface IndexVersionCreationContext {
  scenario: "initial_build" | "candidate" | "no_change";
  definition: IndexDefinitionView;
  active_version: Record<string, unknown> | null;
  candidate_version: Record<string, unknown> | null;
  latest_document_snapshot: Record<string, unknown> | null;
  document_scope: {
    included: number;
    excluded: number;
    source_bytes: number;
    parse_failed: number;
    missing_current_revision: number;
  };
  document_exclusions: Array<{
    document_id: string;
    filename: string;
    reason: "parse_failed" | "missing_current_revision";
    latest_status: string;
    parse_failure_code: string | null;
  }>;
  build_capacity: {
    active_builds: number;
    max_concurrent_builds: number;
    remaining_build_slots: number;
    max_documents: number;
  };
  document_diff: { added: number; removed: number; updated: number; unchanged: number };
  document_set_fingerprint: string;
  config_changed: boolean;
  document_changed: boolean;
  creation_allowed: boolean;
  blocked_reasons: string[];
}

export interface IndexVersionCandidatePreview extends IndexVersionCreationContext {
  reason: IndexVersionCreationReason;
  force: boolean;
  force_reason: string | null;
  config_fingerprint: string | null;
  release_fingerprint: string | null;
  config_snapshot: Record<string, unknown>;
  component_manifest: Record<string, unknown>;
  config_diff: Array<{ field: string; active: unknown; candidate: unknown }>;
  estimated_documents: number;
  estimated_chunks: number;
  estimated_embedding_units: number;
}

export interface IndexVersionBuildResult {
  batch_id: string;
  index_version_id: string;
  index_build_id: string;
  knowledge_base_id: string;
  target_chunking_version: string;
  queued: number;
  reused: boolean;
}

export interface IndexVersionComparison {
  knowledge_base_id: string;
  target_version: Record<string, unknown>;
  baseline_version: Record<string, unknown> | null;
  config_diff: Array<{ field: string; baseline: unknown; target: unknown }>;
  document_diff: { added: number; removed: number; updated: number; unchanged: number };
  actual_scope: { documents: number; chunks: number };
  current_content: {
    document_set_fingerprint: string;
    diff: { added: number; removed: number; updated: number; unchanged: number };
    retrievable_documents: number;
    retrievable_chunks: number;
    requires_confirmation: boolean;
  };
  validation_comparison: { target: Record<string, unknown> | null; baseline: Record<string, unknown> | null };
  content_snapshot_note: string;
}

export interface IndexBuild {
  index_build_id: string; operation_id: string; index_version_id: string;
  attempt_no: number; build_type: string; status: string;
  total_documents: number; queued_documents: number; processing_documents: number;
  succeeded_documents: number; failed_documents: number;
  failure_code: string | null; failure_reason: string | null;
  progress_percent: number | null; current_stage: string;
  started_at: string | null; finished_at: string | null; created_at: string; updated_at: string;
}

export interface DocumentIndexState {
  index_build_id: string; index_version_id: string; document_id: string;
  document_version_id: string; filename: string; vector_status: string;
  keyword_status: string; metadata_status: string; overall_status: string;
  chunk_count: number; failure_stage: string | null; failure_code: string | null;
  failure_reason: string | null; updated_at: string;
}

/** 三层门禁里的单项检查。页面据此指出具体哪一项没过，而不是只显示「验证失败」。 */
export interface ValidationCheck {
  check_key: string;
  status: "pass" | "fail";
  expected: unknown;
  actual: unknown;
  severity: string;
  layer?: string;
}

export interface ValidationLayerResult {
  layer: string;
  /** unknown 表示这一层无法核对（例如快照机制之前创建的版本），不是通过。 */
  status: "pass" | "fail" | "unknown";
  checks: ValidationCheck[];
  note?: string;
  meets_frozen_thresholds?: boolean;
}

export interface ValidationReport {
  validation_report_id: string;
  index_version_id: string;
  index_build_id: string | null;
  status: "pending" | "running" | "pass" | "failed" | "cancelled";
  policy_version: string;
  evaluation_set_version: string | null;
  baseline_version_id: string | null;
  integrity_result: ValidationLayerResult | Record<string, never>;
  technical_result: ValidationLayerResult | Record<string, never>;
  retrieval_result: ValidationLayerResult | Record<string, never>;
  summary: string | null;
  failure_items: ValidationCheck[];
  report_source: "standard" | "legacy_backfill" | "bootstrap";
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

/** 索引版本的一次状态转换。actor_id 为空表示系统自动触发，不是「不知道是谁」。 */
export interface LifecycleEvent {
  event_id: string;
  index_version_id: string;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  actor_id: string | null;
  actor_role: string | null;
  reason: string | null;
  validation_report_id: string | null;
  created_at: string;
}
