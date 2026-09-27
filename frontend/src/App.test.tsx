import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { setAccessToken } from "./api";
import { describeWebExecution } from "./types";
import type { WebExecutionState } from "./types";
import App from "./App";

const base = {
  knowledge_base_id: "kb_default",
  name: "默认知识库",
  description: "V2 迁移资料",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-12T00:00:00Z",
  is_default: true,
  document_count: 1,
  chunk_count: 3,
  source_file_bytes: 2048,
  index_status: "ready",
  // 后端保证这个字段总存在（schemas.py:406 的 default_factory=list、main.py:2916 显式赋值），
  // 所以详情页直接读 base.index_config_drift.length 而不做兜底。这份手工 mock 漏掉它时
  // 页面会崩在那一行——CLAUDE.md 第三条说的手工字段映射，新增 schema 字段不报错、静默出错。
  index_config_drift: [],
  current_user_permission: "admin",
  allowed_actions: ["detail", "edit", "delete"],
};
const document = {
  knowledge_base_id: "kb_default",
  document_id: "doc_1",
  filename: "profile.md",
  chunk_count: 3,
  status: "ready",
  category: "安全",
  category_id: "cat_1234567890abcdef",
  tags: ["ACL"],
  source_type: "file",
  created_at: "2026-08-12T00:00:00Z",
  source_system: "upload",
  external_resource_id: null,
  owner_user_id: null,
  department: null,
  sensitivity: "internal",
  valid_from: null,
  valid_to: null,
  retrieval_status: "searchable",
  acl_version: 1,
  allow_user_ids: [],
  deny_user_ids: [],
  classification_status: "manual",
  classification_confidence: null,
  suggested_category_id: null,
  classification_model: null,
  classified_at: null,
};
const dataSource = {
  data_source_id: "src_1", name: "profile.md", source_type: "file",
  knowledge_base_id: "kb_default", knowledge_base_name: "默认知识库", enabled: true,
  upload_status: "succeeded", index_status: "succeeded",
  sync_status: "succeeded", document_count: 1, source_file_bytes: 2048,
  last_indexed_at: "2026-08-12T00:00:00Z", last_synced_at: "2026-08-12T00:00:00Z", failure_reason: null,
  updated_at: "2026-08-12T00:00:00Z", allowed_actions: ["detail", "edit", "disable", "update_file"],
  acl_version: 1, allow_user_ids: [], deny_user_ids: [],
};
const category = {
  category_id: "cat_1234567890abcdef", knowledge_base_id: "kb_default", name: "安全",
  description: "安全资料", sort_order: 100, active: true, is_system: false,
  origin_type: "migration" as const,
  document_count: 1, created_at: "2026-08-12T00:00:00Z", updated_at: "2026-08-12T00:00:00Z",
};
const categoryTemplate = {
  template_id: "category_template_default", name: "默认分类模板",
  description: "创建知识库时复制的通用企业分类。", active: true, item_count: 2,
  created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z",
  items: [
    { template_item_id: "cti_product", template_id: "category_template_default", name: "产品资料", description: "产品资料", sort_order: 100, active: true, created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z" },
    { template_item_id: "cti_ops", template_id: "category_template_default", name: "运维文档", description: "运维资料", sort_order: 200, active: false, created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z" },
  ],
};
const documentVersion = {
  document_version_id: "ver_1", document_id: "doc_1", filename: "profile.md",
  version_number: 1, content_sha256: "a".repeat(64), source_file_bytes: 2048,
  source_type: "file", status: "ready", failure_reason: null,
  created_at: "2026-08-12T00:00:00Z", indexed_at: "2026-08-12T00:01:00Z", is_current: true,
  parser_name: "text", parser_version: "2.0", chunking_version: "v1-700-100",
  processing_options: { chunk_size: 700, chunk_overlap: 100 }, parse_status: "ready",
  parse_failure_code: null, node_count: 1, parsed_chunk_count: 1,
};
const answerSummary = {
  report_id: "answer-official",
  dataset_id: "answers",
  dataset_version: "1.0.0",
  commit: "daca18509ca8f447aa00395ca88a58543ffb2cd4",
  run_at: "2026-08-12T08:52:33Z",
  models: { generation: "gemini-test", judge: "judge-test" },
  prompt_version: "v3-grounded-answer-1",
  passed: true,
  official: true,
  sample_count: 30,
  failed_metrics: [],
};
const retrievalReport = {
  report_id: "retrieval-official",
  dataset_id: "retrieval",
  dataset_version: "2.0.0",
  commit: "a".repeat(40),
  run_at: "2026-08-30T00:00:00Z",
  models: {},
  official: true,
  passed: true,
  sample_count: 20,
  failed_metrics: [],
  config_fingerprint: "a".repeat(64),
  parameters: {},
  query_count: 20,
  dataset_evidence: {
    document_count: 10,
    query_count: 20,
    integrity_status: "passed",
    integrity_basis: "report",
  },
  recall_at_5: { value: 0.8, threshold: 0.7, baseline: null, passed: true, regressed: false },
  vector_mrr: { value: 0.7, threshold: 0.6, baseline: null, passed: true, regressed: false },
  rerank_mrr: { value: 0.75, threshold: 0.65, baseline: null, passed: true, regressed: false },
  acl_leak_count: 0,
};
const activeIndexVersion = {
  index_version_id: "iv_active",
  status: "active",
  chunking_version: "semantic-v1",
  parser_version: "registry-v1",
  embedding_model: "text2vec",
  embedding_dimension: 768,
  processing_options: {},
  config_fingerprint: "a".repeat(64),
  evaluation_report_id: "retrieval-official",
  validation_report_id: null,
  document_snapshot_id: "ds_1",
  rebuild_batch_id: null,
  version_no: 4,
  creation_reason: "config_changed",
  force_reason: null,
  requested_by: "test-admin",
  config_snapshot: {},
  component_manifest: {},
  release_fingerprint: null,
  config_completeness: "complete",
  legacy_migrated: false,
  excluded_documents_acknowledged: false,
  created_at: "2026-08-30T00:00:00Z",
  activated_at: "2026-08-30T00:01:00Z",
  retired_at: null,
  cleaned_at: null,
};
// RAG 策略：GET 仍返回完整字段（服务端内部保留 rollout_stage 等参数用于兼容旧客户端读取），
// 页面弹框只展示、只提交 web_search_enabled 与 allowed_domains 两项——见 spec 第 10.3 节。
const ragPolicy = {
  knowledge_base_id: "kb_default",
  rollout_stage: "shadow",
  web_search_enabled: false,
  allowed_domains: ["docs.example.com"],
  intent_confidence_threshold: 0.8,
  minimum_evidence_count: 1,
  max_web_results: 5,
  profile_versions: { fact_lookup_v1: "v1" },
};
const admin = {
  user_id: "usr_1234567890abcdef",
  username: "test-admin",
  display_name: "测试管理员",
  role: "admin",
  active: true,
  created_at: "2026-08-16T00:00:00Z",
  updated_at: "2026-08-16T00:00:00Z",
};
const member = {
  user_id: "usr_abcdef1234567890",
  username: "reader",
  display_name: "资料成员",
  role: "member",
  active: true,
  created_at: "2026-08-16T00:00:00Z",
  updated_at: "2026-08-16T00:00:00Z",
};

beforeEach(() => {
  window.scrollTo = vi.fn();
  setAccessToken("test-token");
});
afterEach(() => {
  cleanup();
  setAccessToken(null);
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  window.history.replaceState({}, "", "/");
});

/**
 * 把若干 SSE 事件拼成一个流式 Response。
 *
 * 问答工作台已从非流式 `/query` 改成 SSE `/query/stream`（api.ts:300-312）。
 * `streamQuery` 直接读 `response.body.getReader()` 并按 `\n\n` 切块、用
 * `/^event:\s*(.+)$/m` 与 `/^data:\s*(.+)$/m` 取字段（api.ts:104-116），所以 mock 必须
 * 给出真的 ReadableStream——`json()` 那种一次性 Response 的 body 在 jsdom 里读不出分块，
 * 组件会一直停在加载态，症状是「答案文本找不到」。
 */
/** 问答工作台的查询结果，/query 与 /query/stream 共用。 */
const QUERY_RESULT = {
  answer: "系统使用可追溯检索。[来源 1]",
  answer_status: "answered",
  error_code: null,
  error_message: null,
  model: "gemini-test",
  latency_ms: { retrieval: 10, rerank: 5, generation: 20, total: 35 },
  conversation_id: "conv_1234567890abcdef",
  record_id: "ans_1",
  models: {},
  model_metadata: {},
  prompt_version: "v3",
  prompt_hash: "abc",
  query_metadata: {
    strategy: "controlled_expansion",
    query_count: 2,
    expansion_count: 1,
    fallback_used: false,
    retrieved_candidate_count: 8,
    fused_candidate_count: 5,
    returned_source_count: 1,
    filter_match_count: 5,
    applied_filters: { categories: ["安全"], tags: ["ACL"], source_types: ["file"], created_from: null, created_to: null },
  },
  generation_governance: { minimum_evidence_count: 1, evidence_count: 1, acl_revalidated: true, current_version_revalidated: true, retrieval_status_revalidated: true, citation_indices: [1], citation_valid: true, claim_citation_coverage: true, outcome_reason: "answered" },
  sources: [
    {
      knowledge_base_id: "kb_default",
      chunk_id: "chunk_1",
      document_id: "doc_1",
      filename: "profile.md",
      page: null,
      paragraph: 0,
      chunk_index: 0,
      char_count: 12,
      summary: "系统资料",
      text: "系统资料全文",
      retrieval_score: 0.82,
      rerank_score: 1.31,
      vector_score: 0.78,
      lexical_score: 0.64,
      retrieval_methods: ["vector", "lexical"],
      query_match_count: 2,
      document_version_id: "ver_1",
      content_sha256: "a".repeat(64),
      heading_path: ["系统设计"],
    },
  ],
};

/**
 * 一条模块轨迹。`metrics` 是后端的自由字段，技术抽屉的 Web / 门禁文案全靠它。
 * `status` 默认 succeeded；未触发 Web 时后端会把 evidence.fuse / rerank.unified 记成
 * `skipped` 并保留在轨迹里（service.py:819-890），时间线要照实显示这个状态。
 */
function moduleExecution(sequence: number, moduleKey: string, metrics: Record<string, unknown>, status = "succeeded") {
  return {
    module_execution_id: `mex_${sequence}`,
    sequence,
    module_key: moduleKey,
    module_version: "1",
    status,
    attempt: 1,
    duration_ms: 12,
    metrics,
    error_code: null,
    error_message: null,
    fallback_reason: null,
  };
}

/** Web 证据：`filename` 是网页标题（service.py:1488），定位靠域名与抓取时间。 */
const WEB_SOURCE = {
  knowledge_base_id: "kb_default",
  chunk_id: "web_abc123",
  document_id: "web_abc123",
  filename: "索引版本发布说明",
  page: null,
  paragraph: 0,
  chunk_index: 0,
  char_count: 40,
  summary: "最新索引版本已发布。",
  text: "最新索引版本已发布。",
  retrieval_score: 0.71,
  rerank_score: 1.12,
  content_sha256: "b".repeat(64),
  source_url: "https://docs.example.com/index-versions",
  evidence_source_type: "web",
  retrieved_at: "2026-09-20T02:00:00Z",
};

function sse(events: Array<{ event: string; data: unknown }>) {
  const payload = events.map((item) => `event: ${item.event}\ndata: ${JSON.stringify(item.data)}\n\n`).join("");
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(payload));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
function commonFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
  if (url === "/api/auth/me") return Promise.resolve(json(admin));
  if (url === "/api/auth/logout") return Promise.resolve(new Response(null, { status: 204 }));
  if (url === "/api/health")
    return Promise.resolve(
      json({
        status: "ok",
        version: "1.0.0",
        collection_ready: true,
        generation_ready: false,
        models: {
          embedding: "embedding-test",
          reranker: "reranker-test",
          generation: "gemini-test",
        },
      }),
    );
  if (url === "/api/health/ready")
    return Promise.resolve(
      json({
        status: "ready",
        checks: {
          auth_store: "ok",
          audit_store: "ok",
          knowledge_base_registry: "ok",
          conversation_store: "ok",
        },
      }),
    );
  if (url === "/api/system/metrics")
    return Promise.resolve(
      json({
        generated_at: "2026-08-17T00:00:00Z",
        requests: { total: 10 },
        rag: { queries: 2 },
        indexing: { documents: 1 },
      }),
    );
  if (url === "/api/members?offset=0&limit=100") return Promise.resolve(json([admin, member]));
  if (url === "/api/knowledge-bases/kb_default/members?offset=0&limit=100") return Promise.resolve(json([member]));
  if (url === "/api/audit/events?offset=0&limit=51")
    return Promise.resolve(
      json([
        {
          event_id: "audit_1234567890abcdef",
          occurred_at: "2026-08-17T00:00:00Z",
          action: "member.update",
          actor_hash: "a".repeat(64),
          actor_role: "admin",
          resource_type: "user",
          resource_id: member.user_id,
          result: "success",
          request_id: "req-test",
          metadata: {},
          previous_hash: "0".repeat(64),
          event_hash: "b".repeat(64),
        },
      ]),
    );
  if (url === "/api/knowledge-bases" && init?.method === "POST")
    return Promise.resolve(
      json({
        ...base,
        knowledge_base_id: "kb_created",
        name: "产品资料",
        is_default: false,
        document_count: 0,
        chunk_count: 0,
      }),
    );
  if (url === "/api/category-templates/default") return Promise.resolve(json(categoryTemplate));
  if (url === "/api/category-templates/default/items" && init?.method === "POST") return Promise.resolve(json(categoryTemplate.items[0], 201));
  if (url.startsWith("/api/category-templates/default/items/") && init?.method === "PUT") return Promise.resolve(json(categoryTemplate.items[0]));
  if (url.startsWith("/api/category-templates/default/items/") && init?.method === "DELETE") return Promise.resolve(new Response(null, { status: 204 }));
  if (url === "/api/knowledge-bases/kb_default/documents/doc_1" && init?.method === "DELETE") return Promise.resolve(new Response(null, { status: 204 }));
  if ((url === "/api/knowledge-bases" || url.startsWith("/api/knowledge-bases?")) && !init?.method) return Promise.resolve(json([base]));
  if (url === "/api/data-sources?offset=0&limit=21") return Promise.resolve(json([dataSource]));
  if (url === "/api/data-sources?offset=0&limit=100") return Promise.resolve(json([dataSource]));
  if (url === "/api/knowledge-bases/kb_default/documents/doc_1/acl" && init?.method === "PUT")
    return Promise.resolve(json({ version: 2, allow_user_ids: [member.user_id], deny_user_ids: [] }));
  if (url === "/api/knowledge-bases/kb_default" && init?.method === "PUT") {
    const payload = JSON.parse(String(init.body)) as { name: string; description: string };
    return Promise.resolve(json({ ...base, ...payload, updated_at: "2026-09-15T08:00:00Z" }));
  }
  if (url === "/api/knowledge-bases/kb_default") return Promise.resolve(json(base));
  if (url === "/api/knowledge-bases/kb_default/documents" && init?.method === "POST")
    return Promise.resolve(json({ ...document, status: "pending" }, 201));
  if (url === "/api/knowledge-bases/kb_default/documents") return Promise.resolve(json([document]));
  if (url === "/api/knowledge-bases/kb_default/categories") return Promise.resolve(json([category]));
  if (url === "/api/knowledge-bases/kb_default/document-versions?offset=0&limit=100") return Promise.resolve(json([documentVersion]));
  if (url === "/api/knowledge-bases/kb_default/index-versions") return Promise.resolve(json([activeIndexVersion]));
  if (url === "/api/knowledge-bases/kb_default/rag-policy" && init?.method === "PUT") {
    const payload = JSON.parse(String(init.body)) as { web_search_enabled: boolean; allowed_domains: string[] };
    return Promise.resolve(json({ ...ragPolicy, ...payload }));
  }
  if (url === "/api/knowledge-bases/kb_default/rag-policy") return Promise.resolve(json(ragPolicy));
  if (url === "/api/knowledge-bases/kb_default/document-versions/ver_1/parsing") return Promise.resolve(json({ ...documentVersion, tree: [{ node_id: "node_00000", node_type: "heading", text: "安全规范", level: 1, location: { heading_path: ["安全规范"], paragraph_index: 0 }, children: [] }], chunks: [{ chunk_id: "chunk_1", chunk_index: 0, content: "ACL 必须在召回前过滤。", metadata: { node_id: "node_00000", heading_path: ["安全规范"], paragraph: 0 } }] }));
  if (url === "/api/knowledge-bases/kb_default/citations/chunk_1") return Promise.resolve(json({ chunk_id: "chunk_1", knowledge_base_id: "kb_default", document_id: "doc_1", document_version_id: "ver_1", content_sha256: "a".repeat(64), filename: "profile.md", text: "系统资料全文", page: null, paragraph: 0, heading_path: ["系统设计"], sheet_name: null, row_start: null, row_end: null, source_url: null, external_resource_id: null }));
  if (url === "/api/knowledge-bases/kb_default/conversations") return Promise.resolve(json([]));
  if (url === "/api/evaluations/answers/reports") return Promise.resolve(json([answerSummary]));
  if (url === "/api/evaluation-center/overview") return Promise.resolve(json({
    passed: true, status: "passed", report_count: 2,
    required_scopes: ["retrieval", "answer"], available_scopes: ["retrieval", "answer"], missing_scopes: [], failed_scopes: [], generated_at: "2026-09-20T00:00:00Z",
    retrieval_report: { report_id: "retrieval-official", dataset_id: "retrieval", dataset_version: "2.0.0", commit: "a".repeat(40), run_at: "2026-08-30T00:00:00Z", models: {}, official: true, passed: true, sample_count: 20, failed_metrics: [] },
    answer_report: answerSummary,
  }));
  if (url.startsWith("/api/evaluation-center/pipeline")) return Promise.resolve(json({ run_count: 2, added_count: 4, updated_count: 1, deleted_count: 1, skipped_count: 2, failed_count: 1, retry_count: 3, failure_rate: 0.5, average_duration_ms: 20000, rag_profiles: [] }));
  if (url.startsWith("/api/evaluation-center/bad-cases")) return Promise.resolve(json([{ case_id: "case_1234567890abcdef", source_type: "online", source_record_id: "ans_1", knowledge_base_id: "kb_default", dataset_version: null, question: "为什么没有召回？", expected_source_ids: [], actual_source_ids: [], expected_answer_status: "answered", actual_answer_status: "insufficient_evidence", actual_answer: "资料不足。", failure_stage: "retrieval", root_cause: null, category: "没召回", severity: "high", assignee: null, fix_commit: null, status: "new", regression_added: false, created_at: "2026-08-30T00:00:00Z", confirmed_at: null, resolved_at: null, updated_at: "2026-08-30T00:00:00Z" }]));
  if (url.startsWith("/api/evaluation-center/acceptance-runs")) return Promise.resolve(json([{
    acceptance_run_id: "acc_1",
    knowledge_base_id: "kb_default",
    status: "blocked",
    commit_sha: "local-working-tree",
    schema_version: 42,
    steps: [
      { step_key: "runtime", title: "运行环境", status: "blocked", summary: "应用 Commit 不可追踪；请配置有效的 APP_COMMIT_SHA。", evidence: { schema_version: 42, required_schema_version: 42 } },
      { step_key: "external_source", title: "真实数据源", status: "blocked", summary: "缺少 S3 兼容外部数据源。", evidence: { external_source_count: 0 } },
      { step_key: "parse_and_index", title: "解析与索引", status: "passed", summary: "解析版本与活动索引均可用。", evidence: { active_index_count: 1, active_index_version_id: "iv_active" } },
      { step_key: "retrieval_and_acl", title: "检索与 ACL", status: "passed", summary: "检索质量门通过且 ACL 泄漏为 0。", evidence: { acl_leak_count: 0, retrieval_report_id: "retrieval-official" } },
      { step_key: "evaluation_and_regression", title: "评测与回归", status: "blocked", summary: "当前知识库尚未建立回归案例。", evidence: { regression_case_count: 0, regression_unverified_count: 0, regression_failed_count: 0 } },
    ],
    limitations: ["缺少 S3 兼容外部数据源。"],
    created_by: admin.user_id,
    created_at: "2026-08-30T00:00:00Z",
  }]));
  // 两条路径共用同一份 payload：/query 仍被少数直接断言用到，/query/stream 是问答工作台
  // 现在真正走的那条（api.ts:308）。流式那条把整个结果作为一个 final 事件发出——
  // 组件对 final 的处理与非流式返回等价，测试要断言的是渲染结果不是分块过程。
  if ((url === "/api/knowledge-bases/kb_default/query" || url === "/api/knowledge-bases/kb_default/query/stream") && init?.method === "POST") {
    const result = QUERY_RESULT;
    if (url.endsWith("/stream")) {
      return Promise.resolve(sse([
        { event: "sources", data: { items: result.sources } },
        { event: "final", data: result },
      ]));
    }
    return Promise.resolve(json(result));
  }
  // 知识库详情页的 load() 用 Promise.all 并行取 11 个接口，**任何一个走到下面那条 404
  // 兜底，整个 Promise.all 就 reject，base 保持 null，整页不渲染**——症状是所有 tab、
  // 所有文档全都 getBy* 找不到，看着像 19 个互不相干的断言失效，实际是一个根因。
  // 新增 load() 依赖的接口时必须同步在这里补 mock。
  if (url === "/api/evaluations") return Promise.resolve(json([]));
  if (url === "/api/knowledge-bases/kb_default/index-builds") return Promise.resolve(json([]));
  if (url === "/api/knowledge-bases/kb_default/operations?limit=50") return Promise.resolve(json([]));
  return Promise.resolve(json({ error: { message: "未找到" } }, 404));
}

function governanceCrossLinkFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
  if (url === "/api/evaluations") return Promise.resolve(json([retrievalReport]));
  if (url === "/api/evaluations/retrieval-official") return Promise.resolve(json(retrievalReport));
  if (url === "/api/evaluation-center/reports/retrieval-official/associations") return Promise.resolve(json({
    report_id: "retrieval-official",
    evaluation_type: "retrieval",
    origin_evaluation_run_id: "eval_1",
    origin_version: null,
    compatible_versions: [],
    validation_usages: [],
  }));
  if (url === "/api/knowledge-bases/kb_default/index-versions/iv_active/validations") return Promise.resolve(json([]));
  if (url === "/api/knowledge-bases/kb_default/index-versions/iv_active/events") return Promise.resolve(json([]));
  if (url === "/api/knowledge-bases/kb_default/index-versions/iv_active/evaluation-runs") return Promise.resolve(json([]));
  if (url === "/api/knowledge-bases/kb_default/index-versions/iv_active/evidence-chain") return Promise.resolve(json({
    knowledge_base_id: "kb_default",
    index_version_id: "iv_active",
    version: { index_version_id: "iv_active", version_no: 4, status: "active", config_fingerprint: "a".repeat(64) },
    evaluation_run: null,
    formal_report: { report_id: "retrieval-official", official: true, passed: true, config_fingerprint: "a".repeat(64), run_at: "2026-08-30T00:00:00Z" },
    validation_report: null,
    activation: null,
    governance: { traceability: "partial", configuration: "match", validation: "pending", release: "pending", reasons: [] },
  }));
  return commonFetch(input, init);
}

