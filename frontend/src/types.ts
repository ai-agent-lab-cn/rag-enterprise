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
  sources: Source[];
  model: string;
  latency_ms: Record<string, number>;
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
