export interface DocumentInfo {
  knowledge_base_id: string;
  document_id: string;
  filename: string;
  chunk_count: number;
  status: string;
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
}

export interface ApiErrorPayload {
  error?: { code?: string; message?: string };
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