test("默认进入概览并汇总知识库、资料、会话和回答质量", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  render(<App />);
  expect(await screen.findByRole("heading", { name: "项目概览" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "概览" })).toBeInTheDocument();
  expect(screen.getByText("应用")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "问答工作台" })).toBeInTheDocument();
  expect(screen.queryByText("工作空间")).not.toBeInTheDocument();
  expect(await screen.findByText("默认知识库")).toBeInTheDocument();
  expect(await screen.findByText("主指标通过")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^上传资料/ })).toBeInTheDocument();
});

test("概览快捷操作使用稳定唯一 key", async () => {
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);

  render(<App />);

  expect(await screen.findByRole("button", { name: /^回答评测/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^检索评测/ })).toBeInTheDocument();
  expect(consoleError.mock.calls.flat().join(" ")).not.toContain("Encountered two children with the same key");
});

test("侧栏展示真实可用的数据源管理入口", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);

  render(<App />);

  expect(await screen.findByRole("button", { name: "数据源管理" })).toBeInTheDocument();
});

test("数据源管理使用独立列表而非复用默认知识库详情", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/data-sources");
  render(<App />);
  expect(await screen.findByRole("region", { name: "数据源管理" })).toBeInTheDocument();
  expect(await screen.findByText("profile.md")).toBeInTheDocument();
  // 「上传状态」+「索引状态」两列已合并成一列「处理状态」：文件源看 index_status、
  // 外部源看 sync_status（DataSourcesPage.tsx:63-76）。「上传成功」这个文案随之消失，
  // INDEX_LABEL 里只有 未索引/等待索引/索引中/索引完成/索引失败。
  expect(screen.getByRole("columnheader", { name: "处理状态" })).toBeInTheDocument();
  expect(screen.getByText("索引完成")).toBeInTheDocument();
  // 「独立列表而非复用详情页」现在靠这一列区分：详情页的表格身处某个知识库内部，
  // 不可能有「所属知识库」列。原来的区分依据是「没有类型列」，但数据源页现在有类型列了
  // （DataSourcesPage.tsx:53），那条断言已经不能用来区分两者。
  expect(screen.getByRole("columnheader", { name: "所属知识库" })).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "文档数" })).not.toBeInTheDocument();
  expect(screen.queryByText("会话历史")).not.toBeInTheDocument();
});

test("文件数据源使用更新文件创建新版本", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  // 「更新文件」已从数据源页移到资料 Tab 的 DocumentPanel（:183）。数据源页每行现在只剩
  // 一个导航操作——「进入资料」/「进入管理」（DataSourcesPage.tsx:40），不再直接改数据。
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);
  expect(screen.queryByRole("button", { name: "同步" })).not.toBeInTheDocument();
  // RowActions 统一了可访问名格式为「{rowLabel} 的{action.label}」（见 ui/RowActions.tsx），
  // 不再是页面自己拼的「更新 {name}」。
  await userEvent.upload(await screen.findByLabelText("profile.md 的更新文件"), new File(["updated"], "profile.md", { type: "text/markdown" }));
  // 同内容重传可能恢复缺失源文件，因此提示同时覆盖“上传新版本”和“恢复源文件”。
  expect(await screen.findByRole("status")).toHaveTextContent("“profile.md”已上传或恢复");
  expect(fetchMock).toHaveBeenCalledWith("/api/knowledge-bases/kb_default/documents", expect.objectContaining({ method: "POST" }));
});

test("索引处理中自动刷新数据源状态", async () => {
  let sourceRequests = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/data-sources?offset=0&limit=21") {
      sourceRequests += 1;
      return Promise.resolve(json([{ ...dataSource, index_status: sourceRequests === 1 ? "queued" : "succeeded" }]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/data-sources");
  render(<App />);
  // Badge 不透传任意 DOM 属性，aria-busy 挂在包装它的 span 上（见 DataSourcesPage.tsx
  // 索引状态列的注释），不再和旋转动画的 class 落在同一个节点。
  // .index-loading 已在 UI Foundation 阶段 5 Task 4 收口为内联 utility class（不再是
  // 具名 class），断言改为检查旋转动画本身的 utility（对应 ::before 的 animation），
  // 意图不变：只有「加载中」态才应该带这个旋转指示器。
  const indexBadge = await screen.findByText("等待索引");
  expect(indexBadge).toHaveClass("before:[animation:spin_0.7s_linear_infinite]");
  expect(indexBadge.closest("[aria-busy]")).toHaveAttribute("aria-busy", "true");
  expect(await screen.findByText("索引完成", {}, { timeout: 2_000 })).toBeInTheDocument();
  expect(sourceRequests).toBe(2);
});

test("知识库列表可进入绑定 knowledge_base_id 的详情", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases");
  render(<App />);
  expect(await screen.findByRole("region", { name: "知识库管理" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "＋ 新建知识库" }).closest("header")).not.toBeNull();
  expect(globalThis.document.querySelector(".page-heading.bases-toolbar")).toBeNull();
  expect(screen.queryByText("为不同项目建立隔离的资料、索引与会话空间。")).not.toBeInTheDocument();
  expect(globalThis.document.querySelector(".base-icon")).toBeNull();
  expect(await screen.findByRole("columnheader", { name: "知识库名称" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "存储空间" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "状态" })).toBeInTheDocument();
  // DataTable 的表头是同步渲染的（不等数据），不能再靠等表头来间接等到数据加载完成——
  // 这里必须直接等行内容出现（含 250ms 防抖 + 请求往返）。
  expect(await screen.findByText("默认知识库", { selector: "span" })).toHaveClass("bg-brand-subtle");
  // 行操作直接展示，不再需要先打开「更多操作」菜单。
  const baseRow = screen.getByRole("button", { name: "默认知识库" }).closest("tr") as HTMLElement;
  await userEvent.click(within(baseRow).getByRole("button", { name: "详情" }));
  // 「资料」Tab 现在同时有资料列表和「全部资料版本」表，同一个文件名会出现多次，
  // findByText 会抛 found multiple——限定到资料列表内。
  expect((await screen.findAllByText("profile.md")).length).toBeGreaterThan(0);
  expect(screen.queryByText("正在读取知识库详情…")).not.toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /资料/ })).toHaveAttribute("aria-selected", "true");
  // profile.md 分类状态是 manual，只有「编辑/删除」两个操作，走平铺而非菜单。
  await userEvent.click(screen.getByRole("button", { name: "编辑" }));
  expect(screen.getByRole("dialog", { name: "编辑资料元数据" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "取消" }));
  await userEvent.click(screen.getByRole("tab", { name: /索引治理/ }));
  expect(screen.getByRole("tab", { name: /索引治理/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("heading", { name: "索引版本" })).toBeInTheDocument();
  expect(screen.getByText("iv_active")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "文档与版本" })).not.toBeInTheDocument();
  // 「解析与切片」不再是独立 Tab：ParsingPanel 被移进了「资料」Tab 里文档详情弹层
  // （DocumentPanel.tsx:423），入口是列表里那个文件名按钮（:222-229）。
  // 「解析与切片」这个名字现在是资料版本表的一个列 header（KnowledgeBaseDetailPage.tsx:235）。
  await userEvent.click(screen.getByRole("tab", { name: /资料/ }));
  await userEvent.click(screen.getByRole("button", { name: "profile.md" }));
  expect(await screen.findByRole("heading", { name: "文档结构" })).toBeInTheDocument();
  expect(screen.getAllByText("ACL 必须在召回前过滤。").length).toBeGreaterThan(0);
  // 弹层是模态的，不关掉后面点不到 Tab。
  await userEvent.click(screen.getByRole("button", { name: "关闭弹框" }));
  await userEvent.click(screen.getByRole("tab", { name: /权限边界/ }));
  expect(screen.getByRole("tab", { name: /权限边界/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByRole("heading", { name: "权限边界" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "数据源 ACL" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "文档 ACL" })).toBeInTheDocument();
  await userEvent.click(screen.getAllByRole("button", { name: "配置" })[1]);
  expect(screen.getByRole("dialog", { name: "配置 ACL" })).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("资料成员 ACL"), "allow");
  await userEvent.click(screen.getByRole("button", { name: "保存并立即生效" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(
    "/api/knowledge-bases/kb_default/documents/doc_1/acl",
    expect.objectContaining({ method: "PUT" }),
  ));
  expect(window.location.pathname).toBe("/knowledge-bases/kb_default");
});

test("知识库列表移除编辑入口并保留详情与删除", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases");
  render(<App />);

  const baseName = await screen.findByRole("button", { name: "默认知识库" });
  const baseRow = baseName.closest("tr") as HTMLElement;
  expect(within(baseRow).getByRole("button", { name: "详情" })).toBeInTheDocument();
  expect(within(baseRow).getByRole("button", { name: "删除" })).toBeInTheDocument();
  expect(within(baseRow).queryByRole("button", { name: "编辑" })).toBeNull();
  expect(screen.queryByRole("dialog", { name: "编辑知识库" })).toBeNull();
});

