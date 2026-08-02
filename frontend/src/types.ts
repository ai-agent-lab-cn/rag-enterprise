export interface DocumentInfo {
  document_id: string;
  filename: string;
  chunk_count: number;
  status: string;
}

export interface Source {
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