test("管理员可在知识库详情编辑基础信息并立即刷新摘要", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);

  const editButton = await screen.findByRole("button", { name: "编辑基础信息" });
  await userEvent.click(editButton);
  const dialog = screen.getByRole("dialog", { name: "编辑知识库" });
  expect(within(dialog).getByLabelText("知识库名称")).toHaveValue("默认知识库");
  expect(within(dialog).getByLabelText("描述 选填")).toHaveValue("V2 迁移资料");

  await userEvent.clear(within(dialog).getByLabelText("知识库名称"));
  await userEvent.type(within(dialog).getByLabelText("知识库名称"), "   ");
  await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));
  expect(await within(dialog).findByText("请输入知识库名称。")).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url, init]) => String(url) === "/api/knowledge-bases/kb_default" && init?.method === "PUT")).toBe(false);

  await userEvent.clear(within(dialog).getByLabelText("知识库名称"));
  await userEvent.type(within(dialog).getByLabelText("知识库名称"), "  新知识库名称  ");
  await userEvent.clear(within(dialog).getByLabelText("描述 选填"));
  await userEvent.type(within(dialog).getByLabelText("描述 选填"), "  新描述  ");
  await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/knowledge-bases/kb_default",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ name: "新知识库名称", description: "新描述" }),
    }),
  ));
  expect(await screen.findByText("新知识库名称")).toBeInTheDocument();
  expect(screen.getByText("新描述")).toBeInTheDocument();
  expect(screen.getByText("基础信息已更新")).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "编辑知识库" })).toBeNull();
  await waitFor(() => expect(editButton).toHaveFocus());
});

test("RAG 策略弹框只展示 Web 开关与可信域名两个字段", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);

  await userEvent.click(await screen.findByRole("button", { name: "RAG 策略" }));
  const dialog = await screen.findByRole("dialog", { name: "RAG 策略" });

  expect(dialog).toHaveTextContent("控制当前知识库是否允许受控 Web 补检，并限定可访问的可信域名。");
  expect(dialog).toHaveTextContent("Web 只补充知识库证据，不会自动写入知识库或绕过知识库范围。");

  // 四个已删控件不再出现——发布阶段、意图置信度、最少证据、Web 结果上限属系统内部
  // 参数，spec 第 10.3 节要求页面隐藏，仅由服务端保留现值统一管理。
  expect(screen.queryByText("发布阶段")).not.toBeInTheDocument();
  expect(screen.queryByText("意图置信度")).not.toBeInTheDocument();
  expect(screen.queryByText("最少证据")).not.toBeInTheDocument();
  expect(screen.queryByText("Web 结果上限")).not.toBeInTheDocument();
  expect(screen.getByText("启用受控 Web 检索")).toBeInTheDocument();
  expect(screen.getByText("可信域名白名单")).toBeInTheDocument();

  // D4 正向断言：用可访问角色数量锁住「弹框内只剩这两个可交互控件」这个事实，而不是
  // 只靠上面四条否定断言——否定断言只要文案改一个字就会假绿，数不到「删干净了没有」。
  expect(within(dialog).getAllByRole("checkbox")).toHaveLength(1);
  expect(within(dialog).getAllByRole("textbox")).toHaveLength(1);
  expect(within(dialog).queryAllByRole("spinbutton")).toHaveLength(0); // 原三个 number 输入（意图置信度/最少证据/Web 结果上限）
  expect(within(dialog).queryAllByRole("combobox")).toHaveLength(0); // 原发布阶段 Select
});

test("开启 Web 但域名为空时前端阻止提交并显示可见错误，保存的 PUT body 只含两个公开字段", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);

  await userEvent.click(await screen.findByRole("button", { name: "RAG 策略" }));
  const dialog = await screen.findByRole("dialog", { name: "RAG 策略" });
  const webToggle = within(dialog).getByRole("checkbox", { name: "启用受控 Web 检索" });
  const domainInput = within(dialog).getByLabelText(/可信域名白名单/);

  // R12(b)：不做成禁用按钮——按钮全程可点，开启 Web 但域名为空时点击后报错，
  // 与本页 saveBase / saveCategory（CategoryTemplateModal 同款）的既有交互一致。
  await userEvent.click(webToggle);
  await userEvent.clear(domainInput);
  const saveButton = within(dialog).getByRole("button", { name: "保存策略" });
  expect(saveButton).toBeEnabled();
  await userEvent.click(saveButton);

  expect(await within(dialog).findByRole("alert")).toHaveTextContent("至少填写一个可信域名。");
  expect(fetchMock.mock.calls.some(([url, init]) => String(url) === "/api/knowledge-bases/kb_default/rag-policy" && init?.method === "PUT")).toBe(false);

  await userEvent.type(domainInput, "new.example.com");
  await userEvent.click(within(dialog).getByRole("button", { name: "保存策略" }));

  await waitFor(() => expect(screen.queryByRole("dialog", { name: "RAG 策略" })).toBeNull());
  expect(screen.getByText("RAG 策略已更新")).toBeInTheDocument();

  const putCall = fetchMock.mock.calls.find(
    ([url, init]) => String(url) === "/api/knowledge-bases/kb_default/rag-policy" && init?.method === "PUT",
  );
  expect(putCall).toBeDefined();
  // A13（锁定断言，请勿修改）：PUT body 只能有 web_search_enabled 与 allowed_domains
  // 两个字段——旧的发布阶段/意图置信度/最少证据/Web 结果上限一律不得出现在请求体里。
  // 后续 Task 7、Task 8 会继续往本文件追加 RAG 策略相关用例，但不应改动这条断言。
  expect(JSON.parse(String(putCall?.[1]?.body))).toEqual({
    web_search_enabled: true,
    allowed_domains: ["new.example.com"],
  });
});

test("无 edit 动作的成员看不到详情页编辑入口", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default") {
      return Promise.resolve(json({
        ...base,
        current_user_permission: "use",
        allowed_actions: ["detail"],
      }));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);

  expect(await screen.findByText("V2 迁移资料")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "编辑基础信息" })).toBeNull();
  expect(screen.getByRole("button", { name: "在此知识库提问 →" })).toBeInTheDocument();
});

test("知识库名称冲突时保留编辑内容并显示后端错误", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default" && init?.method === "PUT") {
      return Promise.resolve(json({ error: { code: "KNOWLEDGE_BASE_NAME_CONFLICT", message: "知识库名称已存在。" } }, 409));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);

  await userEvent.click(await screen.findByRole("button", { name: "编辑基础信息" }));
  const dialog = screen.getByRole("dialog", { name: "编辑知识库" });
  const nameInput = within(dialog).getByLabelText("知识库名称");
  await userEvent.clear(nameInput);
  await userEvent.type(nameInput, "重复名称");
  await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));

  expect(await within(dialog).findByText("知识库名称已存在。")).toBeInTheDocument();
  expect(nameInput).toHaveValue("重复名称");
  expect(screen.getByRole("dialog", { name: "编辑知识库" })).toBeInTheDocument();
});

test("知识库基础信息保存期间阻止重复提交和关闭", async () => {
  let resolveUpdate!: (response: Response) => void;
  const pendingUpdate = new Promise<Response>((resolve) => { resolveUpdate = resolve; });
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default" && init?.method === "PUT") {
      return pendingUpdate;
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);

  await userEvent.click(await screen.findByRole("button", { name: "编辑基础信息" }));
  const dialog = screen.getByRole("dialog", { name: "编辑知识库" });
  const nameInput = within(dialog).getByLabelText("知识库名称");
  await userEvent.clear(nameInput);
  await userEvent.type(nameInput, "保存中的知识库");
  await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));

  expect(within(dialog).getByRole("button", { name: "保存" })).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "取消" })).toBeDisabled();
  await userEvent.click(within(dialog).getByRole("button", { name: "关闭弹框" }));
  expect(screen.getByRole("dialog", { name: "编辑知识库" })).toBeInTheDocument();

  resolveUpdate(json({ ...base, name: "保存中的知识库" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑知识库" })).toBeNull());
});

test("编辑资料元数据时按分类 ID 选择并保存分类", async () => {
  const productCategory = {
    ...category,
    category_id: "cat_product",
    name: "产品资料",
    description: "产品介绍、规格与方案资料",
    document_count: 0,
  };
  const requests: Array<{ url: string; body: unknown }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/knowledge-bases/kb_default/categories") {
      return Promise.resolve(json([category, productCategory]));
    }
    if (url === "/api/knowledge-bases/kb_default/documents/categories" && init?.method === "PUT") {
      requests.push({ url, body: JSON.parse(String(init.body)) });
      return Promise.resolve(json({ updated: 1 }));
    }
    if (url === "/api/knowledge-bases/kb_default/documents/doc_1/metadata" && init?.method === "PATCH") {
      return Promise.resolve(json({ ...document, category: "产品资料", category_id: "cat_product" }));
    }
    return commonFetch(input, init);
  });

  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "编辑" }));
  const metadataDialog = screen.getByRole("dialog", { name: "编辑资料元数据" });
  const categorySelect = within(metadataDialog).getByLabelText("分类");
  await userEvent.selectOptions(categorySelect, "cat_product");
  expect(categorySelect).toHaveValue("cat_product");
  await userEvent.click(within(metadataDialog).getByRole("button", { name: "保存" }));

  await waitFor(() => expect(requests).toContainEqual({
    url: "/api/knowledge-bases/kb_default/documents/categories",
    body: { document_ids: ["doc_1"], category_id: "cat_product" },
  }));
});

test("知识库详情提供数据源同步治理 Tab", async () => {
  // 这个 Tab 只列**外部**数据源：详情页传进去的是
  // `dataSources.filter(item => item.source_type !== "file")`（KnowledgeBaseDetailPage.tsx:288），
  // 文件源归「资料」Tab。公共 mock 里那个 profile.md 是 source_type:"file"，会被整个滤掉，
  // 表格走 EmptyState 分支、连 columnheader 都不渲染——所以这里必须给一个非 file 的源。
  const objectSource = { ...dataSource, data_source_id: "src_s3", name: "enterprise-docs", source_type: "object_storage" };
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/data-sources?offset=0&limit=100") return Promise.resolve(json([objectSource]));
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);

  await userEvent.click(await screen.findByRole("tab", { name: /数据源/ }));

  expect(screen.getByRole("button", { name: "新建外部数据源" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "同步进度" })).toBeInTheDocument();
  expect(screen.getByText("enterprise-docs")).toBeInTheDocument();
});

test("资料库支持一次选择多个文件并逐个上传", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);
  const input = await screen.findByLabelText("批量上传资料");
  await userEvent.upload(input, [
    new File(["one"], "one.md", { type: "text/markdown" }),
    new File(["two"], "two.txt", { type: "text/plain" }),
  ]);
  await waitFor(() => {
    const uploads = fetchMock.mock.calls.filter(([url, init]) => String(url) === "/api/knowledge-bases/kb_default/documents" && init?.method === "POST");
    expect(uploads).toHaveLength(2);
  });
});

test("通过弹框创建知识库并支持取消", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases");
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "＋ 新建知识库" }));
  expect(screen.getByRole("dialog", { name: "新建知识库" })).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "应用默认分类模板" })).toBeChecked();
  expect(screen.getByText("将复制 1 个有效分类：产品资料")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("知识库名称"), "产品资料");
  await userEvent.click(screen.getByRole("button", { name: "确认创建" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/knowledge-bases", expect.objectContaining({
    method: "POST",
    body: JSON.stringify({ name: "产品资料", description: "", apply_default_category_template: true }),
  })));
});

test("管理员可在知识库列表治理默认分类模板", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases");
  render(<App />);

  await userEvent.click(await screen.findByRole("button", { name: "知识库分类模板" }));

  expect(screen.getByRole("dialog", { name: "默认分类模板" })).toBeInTheDocument();
  expect(screen.queryByText(/个分类 · 更新于/)).not.toBeInTheDocument();
  expect(screen.getByText("此处管理新知识库的初始分类模板，不会修改已有知识库分类。")).toBeInTheDocument();
  expect(screen.getAllByText("产品资料").length).toBeGreaterThan(0);
  expect(screen.getByText("运维文档")).toBeInTheDocument();
  expect(screen.getByText(/已停用/)).toBeInTheDocument();
});

test("模板分类的新建与编辑复用同一弹层，列表用表头加数据行", async () => {
  // 两处对齐：新建不再是「列表上方三个横排输入框」，而是和编辑同一个弹层；
  // 列表不再把名称/排序/说明堆成三行，改成表格——堆叠布局扫读要一行行看，
  // 而这正是 docs/design/ui-foundation-tokens.md 第 3.5 节列表规则要避免的。
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases");
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "知识库分类模板" }));

  const panel = await screen.findByRole("dialog");
  // 表头存在，且列名齐全
  for (const header of ["分类名称", "排序", "说明", "操作"]) {
    expect(within(panel).getByRole("columnheader", { name: header })).toBeInTheDocument();
  }
  // 列表上方不再有横排的新建输入框
  expect(within(panel).queryByLabelText("模板分类名称")).toBeNull();

  // 新建走弹层，字段与编辑一致
  await userEvent.click(within(panel).getByRole("button", { name: /新建分类/ }));
  const form = await screen.findByRole("dialog", { name: "新建模板分类" });
  expect(within(form).getByLabelText("分类名称")).toBeVisible();
  expect(within(form).getByLabelText("说明")).toBeVisible();
  // 排序默认排在末尾：现有模板最大 200
  expect(within(form).getByLabelText("排序")).toHaveValue(300);

  // 空名称可点击并报错，与其它表单一致
  await userEvent.click(within(form).getByRole("button", { name: "创建" }));
  expect(await within(form).findByRole("alert")).toHaveTextContent("请输入分类名称");
});

test("无分类资料显示占位符而不是伪造的分类名", async () => {
  const uncategorized = {
    ...document, document_id: "doc_2", filename: "draft.md",
    category: null, category_id: null, classification_status: "pending",
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default/documents" && init?.method !== "POST") {
      return Promise.resolve(json([document, uncategorized]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);

  const row = (await screen.findByText("draft.md")).closest("tr") as HTMLElement;
  // 锁定分类列那一格再断言：行里现在有多个「—」占位（标签、当前版本等列都会出现它），
  // 直接 within(row).getByText("—") 会报 found multiple。这里用「待分类」徽章反向找到它
  // 所在的 <td>，而不是数第几个——DocumentPanel 开了 selection（:383），
  // 首列是 checkbox，按索引数会偏一位。
  const categoryCell = within(row).getByText("待分类").closest("td") as HTMLElement;
  expect(within(categoryCell).getByText("—")).toBeTruthy();
  expect(within(row).queryByText("未分类")).toBeNull();
});

/**
 * ⚠ 这条测试当前是**红的，而且不要改断言让它变绿**。
 *
 * 它抓到的是一个真实的功能回退：分类失败的原因不再显示给用户。
 * - 后端照旧写入并返回：`postgres_documents.py:1843-1844` 写、`:361-365` 从 metadata 读、
 *   `schemas.py:129-130` 在响应模型里，字段一路都在。
 * - 前端却在 `23bdcc2`（完善多模型生成与知识库资料治理）之后停止渲染它——
 *   `classification_failure_code` / `classification_failure_reason` 现在只剩
 *   `types.ts:31-32` 的类型声明，任何组件都不再读它们。
 *
 * 所以用户看到的只有一个「分类失败」徽章，看不到为什么失败。这与 CLAUDE.md 第一条
 * （失败/禁用必须说得出为什么）相冲突；CLAUDE.md 第三条本身就是为这个字段写的
 * ——那次是写入了但读取路径漏挑，这次是接口返回了但前端不渲染。
 *
 * 修法在 `DocumentPanel` 的分类列（或详情弹层）里把 reason 显示出来，不是改这里。
 */
test("分类失败展示原因并提供重新分类入口", async () => {
  const failed = {
    ...document, document_id: "doc_3", filename: "broken.md",
    category: null, category_id: null, classification_status: "failed",
    classification_failure_code: "MODEL_TIMEOUT",
    classification_failure_reason: "模型 30 秒未响应",
  };
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default/documents" && init?.method !== "POST") {
      return Promise.resolve(json([failed]));
    }
    if (String(input) === "/api/knowledge-bases/kb_default/documents/reclassify") {
      return Promise.resolve(json({ updated: 1 }));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);

  const row = (await screen.findByText("broken.md")).closest("tr") as HTMLElement;
  expect(within(row).getByText(/分类失败/)).toBeTruthy();
  // 原因挂在 ⓘ 的 Tooltip 上（与 ui/Button 的 blockedReason 同一套模式），
  // Radix 的 Tooltip 内容不悬停时不在 DOM，所以要先 hover——测法同 Button.test.tsx:89。
  await userEvent.hover(within(row).getByRole("button", { name: "broken.md 的分类失败原因" }));
  expect((await screen.findAllByText(/模型 30 秒未响应/)).length).toBeGreaterThan(0);

  await userEvent.click(within(row).getByRole("button", { name: "重新分类" }));

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/knowledge-bases/kb_default/documents/reclassify",
      expect.objectContaining({ method: "POST" }),
    ),
  );
});

test("资料筛选可以单独筛出无分类与分类失败", async () => {
  const uncategorized = {
    ...document, document_id: "doc_2", filename: "draft.md",
    category: null, category_id: null, classification_status: "pending",
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default/documents" && init?.method !== "POST") {
      return Promise.resolve(json([document, uncategorized]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);

  await screen.findByText("draft.md");
  await userEvent.selectOptions(screen.getByLabelText("分类筛选"), "__uncategorized__");

  // 断言范围限定在「资料」表内。同一个 Tab 下方还有「全部资料版本」表，它有意不跟随
  // 这个筛选（标题与副标题就是它的可见说明），整页 queryByText 会把它的行也算进来。
  const documentsTable = screen.getByRole("table", { name: "资料列表" });
  expect(within(documentsTable).queryByText("profile.md")).toBeNull();
  expect(within(documentsTable).getByText("draft.md")).toBeTruthy();
});

test("分类字典为空时给出明确空态而不是凭空造一个分类", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default/categories") {
      return Promise.resolve(json([]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);

  await userEvent.click(await screen.findByRole("tab", { name: /分类管理/ }));

  expect(await screen.findByText("暂无知识库独立分类")).toBeTruthy();
});

test("删除资料使用站内确认弹框", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);
  // profile.md 分类状态是 manual，只有「编辑/删除」两个操作，走平铺而非菜单。
  await userEvent.click(await screen.findByRole("button", { name: "删除" }));
  // 弹层打开时 Radix 会给背景内容加 aria-hidden，所以要显式查隐藏元素。断言的意图
  // 不变：这是站内弹框，页面内容仍在（而不是浏览器原生 confirm）。
  // .detail-toolbar 已随 KnowledgeBaseDetailPage 迁移到基座被删除（见 UI Foundation
  // 阶段 3 Task 5），改为锚定页面外层容器，断言意图不变：弹层打开后背景内容仍在渲染。
  // .product-page 已在 UI Foundation 阶段 5 Task 4 收口为 utility class，改为锚定
  // 最近的 <section> 容器（页面顶层唯一祖先 section），断言意图不变。
  expect(
    screen.getByRole("button", { name: "在此知识库提问 →", hidden: true }).closest("section"),
  ).not.toBeNull();
  expect(screen.getByText("V2 迁移资料")).toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "删除资料" })).toHaveTextContent("profile.md");
  await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/knowledge-bases/kb_default/documents/doc_1", expect.objectContaining({ method: "DELETE" })));
});

test("问答工作台使用所选知识库接口并渲染来源", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/chat?knowledge_base_id=kb_default");
  render(<App />);
  const basePicker = await screen.findByLabelText("当前知识库");
  // 断言的是"知识库选择器在提问表单内"这件事，不是某个 legacy class 是否存在——
  // 迁移后 question-box 类被移除，用语义标签 <form> 重新表达同一个断言意图。
  expect(basePicker.closest("form")).not.toBeNull();
  expect(screen.getByRole("tab", { name: /引用来源/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByRole("tab", { name: /资料库/ })).not.toBeInTheDocument();
  await screen.findByRole("option", { name: "安全" });
  await userEvent.selectOptions(screen.getByLabelText("过滤分类"), "安全");
  await userEvent.type(screen.getByLabelText("过滤标签"), "ACL");
  await userEvent.selectOptions(screen.getByLabelText("过滤来源类型"), "file");
  await userEvent.type(await screen.findByLabelText("向知识库提问"), "系统如何工作？");
  await userEvent.click(screen.getByRole("button", { name: /提问/ }));
  expect(await screen.findByText("系统使用可追溯检索。")).toBeInTheDocument();
  await userEvent.click(screen.getByText("查看技术细节"));
  expect(screen.getByText("可控查询扩展 · 2 路查询")).toBeInTheDocument();
  expect(screen.getByLabelText("实际生效的过滤条件")).toHaveTextContent("分类：安全标签：ACL来源：文件");
  expect(screen.getByText("候选：召回 8 / 融合 5 / 返回 1 · 过滤命中 5")).toBeInTheDocument();
  // 问答工作台走的是流式那条（api.ts:308），不是非流式 /query。
  expect(fetchMock).toHaveBeenCalledWith("/api/knowledge-bases/kb_default/query/stream", expect.objectContaining({ method: "POST" }));
  const queryCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/knowledge-bases/kb_default/query/stream");
  expect(JSON.parse(String(queryCall?.[1]?.body))).toMatchObject({ filters: { category_ids: ["cat_1234567890abcdef"], tags: ["ACL"], source_types: ["file"] } });
});

test("同一会话连续提问后保留全部问答轮次", async () => {
  const conversationId = "conv_1234567890abcdef";
  const records = [{
    record_id: "ans_first", conversation_id: conversationId, knowledge_base_id: "kb_default",
    question: "第一轮问题", status: "success", answer: "第一轮回答", sources: [],
    latency_ms: {}, models: {}, model_metadata: {}, prompt_version: null, prompt_hash: null,
    answer_status: "answered", generation_governance: null, query_metadata: null,
    error_code: null, error_message: null, created_at: "2026-09-15T05:00:00Z",
  }];
  let nextRecord = 2;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === `/api/knowledge-bases/kb_default/conversations/${conversationId}`) {
      return Promise.resolve(json({
        conversation_id: conversationId, knowledge_base_id: "kb_default", title: "第一轮问题",
        created_at: "2026-09-15T05:00:00Z", updated_at: "2026-09-15T05:03:00Z",
        records: [...records],
      }));
    }
    if (url === "/api/knowledge-bases/kb_default/conversations") {
      return Promise.resolve(json([{
        conversation_id: conversationId, knowledge_base_id: "kb_default", title: "第一轮问题",
        created_at: "2026-09-15T05:00:00Z", updated_at: "2026-09-15T05:03:00Z",
        turn_count: records.length, last_status: "success",
      }]));
    }
    if (url === "/api/knowledge-bases/kb_default/query/stream" && init?.method === "POST") {
      const payload = JSON.parse(String(init.body)) as { question: string };
      const record = {
        ...records[0],
        record_id: `ans_${nextRecord}`,
        question: payload.question,
        answer: `${payload.question}的回答`,
        created_at: `2026-09-15T05:0${nextRecord}:00Z`,
      };
      nextRecord += 1;
      records.push(record);
      return Promise.resolve(sse([{ event: "final", data: {
        ...QUERY_RESULT,
        answer: record.answer,
        conversation_id: conversationId,
        record_id: record.record_id,
        sources: [],
      } }]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", `/chat/${conversationId}?knowledge_base_id=kb_default`);
  render(<App />);

  expect(await screen.findByText("第一轮回答")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("向知识库提问"), "第二轮问题");
  await userEvent.click(screen.getByRole("button", { name: "提问并发送" }));
  expect(await screen.findByText("第二轮问题的回答")).toBeInTheDocument();

  await userEvent.type(screen.getByLabelText("向知识库提问"), "第三轮问题");
  await userEvent.click(screen.getByRole("button", { name: "提问并发送" }));
  expect(await screen.findByText("第三轮问题的回答")).toBeInTheDocument();

  expect(screen.getByText("第一轮回答")).toBeInTheDocument();
  expect(screen.getByText("第二轮问题的回答")).toBeInTheDocument();
  expect(screen.getAllByText("第二轮问题的回答")).toHaveLength(1);
  expect(screen.getByText("3 轮 · 2026/9/15")).toBeInTheDocument();
});

test("可信引用可以在局部弹窗定位到原文", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/chat?knowledge_base_id=kb_default");
  render(<App />);
  await userEvent.type(await screen.findByLabelText("向知识库提问"), "系统如何工作？");
  await userEvent.click(screen.getByRole("button", { name: /提问/ }));

  await userEvent.click(await screen.findByRole("button", { name: "查看 profile.md 原文" }));

  expect(await screen.findByRole("dialog", { name: "可信引用原文" })).toHaveTextContent("系统资料全文");
  expect(screen.getByRole("dialog", { name: "可信引用原文" })).toHaveTextContent("系统设计");
  expect(screen.getByRole("dialog", { name: "可信引用原文" })).toHaveTextContent("ver_1");
});

test("证据不足状态说明不会把降级结果伪装成答案", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    // 拦流式那条：工作台已不走非流式 /query（api.ts:308）。
    if (String(input) === "/api/knowledge-bases/kb_default/query/stream") {
      const degraded = {
        ...QUERY_RESULT,
        answer: "当前资料不足以支持确定回答。",
        answer_status: "insufficient_evidence",
        sources: [],
      };
      return Promise.resolve(sse([{ event: "final", data: degraded }]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/chat?knowledge_base_id=kb_default");
  render(<App />);
  await userEvent.type(await screen.findByLabelText("向知识库提问"), "未知问题");
  await userEvent.click(screen.getByRole("button", { name: /提问/ }));

  expect(await screen.findByText("证据不足")).toBeInTheDocument();
  expect(screen.getByText("未达到证据阈值，不生成确定性结论。")).toBeInTheDocument();
});

test("时效降级回答显示「时效未验证」并说明未取得 Web 证据", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default/query/stream") {
      return Promise.resolve(sse([{ event: "final", data: {
        ...QUERY_RESULT,
        answer: "时效未验证：索引版本停留在 v1[来源 1]。",
        answer_status: "answered_stale",
        module_executions: [
          moduleExecution(1, "evidence.preliminary_gate", { outcome: "needs_web", sufficient: true, kb_count: 1, evidence_count: 1, requires_freshness: true, web_available: true, reason_codes: ["freshness_required"] }),
          moduleExecution(2, "retrieval.web_policy", { decision: "failed", result_count: 0, reason_code: "web_retrieval_failed" }),
          moduleExecution(3, "evidence.final_gate", { outcome: "stale", sufficient: true, selected_count: 1, kb_count: 1, web_count: 0, requires_freshness: true, freshness_verified: false, reason_codes: ["freshness_required", "web_no_qualified_result", "freshness_unverified"] }),
        ],
      } }]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/chat?knowledge_base_id=kb_default");
  render(<App />);
  await userEvent.type(await screen.findByLabelText("向知识库提问"), "截至目前最新的索引版本是什么？");
  await userEvent.click(screen.getByRole("button", { name: /提问/ }));

  const label = await screen.findByText("时效未验证");
  expect(label).toBeInTheDocument();
  // 橙色是这条状态唯一的非文字信号。tailwind.css 只有 --color-warning，
  // 没有 --color-warning-text——写错令牌 Tailwind 不报错，只会让告警色消失。
  expect(label).toHaveClass("text-warning");
  expect(screen.getByText(/未取得可信的当前 Web 证据/)).toBeInTheDocument();

  await userEvent.click(screen.getByText("查看技术细节"));
  // 技术原因只落在抽屉里（spec 7.2），而且读的是 retrieval.web_policy 的 decision，
  // 不是"最终有没有 Web 来源"——stale 的 Web 来源数恒为 0，反推只会说成"未采用"。
  expect(screen.getByText("联网：Web 搜索失败")).toBeInTheDocument();
  expect(screen.getByText(/初步门禁：需要 Web 补检 · 知识库合格 1 条/)).toBeInTheDocument();
  expect(screen.getByText(/Final Gate 时效未验证 · 原因：问题要求时效、Web 无合格结果、时效未验证/)).toBeInTheDocument();
});

test("问候直接回复，不展示证据、查询性能与技术抽屉", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default/query/stream") {
      return Promise.resolve(sse([
        { event: "routing_completed", data: { intent: "greeting", confidence: 1, reason: "整句问候", control_outcome: "social", original_question: "你好，在吗", effective_question: "你好，在吗", follow_up_rewritten: false, requires_freshness: false, requires_web: false, classifier_model: null, fallback_used: false } },
        { event: "final", data: {
          ...QUERY_RESULT,
          answer: "你好，我在。你可以询问当前知识库中的事实、配置或资料内容。",
          answer_status: "direct_response",
          sources: [],
          // 问候不跑检索，latency_ms 只有 routing 与 total（Task 6 契约 5.4）。
          latency_ms: { routing: 3, total: 4 },
          query_metadata: null,
          generation_governance: null,
          module_executions: [moduleExecution(1, "intent.router", {})],
        } },
      ]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/chat?knowledge_base_id=kb_default");
  render(<App />);
  await userEvent.type(await screen.findByLabelText("向知识库提问"), "你好，在吗");
  await userEvent.click(screen.getByRole("button", { name: /提问/ }));

  expect(await screen.findByText("你好，我在。你可以询问当前知识库中的事实、配置或资料内容。")).toBeInTheDocument();
  expect(screen.getByText("对话回复")).toBeInTheDocument();
  expect(screen.queryByLabelText("查询性能")).toBeNull();
  expect(screen.queryByText("查看技术细节")).toBeNull();
  // 这里**不**断言 AnswerPanel 里的「引用证据」不存在：`showSources` 在两处调用点
  // （ChatPage 里）都硬传 false，那个分支恒不渲染，断言它为空对任何状态都成立，
  // 改坏 direct_response 也不会红。一条永真断言比没有断言更糟——它让人以为覆盖了。
  // 证据区该不该出现，由下面针对 evidencePanel 的断言负责。
  // 右侧证据列保留（抽掉会让中间栏宽度跳变），但空态要说得出自己为什么空：
  // 一个不解释自己的空白区域和一个不解释自己的灰色禁用按钮是同一类问题。
  const evidencePanel = screen.getByLabelText("引用来源");
  expect(within(evidencePanel).getByText("本次是对话回复")).toBeInTheDocument();
  expect(within(evidencePanel).getByText(/问候与寒暄不触发检索，所以这里没有引用证据/)).toBeInTheDocument();
  expect(within(evidencePanel).queryByText("回答后查看证据")).toBeNull();
});

test("引用按知识库与 Web 分区，Web 卡片给域名、抓取时间和外链", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default/query/stream") {
      return Promise.resolve(sse([{ event: "final", data: {
        ...QUERY_RESULT,
        answer: "索引版本是 v3。[来源 1][来源 2]",
        sources: [...QUERY_RESULT.sources, WEB_SOURCE],
      } }]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/chat?knowledge_base_id=kb_default");
  render(<App />);
  await userEvent.type(await screen.findByLabelText("向知识库提问"), "最新索引版本是什么？");
  await userEvent.click(screen.getByRole("button", { name: /提问/ }));

  const kbGroup = await screen.findByLabelText("知识库引用");
  const webGroup = screen.getByLabelText("Web 引用");
  expect(within(kbGroup).getByText("profile.md")).toBeInTheDocument();
  // Web 卡片不能把 URL 当成知识库文件名展示：标题是网页标题，定位是域名 + 抓取时间。
  expect(within(webGroup).getByText("索引版本发布说明 · Web")).toBeInTheDocument();
  expect(within(webGroup).getByText(/^docs\.example\.com · 抓取于/)).toBeInTheDocument();
  expect(within(webGroup).getByRole("button", { name: "打开 Web 原文" })).toBeInTheDocument();
  // 分组后仍然用原始下标编号：答案里的 [来源 2] 必须指向这张卡（锚点 #source-2）。
  expect(within(webGroup).getByText("2")).toBeInTheDocument();
  expect(within(kbGroup).queryByText("2")).toBeNull();
});

test("Web 执行状态只认模块轨迹：七个 decision 各有文案，reason_code 优先", () => {
  const status = (patch: Partial<WebExecutionState>) =>
    describeWebExecution({ decision: null, resultCount: null, webCount: null, reasonCodes: [], ...patch });

  // 旧记录没有 retrieval.web_policy 轨迹时说"不知道"，不拿 Web 来源数猜。
  expect(status({})).toBe("历史记录未保存 Web 执行状态");
  expect(status({ decision: "disabled" })).toBe("Web 未启用");
  expect(status({ decision: "not_needed" })).toBe("KB 证据已满足，未触发 Web");
  expect(status({ decision: "scope_limited" })).toBe("当前检索范围禁止 Web");
  expect(status({ decision: "provider_unavailable" })).toBe("Web Provider 未配置");
  expect(status({ decision: "failed" })).toBe("Web 搜索失败");
  expect(status({ decision: "no_result" })).toBe("Web 未找到合格结果");
  expect(status({ decision: "executed", resultCount: 3, webCount: 0 })).toBe("已检索，结果未被采用");
  expect(status({ decision: "executed", resultCount: 3, webCount: 2 })).toBe("已采用 2 条 Web 证据");
  // 流式途中 Final Gate 还没出结论，采用数未知——只说搜到几条，不谎报已采用。
  expect(status({ decision: "executed", resultCount: 3 })).toBe("已检索到 3 条 Web 结果");
  // 时效问题没有 KB anchor 时仍会打一次 Web（spec 5.3），decision 是 executed 而结论必拒。
  // 只说"已检索"，用户就会看到"已联网"却说不出为什么被拒答。
  expect(status({ decision: "executed", resultCount: 3, webCount: 0, reasonCodes: ["freshness_required", "no_kb_anchor"] }))
    .toBe("已检索到 3 条 Web 结果，但知识库缺少可锚定证据，未被采用");
  expect(status({ decision: "disabled", reasonCodes: ["no_kb_anchor"] })).toBe("Web 未启用；知识库缺少可锚定证据");
});

test("Web 已检索却因缺少知识库锚点被拒答时，技术抽屉说得出原因", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default/query/stream") {
      return Promise.resolve(sse([{ event: "final", data: {
        ...QUERY_RESULT,
        answer: "现有知识库和受控外部来源的证据不足，无法可靠回答该问题。",
        answer_status: "insufficient_evidence",
        sources: [],
        module_executions: [
          moduleExecution(1, "evidence.preliminary_gate", { outcome: "needs_web", sufficient: false, kb_count: 0, evidence_count: 0, requires_freshness: true, web_available: true, reason_codes: ["freshness_required", "no_kb_anchor"] }),
          moduleExecution(2, "retrieval.web_policy", { decision: "executed", result_count: 3, reason_code: null }),
          moduleExecution(3, "evidence.final_gate", { outcome: "reject", sufficient: false, selected_count: 0, kb_count: 0, web_count: 0, requires_freshness: true, freshness_verified: false, reason_codes: ["freshness_required", "no_kb_anchor"] }),
        ],
      } }]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/chat?knowledge_base_id=kb_default");
  render(<App />);
  await userEvent.type(await screen.findByLabelText("向知识库提问"), "截至今天的最新公告是什么？");
  await userEvent.click(screen.getByRole("button", { name: /提问/ }));

  expect(await screen.findByText("证据不足")).toBeInTheDocument();
  await userEvent.click(screen.getByText("查看技术细节"));
  expect(screen.getByText("联网：已检索到 3 条 Web 结果，但知识库缺少可锚定证据，未被采用")).toBeInTheDocument();
  expect(screen.getByText(/Final Gate 拒答/)).toBeInTheDocument();
  // kb_count / web_count 是门禁数到的**合格**数，拒答时 selected 为空。仍叫「最终证据」
  // 就会出现「最终证据：知识库 0 条」这种自己和自己打架的话（CLAUDE.md 第一条）。
  expect(screen.getByText(/合格证据：知识库 0 条 \/ Web 0 条 · Final Gate 拒答/)).toBeInTheDocument();
  expect(screen.queryByText(/最终证据/)).toBeNull();
});

test("需要澄清时抽屉说本次未进入检索管线，而不是谎称历史记录没保存", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default/query/stream") {
      return Promise.resolve(sse([{ event: "final", data: {
        ...QUERY_RESULT,
        answer: "当前问题缺少明确的查询对象，请补充要查询的资料、对象或范围。",
        answer_status: "insufficient_evidence",
        sources: [],
        query_metadata: null,
        generation_governance: null,
        latency_ms: { routing: 3, total: 4 },
        routing: {
          intent: null, confidence: 1, reason: "缺少明确的查询对象", control_outcome: "clarify",
          original_question: "那个呢", effective_question: "那个呢", follow_up_rewritten: false,
          requires_freshness: false, requires_web: false, classifier_model: null, fallback_used: false,
        },
        // clarify 在 service.py:398 早返回，轨迹里只有 Router 与一条 skipped 的 evidence.gate，
        // 永远不会有 retrieval.web_policy。
        module_executions: [
          moduleExecution(1, "intent.router", {}),
          moduleExecution(2, "evidence.gate", {}, "skipped"),
        ],
      } }]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/chat?knowledge_base_id=kb_default");
  render(<App />);
  await userEvent.type(await screen.findByLabelText("向知识库提问"), "那个呢");
  await userEvent.click(screen.getByRole("button", { name: /提问/ }));

  expect(await screen.findByText("证据不足")).toBeInTheDocument();
  await userEvent.click(screen.getByText("查看技术细节"));
  // 一条刚刚发生的查询不能被告知"历史记录未保存"——没有 web_policy 轨迹的原因是
  // 这次压根没进检索管线，不是记录丢了。
  expect(screen.getByText("联网：本次未进入检索管线，没有发起 Web 检索")).toBeInTheDocument();
  expect(screen.queryByText(/历史记录未保存 Web 执行状态/)).toBeNull();
  // 同一个判据管两行：没进管线就不能说「返回 0 条来源，并按融合排序结果展示」——
  // 那次融合排序根本没发生过。
  expect(screen.queryByText(/并按融合排序结果展示/)).toBeNull();
});

test("历史记录没有 Web 轨迹时如实说不知道，并保留时效未验证状态", async () => {
  const conversationId = "conv_1234567890abcdef";
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === `/api/knowledge-bases/kb_default/conversations/${conversationId}`) {
      return Promise.resolve(json({
        conversation_id: conversationId, knowledge_base_id: "kb_default", title: "旧记录",
        created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
        records: [{
          record_id: "ans_old", conversation_id: conversationId, knowledge_base_id: "kb_default",
          question: "最新索引版本是什么？", status: "success", answer: "索引版本停留在 v1。",
          sources: [], latency_ms: { total: 30 }, models: {}, model_metadata: {},
          prompt_version: null, prompt_hash: null, answer_status: "answered_stale",
          generation_governance: null, query_metadata: null, error_code: null, error_message: null,
          created_at: "2026-09-01T00:00:00Z",
          // 旧记录没有 module_summary，技术抽屉不得靠 Web 来源数猜联网状态。
        }],
      }));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", `/chat/${conversationId}?knowledge_base_id=kb_default`);
  render(<App />);

  // 会话刷新之后 AnswerPanel 会卸载，状态只剩历史气泡能讲——spec 10.1 要求它一直在。
  expect(await screen.findByText("索引版本停留在 v1。")).toBeInTheDocument();
  expect(screen.getByText("时效未验证")).toBeInTheDocument();
  expect(screen.getByText(/未取得可信的当前 Web 证据/)).toBeInTheDocument();

  await userEvent.click(screen.getByText("查看技术细节"));
  expect(screen.getByText("联网：历史记录未保存 Web 执行状态")).toBeInTheDocument();
  expect(screen.queryByText(/未采用 Web 证据/)).toBeNull();
});

// 设计稿 13.3 的验收场景在页面上的落点。场景 2（时效降级）、4（缺少 KB anchor）、
// 5（问候）已由上面三条覆盖；下面两条补的是场景 1 与场景 3——它们的 Web 决策
// （not_needed / scope_limited）此前只有 describeWebExecution 的纯函数用例，
// 没有一条渲染用例证明这两句话真的会出现在抽屉里。
test("验收场景 1：KB 证据足够时抽屉说明未触发 Web，跳过的模块仍留在时间线上", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default/query/stream") {
      return Promise.resolve(sse([{ event: "final", data: {
        ...QUERY_RESULT,
        answer: "索引版本是 v1。[来源 1]",
        module_executions: [
          moduleExecution(1, "evidence.preliminary_gate", { outcome: "pass", sufficient: true, kb_count: 1, evidence_count: 1, requires_freshness: false, web_available: true, reason_codes: ["kb_evidence_sufficient"] }),
          moduleExecution(2, "retrieval.web_policy", { decision: "not_needed", result_count: 0, reason_code: "kb_evidence_sufficient" }, "skipped"),
          moduleExecution(3, "evidence.fuse", { kb_count: 1, web_count: 0, merged_count: 1, reason_code: "web_not_executed" }, "skipped"),
          moduleExecution(4, "rerank.unified", { candidate_count: 1, selected_count: 1, reason_code: "web_not_executed" }, "skipped"),
          moduleExecution(5, "evidence.final_gate", { outcome: "pass", sufficient: true, selected_count: 1, kb_count: 1, web_count: 0, requires_freshness: false, freshness_verified: false, reason_codes: ["kb_evidence_sufficient"] }),
        ],
      } }]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/chat?knowledge_base_id=kb_default");
  render(<App />);
  await userEvent.type(await screen.findByLabelText("向知识库提问"), "索引版本是什么？");
  await userEvent.click(screen.getByRole("button", { name: /提问/ }));

  expect(await screen.findByText("已基于证据回答")).toBeInTheDocument();
  await userEvent.click(screen.getByText("查看技术细节"));
  // 「没联网」要说清是"不需要"而不是"不能"——后者会让管理员去查 Web 配置。
  expect(screen.getByText("联网：KB 证据已满足，未触发 Web")).toBeInTheDocument();
  expect(screen.getByText(/初步门禁：通过 · 知识库合格 1 条 · 原因：知识库证据充足/)).toBeInTheDocument();
  expect(screen.getByText(/最终证据：知识库 1 条 \/ Web 0 条 · Final Gate 通过/)).toBeInTheDocument();
  // 被跳过的模块不能从时间线上消失：轨迹是抽屉判断联网状态的唯一依据。
  const timeline = screen.getByLabelText("模块执行时间线");
  expect(within(timeline).getByText("3. evidence.fuse")).toBeInTheDocument();
  expect(within(timeline).getByText("4. rerank.unified")).toBeInTheDocument();
});

test("验收场景 3：检索范围限定在知识库内时抽屉说明 Web 被范围禁止", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default/query/stream") {
      return Promise.resolve(sse([{ event: "final", data: {
        ...QUERY_RESULT,
        answer: "该文档记录的切片参数是 700/100。[来源 1]",
        module_executions: [
          moduleExecution(1, "evidence.preliminary_gate", { outcome: "pass", sufficient: true, kb_count: 1, evidence_count: 1, requires_freshness: false, web_available: false, reason_codes: ["kb_evidence_sufficient"] }),
          moduleExecution(2, "retrieval.web_policy", { decision: "scope_limited", result_count: 0, reason_code: "knowledge_base_scope_locked" }, "skipped"),
          moduleExecution(3, "evidence.final_gate", { outcome: "pass", sufficient: true, selected_count: 1, kb_count: 1, web_count: 0, requires_freshness: false, freshness_verified: false, reason_codes: ["kb_evidence_sufficient"] }),
        ],
      } }]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/chat?knowledge_base_id=kb_default");
  render(<App />);
  await userEvent.type(await screen.findByLabelText("向知识库提问"), "查找指定文档中的索引配置");
  await userEvent.click(screen.getByRole("button", { name: /提问/ }));

  expect(await screen.findByText("已基于证据回答")).toBeInTheDocument();
  await userEvent.click(screen.getByText("查看技术细节"));
  expect(screen.getByText("联网：当前检索范围禁止 Web")).toBeInTheDocument();
  // 范围锁住时不会有 Web 引用分区——分区只在该类来源非空时渲染。
  expect(screen.getByLabelText("知识库引用")).toBeInTheDocument();
  expect(screen.queryByLabelText("Web 引用")).toBeNull();
});

test("回答评测页只读展示正式指标", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    if (String(input) === "/api/auth/me") return Promise.resolve(json(admin));
    if (String(input) === "/api/evaluations/answers/reports") return Promise.resolve(json([answerSummary]));
    if (String(input) === "/api/evaluations/answers/reports/answer-official")
      return Promise.resolve(
        json({
          ...answerSummary,
          prompt_hash: "a".repeat(64),
          parameters: { temperature: 0 },
          case_count: 30,
          metrics: {
            answer_correctness: {
              value: 1,
              threshold: 0.8,
              baseline: null,
              passed: true,
              regressed: false,
              direction: "minimum",
            },
            unsupported_claim_rate: {
              value: 0,
              threshold: 0.05,
              baseline: null,
              passed: true,
              regressed: false,
              direction: "maximum",
            },
          },
        }),
      );
    return Promise.resolve(json({}, 404));
  });
  window.history.replaceState({}, "", "/evaluation?view=reports&report=answer-official");
  render(<App />);
  const detail = await screen.findByRole("dialog", { name: "正式报告详情" });
  expect(await within(detail).findByText("答案正确性")).toBeInTheDocument();
  expect(detail).toHaveTextContent("回答报告");
  expect(detail).toHaveTextContent("通过");
  expect(detail).toHaveTextContent("无支持声明率");
  expect(detail).toHaveTextContent("实际值 100.0% · 门槛 ≥ 80.0%");
  expect(detail).toHaveTextContent("实际值 0.0% · 门槛 ≤ 5.0%");
  expect(detail).toHaveTextContent("回答报告是横向质量证据，不参与索引版本放行");
  expect(detail).not.toHaveAttribute("aria-modal");
});

test("检索报告先解释数据完整性再解释质量失败", async () => {
  const failedReport = {
    ...retrievalReport,
    report_id: "corpus-20260915T045543Z",
    dataset_id: "rag-enterprise-corpus-paraphrased",
    dataset_version: "1.1.0",
    sample_count: 145,
    query_count: 145,
    passed: false,
    failed_metrics: ["recall_at_5"],
    dataset_evidence: {
      document_count: 10,
      query_count: 145,
      integrity_status: "passed",
      integrity_basis: "current_registry",
    },
    recall_at_5: { value: 0.429, threshold: 0.7, baseline: null, passed: false, regressed: false },
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url === "/api/auth/me") return Promise.resolve(json(admin));
    if (url === "/api/evaluations") return Promise.resolve(json([failedReport]));
    if (url === "/api/evaluations/answers/reports") return Promise.resolve(json([]));
    if (url === "/api/evaluations/corpus-20260915T045543Z") return Promise.resolve(json(failedReport));
    if (url === "/api/evaluation-center/reports/corpus-20260915T045543Z/associations") {
      return Promise.resolve(json({
        report_id: failedReport.report_id,
        evaluation_type: "retrieval",
        origin_evaluation_run_id: "eval_1",
        origin_version: null,
        compatible_versions: [],
        validation_usages: [],
      }));
    }
    return Promise.resolve(json({}, 404));
  });
  window.history.replaceState({}, "", "/evaluation?view=reports&report=corpus-20260915T045543Z");
  render(<App />);

  const detail = await screen.findByRole("dialog", { name: "正式报告详情" });
  expect(detail).toHaveTextContent("正式证据");
  expect(detail).toHaveTextContent("数据完整");
  expect(detail).toHaveTextContent("质量未通过");
  expect(detail).toHaveTextContent("10/10 份文档");
  expect(detail).toHaveTextContent("145/145 条问题");
  expect(detail).toHaveTextContent("评测数据完整，但 1 项质量指标未达到冻结阈值；失败不是由缺少数据导致。");
  expect(detail).toHaveTextContent("当前注册数据集校验");
});

test("历史检索报告未记录完整性时不显示为数据完整", async () => {
  const legacyReport = {
    ...retrievalReport,
    report_id: "legacy-report",
    dataset_evidence: {
      document_count: null,
      query_count: 20,
      integrity_status: "unknown",
      integrity_basis: "unavailable",
    },
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url === "/api/auth/me") return Promise.resolve(json(admin));
    if (url === "/api/evaluations") return Promise.resolve(json([legacyReport]));
    if (url === "/api/evaluations/answers/reports") return Promise.resolve(json([]));
    if (url === "/api/evaluations/legacy-report") return Promise.resolve(json(legacyReport));
    if (url === "/api/evaluation-center/reports/legacy-report/associations") {
      return Promise.resolve(json({ report_id: "legacy-report", evaluation_type: "retrieval", origin_evaluation_run_id: null, origin_version: null, compatible_versions: [], validation_usages: [] }));
    }
    return Promise.resolve(json({}, 404));
  });
  window.history.replaceState({}, "", "/evaluation?view=reports&report=legacy-report");
  render(<App />);

  const detail = await screen.findByRole("dialog", { name: "正式报告详情" });
  expect(detail).toHaveTextContent("完整性未记录");
  expect(detail).toHaveTextContent("报告记录了 20 条问题；未保存或无法复核数据集完整性，不按“数据完整”处理。");
  expect(within(detail).queryByText("数据完整", { exact: true })).not.toBeInTheDocument();
});

test("报告详情遮罩 Hover 时保持半透明背景", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url === "/api/auth/me") return Promise.resolve(json(admin));
    if (url === "/api/evaluations/answers/reports") return Promise.resolve(json([answerSummary]));
    if (url === "/api/evaluations/answers/reports/answer-official") {
      return Promise.resolve(json({
        ...answerSummary,
        prompt_hash: "a".repeat(64),
        parameters: { temperature: 0 },
        case_count: 30,
        metrics: {},
      }));
    }
    return Promise.resolve(json({}, 404));
  });
  window.history.replaceState({}, "", "/evaluation?view=reports&report=answer-official");
  render(<App />);

  const backdrop = await screen.findByRole("button", { name: "关闭报告详情" });
  expect(backdrop).toHaveClass("bg-ink/35", "hover:bg-ink/35");
  expect(backdrop).not.toHaveClass("hover:bg-brand-subtle");
});

test("从正式报告原地打开索引版本弹框且地址保持不变", async () => {
  const retrievalSummary = {
    report_id: "retrieval-official",
    dataset_id: "retrieval",
    dataset_version: "2.0.0",
    commit: "a".repeat(40),
    run_at: "2026-08-30T00:00:00Z",
    models: {},
    official: true,
    passed: true,
    config_fingerprint: "a".repeat(64),
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/evaluations") return Promise.resolve(json([retrievalSummary]));
    if (url === "/api/evaluations/retrieval-official") {
      const metric = { value: 1, threshold: 0.7, baseline: null, passed: true, regressed: false };
      return Promise.resolve(json({
        ...retrievalSummary,
        parameters: {},
        query_count: 10,
        recall_at_5: metric,
        recall_at_10: metric,
        vector_mrr: metric,
        rerank_mrr: metric,
        rerank_recall_at_5: metric,
        hybrid_mrr: null,
        ndcg_at_5: metric,
        ndcg_at_10: metric,
        metadata_filter_accuracy: metric,
        query_rewrite_success_rate: null,
        query_rewrite_fallback_rate: null,
        no_result_rate: null,
        acl_leak_count: 0,
      }));
    }
    if (url === "/api/evaluation-center/reports/retrieval-official/associations") {
      return Promise.resolve(json({
        report_id: "retrieval-official",
        evaluation_type: "retrieval",
        origin_evaluation_run_id: "eval_origin",
        origin_version: {
          knowledge_base_id: "kb_default",
          index_version_id: "iv_active",
          version_no: 3,
          status: "active",
          config_fingerprint: "a".repeat(64),
        },
        compatible_versions: [{
          knowledge_base_id: "kb_default",
          index_version_id: "iv_previous",
          version_no: 2,
          status: "previous",
          config_fingerprint: "a".repeat(64),
        }],
        validation_usages: [{
          validation_report_id: "vr_passed",
          knowledge_base_id: "kb_default",
          index_version_id: "iv_previous",
          status: "pass",
          created_at: "2026-08-30T00:02:00Z",
        }],
      }));
    }
    if (url === "/api/knowledge-bases/kb_default/index-versions") {
      return Promise.resolve(json([{
        index_version_id: "iv_active",
        status: "active",
        chunking_version: "semantic-v1",
        parser_version: "registry-v1",
        embedding_model: "text2vec",
        embedding_dimension: 768,
        processing_options: {},
        config_fingerprint: "a".repeat(64),
        evaluation_report_id: "retrieval-official",
        validation_report_id: null,
        document_snapshot_id: "snapshot_1",
        rebuild_batch_id: null,
        version_no: 3,
        creation_reason: "config_changed",
        force_reason: null,
        requested_by: admin.user_id,
        config_snapshot: {},
        component_manifest: {},
        release_fingerprint: "b".repeat(64),
        config_completeness: "complete",
        legacy_migrated: false,
        excluded_documents_acknowledged: false,
        created_at: "2026-08-30T00:00:00Z",
        activated_at: "2026-08-30T00:01:00Z",
        retired_at: null,
        cleaned_at: null,
      }]));
    }
    if (url === "/api/knowledge-bases/kb_default/index-versions/iv_active/validations") return Promise.resolve(json([]));
    if (url === "/api/knowledge-bases/kb_default/index-versions/iv_active/events") return Promise.resolve(json([]));
    if (url === "/api/knowledge-bases/kb_default/index-versions/iv_active/evaluation-runs") return Promise.resolve(json([]));
    if (url === "/api/knowledge-bases/kb_default/index-versions/iv_active/evidence-chain") {
      return Promise.resolve(json({
        knowledge_base_id: "kb_default",
        index_version_id: "iv_active",
        version: { index_version_id: "iv_active", version_no: 3, status: "active", config_fingerprint: "a".repeat(64) },
        evaluation_run: null,
        formal_report: null,
        validation_report: null,
        activation: null,
        governance: { traceability: "missing", configuration: "unknown", validation: "missing", release: "blocked", reasons: [] },
      }));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/evaluation?view=reports&report=retrieval-official");
  render(<App />);

  const reportDetail = await screen.findByRole("dialog", { name: "正式报告详情" });
  expect(reportDetail).toHaveTextContent("当前生效");
  expect(reportDetail).toHaveTextContent("同配置版本 · 1");
  expect(reportDetail).toHaveTextContent("v2 · iv_previous · 上一版本");
  expect(reportDetail).toHaveTextContent("已通过");
  await userEvent.click(await within(reportDetail).findByRole("button", { name: "v3 · iv_active" }));
  expect(await screen.findByRole("dialog", { name: "索引版本 v3" })).toBeInTheDocument();
  expect(window.location.pathname + window.location.search).toBe(
    "/evaluation?view=reports&report=retrieval-official",
  );
  // Radix 会在模态框打开时把背景内容设为 aria-hidden；报告详情应仍保留在 DOM，
  // 关闭子弹框后恢复，而不是通过页面跳转重新加载。
  expect(globalThis.document.querySelector('[aria-label="正式报告详情"]')).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "关闭弹框" }));

  expect(await screen.findByRole("dialog", { name: "正式报告详情" })).toBeInTheDocument();
  expect(window.location.pathname + window.location.search).toBe(
    "/evaluation?view=reports&report=retrieval-official",
  );
});

test("评测中心用三个可深链工作区承载质量治理", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/evaluation");
  render(<App />);

  expect(await screen.findByRole("tab", { name: "质量总览" })).toHaveAttribute("data-state", "active");
  expect(screen.getByRole("tab", { name: "正式报告" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "运行观测" })).toBeInTheDocument();
  expect(await screen.findByText("最新正式证据状态")).toBeInTheDocument();
  expect(screen.getByText("正式证据已覆盖 2/2 个必需质量域，当前没有质量阻塞。")).toBeInTheDocument();
  expect(screen.getByText("20 条问题")).toBeInTheDocument();
  expect(screen.getByText("30 个案例")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "查看检索质量正式证据" })).toBeInTheDocument();
  expect(screen.getByText(/汇总于/)).toBeInTheDocument();
  expect(screen.queryByText("retrieval-official")).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("tab", { name: "运行观测" }));
  expect(await screen.findByRole("tab", { name: "运行观测" })).toHaveAttribute("data-state", "active");
  expect(await screen.findByRole("heading", { name: "Data Sync" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "RAG Runtime" })).toBeInTheDocument();
  expect(screen.getByText("统计当前可访问知识库最近 1,000 个同步批次。")).toBeInTheDocument();
  expect(screen.getByText("统计当前可访问知识库的全部历史在线执行记录。")).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Data Sync" })).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "RAG Runtime" })).not.toBeInTheDocument();
  const syncResults = screen.getByLabelText("Data Sync 同步结果");
  for (const label of ["新增", "更新", "删除", "跳过", "失败", "重试"]) {
    expect(within(syncResults).getByText(label)).toBeInTheDocument();
  }
  expect(screen.getAllByText("2").length).toBeGreaterThan(0);
  expect(new URLSearchParams(window.location.search).get("view")).toBe("observations");
});

test("质量总览清楚区分数据完整与质量未通过", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/evaluation-center/overview") {
      return Promise.resolve(json({
        passed: false,
        status: "failed",
        report_count: 5,
        required_scopes: ["retrieval", "answer"],
        available_scopes: ["retrieval", "answer"],
        missing_scopes: [],
        failed_scopes: ["retrieval"],
        generated_at: "2026-09-20T00:00:00Z",
        retrieval_report: {
          ...retrievalReport,
          report_id: "corpus-20260915T045543Z",
          dataset_id: "rag-enterprise-corpus-paraphrased",
          dataset_version: "1.1.0",
          sample_count: 145,
          passed: false,
          failed_metrics: ["recall_at_5", "vector_mrr"],
        },
        answer_report: answerSummary,
      }));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/evaluation");
  render(<App />);

  expect(await screen.findByText("存在 1 个质量阻塞")).toBeInTheDocument();
  expect(screen.getByText("正式证据已覆盖 2/2 个必需质量域；缺失证据不会按“通过”处理。")).toBeInTheDocument();
  expect(screen.getByText("未达到冻结阈值：Recall@5、Vector MRR")).toBeInTheDocument();
  expect(screen.getByText("145 条问题")).toBeInTheDocument();
  expect(screen.queryByText("corpus-20260915T045543Z")).not.toBeInTheDocument();
});

test("质量总览查看正式证据只打开弹框且不切换工作区", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/evaluation-center/overview") {
      return Promise.resolve(json({
        passed: true,
        status: "passed",
        report_count: 2,
        required_scopes: ["retrieval", "answer"],
        available_scopes: ["retrieval", "answer"],
        missing_scopes: [],
        failed_scopes: [],
        generated_at: "2026-09-20T00:00:00Z",
        retrieval_report: retrievalReport,
        answer_report: answerSummary,
      }));
    }
    if (url === "/api/evaluations/retrieval-official") return Promise.resolve(json(retrievalReport));
    if (url === "/api/evaluation-center/reports/retrieval-official/associations") {
      return Promise.resolve(json({
        report_id: "retrieval-official",
        evaluation_type: "retrieval",
        origin_evaluation_run_id: "eval_1",
        origin_version: null,
        compatible_versions: [],
        validation_usages: [],
      }));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/evaluation");
  render(<App />);

  await userEvent.click(await screen.findByRole("button", { name: "查看检索质量正式证据" }));

  expect(await screen.findByRole("dialog", { name: "正式报告详情" })).toHaveTextContent("retrieval-official");
  const overviewTab = Array.from(globalThis.document.querySelectorAll<HTMLElement>('[role="tab"]')).find((element) => element.textContent?.includes("质量总览"));
  const reportsTab = Array.from(globalThis.document.querySelectorAll<HTMLElement>('[role="tab"]')).find((element) => element.textContent?.includes("正式报告"));
  expect(overviewTab).toHaveAttribute("data-state", "active");
  expect(reportsTab).toHaveAttribute("data-state", "inactive");
  expect(window.location.pathname + window.location.search).toBe("/evaluation");
});

test("运行观测没有同步批次时显示空态而不是零失败率", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input).startsWith("/api/evaluation-center/pipeline")) {
      return Promise.resolve(json({
        run_count: 0,
        added_count: 0,
        updated_count: 0,
        deleted_count: 0,
        skipped_count: 0,
        failed_count: 0,
        retry_count: 0,
        failure_rate: 0,
        average_duration_ms: 0,
        rag_profiles: [],
      }));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/evaluation?view=observations");
  render(<App />);

  expect(await screen.findByText("暂无 Data Sync 观测数据")).toBeInTheDocument();
  expect(screen.queryByText("批次失败率")).not.toBeInTheDocument();
});

test("直接进入正式报告仍显示最新正式证据状态", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/evaluation?view=reports");
  render(<App />);

  expect(await screen.findByText("正式质量：通过")).toBeInTheDocument();
  expect(screen.getByText("当前展示 1 份正式报告；选择报告查看指标证据及其治理关联。")).toBeInTheDocument();
});

test("浏览器历史变化会同步关闭和恢复正式报告弹框", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/evaluations/answers/reports/answer-official") {
      return Promise.resolve(json({
        ...answerSummary,
        prompt_hash: "a".repeat(64),
        parameters: { temperature: 0 },
        case_count: 30,
        metrics: {},
      }));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/evaluation?view=reports&report=answer-official");
  render(<App />);
  expect(await screen.findByRole("dialog", { name: "正式报告详情" })).toBeInTheDocument();

  window.history.pushState({}, "", "/evaluation?view=reports");
  window.dispatchEvent(new PopStateEvent("popstate"));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "正式报告详情" })).not.toBeInTheDocument());

  window.history.pushState({}, "", "/evaluation?view=reports&report=answer-official");
  window.dispatchEvent(new PopStateEvent("popstate"));
  expect(await screen.findByRole("dialog", { name: "正式报告详情" })).toBeInTheDocument();
});

test("Bad Case 是独立菜单与独立路由", async () => {
  // Bad Case 不是看指标，而是一条完整的治理工作流：发现 → 分类 → 定位根因 → 修复
  // → 回归 → 关闭。它有自己的工作场景，所以配得上一个左侧菜单。
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/evaluation/bad-cases");
  render(<App />);

  expect(await screen.findByText("为什么没有召回？")).toBeInTheDocument();
  expect(screen.getByLabelText("Bad Case 筛选与统计")).toHaveClass("flex-col");
  expect(screen.getByLabelText("Bad Case 筛选条件")).toHaveClass("flex-wrap");
  expect(screen.getByLabelText("Bad Case 状态筛选")).toBeInTheDocument();
  expect(screen.getByLabelText("Bad Case 严重级别筛选")).toBeInTheDocument();
  expect(screen.getByText("治理详情")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "治理详情" }));
  expect(screen.getByText("来源证据")).toBeInTheDocument();
  expect(screen.getByText(/线上回答$/)).toBeInTheDocument();
  expect(screen.getByText("ans_1")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Bad Case" })).toHaveAttribute("aria-current", "page");
});

test("索引治理中的正式质量报告原地打开弹框", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(governanceCrossLinkFetch);
  window.history.replaceState({}, "", "/knowledge-bases/kb_default?tab=versions");
  render(<App />);

  await userEvent.click(await screen.findByRole("button", { name: "retrieval-official" }));

  expect(await screen.findByRole("dialog", { name: "正式报告详情" })).toHaveTextContent("retrieval-official");
  expect(window.location.pathname).toBe("/knowledge-bases/kb_default");
  expect(window.location.search).toBe("?tab=versions");
});

test("链路验收是独立菜单与独立路由", async () => {
  // 链路验收承担版本放行职责，结论是 PASS / BLOCKED，与「看指标」不是一件事。
  vi.spyOn(globalThis, "fetch").mockImplementation(governanceCrossLinkFetch);
  window.history.replaceState({}, "", "/evaluation/acceptance");
  render(<App />);

  expect(await screen.findByText("缺少 S3 兼容外部数据源。")).toBeInTheDocument();
  expect(screen.queryByText(/\{"external_source_count"/)).not.toBeInTheDocument();
  expect(screen.getByText("当前 Schema")).toBeInTheDocument();
  expect(screen.getByText("要求 Schema")).toBeInTheDocument();
  expect(screen.getByText("回归案例")).toBeInTheDocument();
  expect(screen.getByText("待验证回归")).toBeInTheDocument();
  expect(screen.queryByText("schema_version")).not.toBeInTheDocument();
  expect(screen.queryByText("regression_unverified_count")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "链路验收" })).toHaveAttribute("aria-current", "page");
  await userEvent.click(screen.getByRole("button", { name: "查看正式报告 retrieval-official" }));
  expect(await screen.findByRole("dialog", { name: "正式报告详情" })).toHaveTextContent("retrieval-official");
  expect(window.location.pathname).toBe("/evaluation/acceptance");
  expect(window.location.search).toBe("");
});

test("链路验收中的索引版本原地打开弹框", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(governanceCrossLinkFetch);
  window.history.replaceState({}, "", "/evaluation/acceptance");
  render(<App />);

  await userEvent.click(await screen.findByRole("button", { name: "查看索引版本 iv_active" }));

  expect(await screen.findByRole("dialog", { name: "索引版本 v4" })).toHaveTextContent("iv_active");
  expect(window.location.pathname).toBe("/evaluation/acceptance");
  expect(window.location.search).toBe("");
});

test("运行链路验收时立即显示处理中并阻止重复提交", async () => {
  let resolveStart: ((response: Response) => void) | undefined;
  let startCount = 0;
  const pendingStart = new Promise<Response>((resolve) => {
    resolveStart = resolve;
  });
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/evaluation-center/acceptance-runs" && init?.method === "POST") {
      startCount += 1;
      return pendingStart;
    }
    return governanceCrossLinkFetch(input, init);
  });
  window.history.replaceState({}, "", "/evaluation/acceptance");
  render(<App />);

  const trigger = await screen.findByRole("button", { name: "运行默认知识库验收" });
  await userEvent.click(trigger);

  expect(trigger).toBeDisabled();
  expect(trigger).toHaveAttribute("aria-busy", "true");
  expect(trigger).toHaveTextContent("验收运行中…");
  await userEvent.click(trigger);
  expect(startCount).toBe(1);

  resolveStart?.(json({
    acceptance_run_id: "acc_new",
    knowledge_base_id: "kb_default",
    status: "blocked",
    commit_sha: "local-working-tree",
    schema_version: 42,
    steps: [],
    limitations: ["缺少正式证据。"],
    created_by: admin.user_id,
    created_at: "2026-09-24T07:30:00Z",
  }, 201));

  expect(await screen.findByText("acc_new")).toBeInTheDocument();
  expect(trigger).toBeEnabled();
  expect(trigger).toHaveTextContent("运行默认知识库验收");
});

test("概览页质量监控入口打开回答正式报告详情弹框", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/evaluations/answers/reports/answer-official") {
      return Promise.resolve(json({
        ...answerSummary,
        prompt_hash: "a".repeat(64),
        parameters: { temperature: 0 },
        case_count: 30,
        metrics: {},
      }));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/overview");
  render(<App />);

  await userEvent.click(await screen.findByRole("button", { name: /查看回答评测详情/ }));

  const dialog = await screen.findByRole("dialog", { name: "正式报告详情" });
  expect(dialog).toHaveTextContent("回答报告");
  expect(dialog).toHaveTextContent("answer-official");
  expect(window.location.pathname).toBe("/overview");
  expect(window.location.search).toBe("");
  await userEvent.click(screen.getByRole("button", { name: "关闭弹框" }));
  expect(screen.queryByRole("dialog", { name: "正式报告详情" })).not.toBeInTheDocument();
  expect(window.location.pathname + window.location.search).toBe("/overview");
});

test("保留检索评测页且可直接访问", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => (String(input) === "/api/auth/me" ? Promise.resolve(json(admin)) : String(input) === "/api/evaluations" ? Promise.resolve(json([])) : Promise.resolve(json({}, 404))));
  window.history.replaceState({}, "", "/evaluation?view=reports");
  render(<App />);
  expect(await screen.findByText("还没有正式报告。")).toBeInTheDocument();
});

test("页面显示稳定 API 错误", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => (String(input) === "/api/auth/me" ? Promise.resolve(json(admin)) : Promise.resolve(json({ error: { message: "后端不可用" } }, 503))));
  render(<App />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("后端不可用"));
});

test("首次启动可创建管理员并进入工作台", async () => {
  setAccessToken(null);
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/auth/bootstrap" && init?.method === "POST")
      return Promise.resolve(
        json(
          {
            access_token: "new-token",
            token_type: "bearer",
            expires_at: "2026-08-17T00:00:00Z",
            user: admin,
          },
          201,
        ),
      );
    if (url === "/api/auth/bootstrap") return Promise.resolve(json({ required: true }));
    return commonFetch(input, init);
  });
  render(<App />);

  expect(await screen.findByRole("heading", { name: "创建首位管理员" })).toBeInTheDocument();
  const passwordInput = screen.getByLabelText("密码");
  expect(passwordInput).toHaveAttribute("type", "password");
  await userEvent.click(screen.getByRole("button", { name: "显示密码" }));
  expect(passwordInput).toHaveAttribute("type", "text");
  await userEvent.click(screen.getByRole("button", { name: "隐藏密码" }));
  expect(passwordInput).toHaveAttribute("type", "password");
  await userEvent.type(screen.getByLabelText("显示名称"), "测试管理员");
  await userEvent.type(screen.getByLabelText("用户名"), "test-admin");
  await userEvent.type(screen.getByLabelText("密码"), "correct-horse-battery-staple");
  await userEvent.click(screen.getByRole("button", { name: "创建管理员并进入" }));

  expect(await screen.findByRole("heading", { name: "项目概览" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/auth/bootstrap", expect.objectContaining({ method: "POST" }));
});

test("登录失败显示中文错误且不会进入业务页面", async () => {
  setAccessToken(null);
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/auth/bootstrap") return Promise.resolve(json({ required: false }));
    if (String(input) === "/api/auth/login" && init?.method === "POST") return Promise.resolve(json({ error: { message: "用户名或密码错误。" } }, 401));
    return commonFetch(input, init);
  });
  render(<App />);

  expect(await screen.findByRole("heading", { name: "登录 RAG 工作台" })).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("用户名"), "test-admin");
  await userEvent.type(screen.getByLabelText("密码"), "incorrect-password");
  await userEvent.click(screen.getByRole("button", { name: "登录" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("用户名或密码错误");
  expect(screen.queryByRole("heading", { name: "项目概览" })).not.toBeInTheDocument();
});

test("退出后清除当前会话并返回登录入口", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  render(<App />);

  await screen.findByRole("heading", { name: "项目概览" });
  await userEvent.click(screen.getByRole("button", { name: "退出登录" }));

  expect(await screen.findByRole("heading", { name: "登录 RAG 工作台" })).toBeInTheDocument();
});

test("管理员可查看系统状态、模型和恢复边界", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/system");
  render(<App />);
  expect(await screen.findByRole("heading", { name: "系统状态" })).toBeInTheDocument();
  expect(await screen.findByText("服务已就绪")).toBeInTheDocument();
  expect(screen.getByText("服务已就绪").closest("header")).not.toBeNull();
  expect(screen.queryByText("查看服务健康、模型配置、运行指标与恢复边界。")).not.toBeInTheDocument();
  expect(screen.getByText("隔离恢复")).toBeInTheDocument();
  expect(screen.getAllByText("embedding-test")).toHaveLength(2);
});

test("管理员可查看成员授权并对敏感变更二次确认", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/settings/members");
  render(<App />);
  expect(await screen.findByRole("heading", { name: "成员与权限" })).toBeInTheDocument();
  expect(await screen.findByText("资料成员")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "新建成员" }).closest("header")).not.toBeNull();
  await userEvent.click(screen.getAllByRole("button", { name: "停用" }).find((button) => !button.hasAttribute("disabled"))!);
  expect(screen.getByRole("dialog", { name: "停用成员" })).toBeInTheDocument();
});

test("审计页展示哈希链事件且不展示业务正文", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/settings/audit");
  render(<App />);
  expect(await screen.findByRole("heading", { name: "审计记录" })).toBeInTheDocument();
  expect(await screen.findByText("更新成员")).toBeInTheDocument();
  expect(screen.getByText("哈希链由服务端校验").closest("header")).not.toBeNull();
  expect(screen.getByText("bbbbbbbbbbbbbbbb")).toBeInTheDocument();
});

test("普通成员不显示管理导航且直接访问时不请求管理接口", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    if (String(input) === "/api/auth/me") return Promise.resolve(json(member));
    if (String(input) === "/api/health")
      return Promise.resolve(
        json({
          status: "ok",
          version: "1.0.0",
          collection_ready: true,
          generation_ready: false,
          models: {
            embedding: "embedding-test",
            reranker: "reranker-test",
            generation: "gemini-test",
          },
        }),
      );
    return Promise.resolve(json({ error: { message: "不应请求" } }, 500));
  });
  window.history.replaceState({}, "", "/system");
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("无权访问管理页面");
  expect(screen.queryByRole("button", { name: "系统状态" })).not.toBeInTheDocument();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(fetchMock).not.toHaveBeenCalledWith("/api/health/ready", expect.anything());
  expect(fetchMock).not.toHaveBeenCalledWith("/api/system/metrics", expect.anything());
});

test("表单 pattern 在现代浏览器的 v flag 下必须合法", () => {
  // Chrome 125+ 用 `v` flag 解析 pattern 属性，字符类里未转义的 `-` 是语法错误。
  // 非法时浏览器静默丢弃整个 pattern，前端格式校验无声失效——后端仍会校验，
  // 所以这不是安全问题，但用户会先提交、再被拒，而不是当场看到提示。
  const sources = import.meta.glob("./components/*.tsx", { eager: true, query: "?raw", import: "default" });
  const offenders: string[] = [];
  for (const [file, content] of Object.entries(sources)) {
    for (const match of String(content).matchAll(/pattern="([^"]+)"/g)) {
      try {
        new RegExp(`^(?:${match[1]})$`, "v");
      } catch {
        offenders.push(`${file}: ${match[1]}`);
      }
    }
  }
  expect(offenders).toEqual([]);
});

/**
 * 语义色 utility 的前缀。**故意不含 `shadow`**：`--shadow-*` 是另一套命名空间，
 * `shadow-focus` 用的是 `--shadow-focus` 而不是 `--color-focus`，放进来会误报。
 * `text` 与 `--text-*`（字阶）同前缀但不冲突：字阶名（xs/sm/base/md/lg/xl）
 * 和颜色名没有交集，下面的正则只认从 tailwind.css 里现读出来的颜色词根。
 */
const COLOR_UTILITY_PREFIXES = ["text", "bg", "border", "ring", "outline", "fill", "stroke", "divide", "from", "via", "to", "accent", "caret", "placeholder", "decoration"];

/**
 * `node:fs` 的 specifier 写成 `string` 变量而不是字面量：`@types/node` 不在依赖里
 * （`tsconfig.app.json` 的 `types` 只列了 vitest/globals 与 jest-dom），
 * 写成字面量 `npm run typecheck` 会报 TS2591 而不是解析成功。
 */
const NODE_FS: string = "node:fs";
type NodeFs = { readFileSync(path: string, encoding: "utf8"): string };

test("语义色 utility 只能引用 tailwind.css 里真实定义的令牌", async () => {
  // 不存在的令牌会被 Tailwind **静默丢弃**：不报错、typecheck 不报错、构建不报错，
  // 那行文字直接继承父级颜色，告警色整个消失（CLAUDE.md 第七条，仓库踩过一次）。
  // 这条守卫两边都从真正的源现读——tailwind.css 定义了什么、组件引用了什么——
  // 测试里不留任何一份令牌副本，否则它自己就成了第三个需要同步的地方。
  //
  // **覆盖边界（别把它当全量校验）**：下面的正则是用 `defined` 里的词根现拼的，
  // 所以它只抓得到「已定义词根的复合扩展」——`--color-warning` 存在而写了
  // `text-warning-text`，正是仓库踩过的那种。反过来，引用了**全新未定义词根**的
  // utility（比如 `text-highlight` 而 `highlight` 从未在任何地方定义过）
  // 词根不在 `defined` 里，正则根本不会匹配，`referenced` 与 `missing` 都不会记录它。
  // 要覆盖那一类得换一套扫描思路（枚举所有 `前缀-*` 再反查），成本高得多。
  // 把这条守卫当成「全量令牌校验」而放松警惕，比没有守卫更危险。
  //
  // **样式表从磁盘现读，不能用 `import.meta.glob(..., { query: "?raw" })`**：vitest 默认
  // `css: false`，它的 `vitest:css-empty-post`（enforce: post）对所有 CSS id 一律返回
  // `export default ""`，判定正则 `\.css(?:$|\?)` 把 `?raw` 也算进 CSS——glob 确实匹配到了
  // 文件（key 是 `./tailwind.css`），读回来的却是空串，这条守卫先前就红在下面那句
  // `defined.size > 0` 上。`.tsx` 的 `?raw` 不经过这条链路，上面 pattern 那条守卫仍用 glob。
  const fs = (await import(/* @vite-ignore */ NODE_FS)) as NodeFs;
  const stylesheet = fs.readFileSync(`${(import.meta as ImportMeta & { dirname: string }).dirname}/tailwind.css`, "utf8");
  const defined = new Set([...stylesheet.matchAll(/--color-([a-z0-9-]+)\s*:/g)].map((match) => match[1]));
  // 解析不出来就当场红，不能让空集合把后面的断言全变成假绿。
  expect(defined.size).toBeGreaterThan(0);
  // 本轮 answered_stale 的橙色全部依赖这一个令牌：text-warning / bg-warning\/10 /
  // border-warning\/30。它被删或改名，这里立刻红。
  expect(defined.has("warning")).toBe(true);

  const roots = [...new Set([...defined].map((token) => token.split("-")[0]))];
  const utility = new RegExp(`\\b(?:${COLOR_UTILITY_PREFIXES.join("|")})-((?:${roots.join("|")})(?:-[a-z0-9]+)*)`, "g");
  const sources = import.meta.glob("./components/**/*.tsx", { eager: true, query: "?raw", import: "default" });
  const referenced = new Map<string, string[]>();
  for (const [file, content] of Object.entries(sources)) {
    if (file.includes(".test.")) continue;
    for (const match of String(content).matchAll(utility)) {
      referenced.set(match[1], [...(referenced.get(match[1]) ?? []), file]);
    }
  }
  // 扫描确实看见了本轮那三处写法；少了这条，正则写错时整份守卫会静悄悄地一个都不扫，
  // 然后以「没有缺失令牌」的姿态假绿——这正是写这条守卫时先踩到的那个坑。
  const warningComponents = new Set((referenced.get("warning") ?? []).map((file) => file.split("/").pop()));
  expect([...warningComponents]).toEqual(expect.arrayContaining(["AnswerPanel.tsx", "ChatPage.tsx", "TechnicalDrawer.tsx"]));

  // 已知缺陷，**不在本 Task 范围内**：KnowledgeBaseDataSourcesPanel.tsx:261 的同步记录行
  // 悬停底色引用了一个从未定义过的 --color-surface-muted，实际没有悬停效果。
  // 这条断言是精确相等的：修好那一处之后它会变红，提示把这里的豁免一起删掉——
  // 豁免不许悄悄留下来变成第二个腐烂点。
  const missing = [...referenced.keys()].filter((token) => !defined.has(token)).sort();
  expect(missing).toEqual(["surface-muted"]);
});

test("知识库删除走确认弹层，不能删的原因在弹层里讲清楚", async () => {
  // 早先这里是「禁用 + 行内小字说明原因」，列表每行多一句话、行距被撑开。
  // 按 docs/design/ui-foundation-tokens.md 第 3.5 节的删除规则改成：按钮永不禁用，
  // 点开弹层说清后果与下一步——一行小字装不下「删知识库会连带删掉全部资料」。
  const defaultBase = { ...base, allowed_actions: ["detail", "edit"] };
  const withDocuments = {
    ...base, knowledge_base_id: "kb_busy", name: "有资料的库", is_default: false,
    document_count: 7, allowed_actions: ["detail", "edit"],
  };
  const deletable = {
    ...base, knowledge_base_id: "kb_free", name: "空库", is_default: false,
    document_count: 0, index_status: "empty", allowed_actions: ["detail", "edit", "delete"],
  };
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if ((url === "/api/knowledge-bases" || url.startsWith("/api/knowledge-bases?")) && !init?.method) {
      return Promise.resolve(json([defaultBase, withDocuments, deletable]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/knowledge-bases");
  render(<App />);

  // 每行操作直接平铺，删除仍走原确认弹层。
  const rowOf = async (name: string) =>
    (await screen.findByRole("button", { name })).closest("tr") as HTMLElement;

  // 所有删除项都直接可见且可点，行内没有占位小字。
  for (const name of ["默认知识库", "有资料的库", "空库"]) {
    expect(within(await rowOf(name)).getByRole("button", { name: "删除" })).not.toBeDisabled();
  }
  expect(screen.queryByText(/请先删除/)).toBeNull();

  // 有资料：说清连带后果，并给出下一步
  await userEvent.click(within(await rowOf("有资料的库")).getByRole("button", { name: "删除" }));
  let dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/7 份资料/)).toBeVisible();
  expect(within(dialog).getByText(/连带删除/)).toBeVisible();
  expect(within(dialog).getByRole("button", { name: "去清空资料" })).toBeEnabled();
  await userEvent.click(within(dialog).getByRole("button", { name: "知道了" }));

  // 默认知识库：说明它为什么特殊，且不给「去清空」
  await userEvent.click(within(await rowOf("默认知识库")).getByRole("button", { name: "删除" }));
  dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/兜底归属/)).toBeVisible();
  expect(within(dialog).queryByRole("button", { name: "去清空资料" })).toBeNull();
  await userEvent.click(within(dialog).getByRole("button", { name: "知道了" }));

  // 空库：正常确认
  await userEvent.click(within(await rowOf("空库")).getByRole("button", { name: "删除" }));
  dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByRole("button", { name: "确认删除" })).toBeEnabled();
});

test("新建分类复用编辑弹层：三个字段一次填完，排序默认排在末尾", async () => {
  // 此前新建只有一个行内输入框、只能填名称，描述与排序写死（""/100），想补充就得
  // 建完再点「编辑」填一遍——同一件事分两次做。而且所有新分类的排序都是 100，
  // 会和模板分类挤在一起。
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/knowledge-bases/kb_default/categories" && init?.method === "POST") {
      return Promise.resolve(json({ ...category, category_id: "cat_bbbbbbbbbbbbbbbb" }, 201));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);
  await userEvent.click(await screen.findByRole("tab", { name: /分类管理/ }));

  await userEvent.click(screen.getByRole("button", { name: /新建分类/ }));

  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("新建分类")).toBeVisible();
  // 与编辑弹层同样三个字段，而不是只有名称。
  expect(within(dialog).getByLabelText("名称")).toBeVisible();
  expect(within(dialog).getByLabelText("描述")).toBeVisible();
  // 现有分类排序 100，新建默认排在它后面而不是挤在同一档。
  expect(within(dialog).getByLabelText("排序")).toHaveValue(200);

  await userEvent.type(within(dialog).getByLabelText("名称"), "安全合规");
  await userEvent.type(within(dialog).getByLabelText("描述"), "合规与审计要求");
  await userEvent.click(within(dialog).getByRole("button", { name: "创建" }));

  await waitFor(() => {
    const post = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/knowledge-bases/kb_default/categories" && init?.method === "POST",
    );
    expect(post).toBeTruthy();
    expect(JSON.parse(String(post![1]!.body))).toEqual({
      name: "安全合规",
      description: "合规与审计要求",
      sort_order: 200,
    });
  });
});

test("分类管理复用知识库管理的数据表格结构", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);
  await userEvent.click(await screen.findByRole("tab", { name: /分类管理/ }));

  const table = screen.getByRole("table", { name: "分类管理列表" });
  expect(within(table).getByRole("columnheader", { name: "分类名称" })).toBeVisible();
  expect(within(table).getByRole("columnheader", { name: "描述" })).toBeVisible();
  expect(within(table).getByRole("columnheader", { name: "资料数量" })).toBeVisible();
  expect(within(table).getByRole("columnheader", { name: "排序" })).toBeVisible();
  expect(within(table).getByRole("columnheader", { name: "初始来源" })).toBeVisible();
  expect(within(table).getByRole("columnheader", { name: "状态" })).toBeVisible();
  expect(within(table).getByRole("columnheader", { name: "操作" })).toBeVisible();
  expect(within(table).getByText("安全资料")).toBeVisible();
  expect(within(table).getByText("1")).toBeVisible();
  expect(within(table).getByText("100")).toBeVisible();
  expect(within(table).getByText("历史迁移")).toBeVisible();
  expect(within(table).getByText("启用")).toBeVisible();
  expect(screen.getByText("以下是本知识库独立维护的分类，不会同步到默认模板。")).toBeVisible();
});

test("分类管理将模板复制分类移出表格并保持只读", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (String(input) === "/api/knowledge-bases/kb_default/categories") {
      return Promise.resolve(json([
        { ...category, category_id: "cat_template", name: "产品资料", origin_type: "template_copy" },
        { ...category, category_id: "cat_manual", name: "项目约定", origin_type: "manual" },
        category,
      ]));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);
  await userEvent.click(await screen.findByRole("tab", { name: /分类管理/ }));

  const templateRegion = screen.getByRole("region", { name: "默认模板分类" });
  const table = screen.getByRole("table", { name: "分类管理列表" });
  expect(within(templateRegion).getByText("产品资料")).toBeVisible();
  expect(within(templateRegion).queryByRole("button", { name: /编辑|停用|删除/ })).toBeNull();
  expect(within(table).queryByText("产品资料")).toBeNull();
  expect(within(table).getByText("项目约定")).toBeVisible();
  expect(within(table).getByText("安全")).toBeVisible();
  expect(within(table).getByText("手动创建")).toBeVisible();
  expect(within(table).getByText("历史迁移")).toBeVisible();
});

test("新建分类的空名称：按钮可点击并报错，与模板弹框一致", async () => {
  // CLAUDE.md 第一条：能用「点击后报错」代替禁用时优先报错。分类模板弹框就是这么做的，
  // 这里必须一样——用户在一处学会的操作方式会带到另一处。
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);
  await userEvent.click(await screen.findByRole("tab", { name: /分类管理/ }));
  await userEvent.click(screen.getByRole("button", { name: /新建分类/ }));

  const dialog = await screen.findByRole("dialog");
  const submit = within(dialog).getByRole("button", { name: "创建" });
  expect(submit).toBeEnabled();

  await userEvent.click(submit);
  expect(await screen.findByRole("alert")).toHaveTextContent("请输入分类名称");

  await userEvent.type(within(dialog).getByLabelText("名称"), "运维文档");
  expect(screen.queryByRole("alert")).toBeNull();
});

test("删除分类走确认弹层，并说明资料不会被删", async () => {
  // 此前是点一下直接删（无确认），而同项目的资料删除、知识库删除都有确认弹层。
  // 顺带把「请先迁移资料」那行占位小字去掉：列表要保持紧凑，后果说明放进弹层，
  // 那里能说清「删分类不删资料」——一行小字装不下这句话。
  const used = { ...category, category_id: "cat_aaaaaaaaaaaaaaaa", name: "技术文档", document_count: 3 };
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/knowledge-bases/kb_default/categories" && !init?.method) {
      return Promise.resolve(json([used]));
    }
    if (url.startsWith("/api/knowledge-bases/kb_default/categories/cat_") && init?.method === "DELETE") {
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    return commonFetch(input, init);
  });
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);
  await userEvent.click(await screen.findByRole("tab", { name: /分类管理/ }));

  // 有资料也能点，不再禁用，也不再有行内占位小字。
  const remove = await screen.findByRole("button", { name: /删除/ });
  expect(remove).toBeEnabled();
  expect(screen.queryByText("请先迁移资料")).toBeNull();

  await userEvent.click(remove);

  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/3 份资料/)).toBeVisible();
  expect(within(dialog).getByText(/不会删除资料/)).toBeVisible();

  await userEvent.click(within(dialog).getByRole("button", { name: "仍要删除" }));

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/knowledge-bases/kb_default/categories/cat_aaaaaaaaaaaaaaaa",
      expect.objectContaining({ method: "DELETE" }),
    ),
  );
});
