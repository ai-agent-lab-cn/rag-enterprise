import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { setAccessToken } from "./api";
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
  if (url === "/api/knowledge-bases/kb_default") return Promise.resolve(json(base));
  if (url === "/api/knowledge-bases/kb_default/documents" && init?.method === "POST")
    return Promise.resolve(json({ ...document, status: "pending" }, 201));
  if (url === "/api/knowledge-bases/kb_default/documents") return Promise.resolve(json([document]));
  if (url === "/api/knowledge-bases/kb_default/categories") return Promise.resolve(json([category]));
  if (url === "/api/knowledge-bases/kb_default/document-versions?offset=0&limit=100") return Promise.resolve(json([documentVersion]));
  if (url === "/api/knowledge-bases/kb_default/index-versions") return Promise.resolve(json([{ index_version_id: "iv_active", status: "active", chunking_version: "semantic-v1", parser_version: "registry-v1", embedding_model: "text2vec", embedding_dimension: 768, processing_options: {}, config_fingerprint: "a".repeat(64), evaluation_report_id: "retrieval-official", rebuild_batch_id: null, created_at: "2026-08-30T00:00:00Z", activated_at: "2026-08-30T00:01:00Z", retired_at: null }]));
  if (url === "/api/knowledge-bases/kb_default/document-versions/ver_1/parsing") return Promise.resolve(json({ ...documentVersion, tree: [{ node_id: "node_00000", node_type: "heading", text: "安全规范", level: 1, location: { heading_path: ["安全规范"], paragraph_index: 0 }, children: [] }], chunks: [{ chunk_id: "chunk_1", chunk_index: 0, content: "ACL 必须在召回前过滤。", metadata: { node_id: "node_00000", heading_path: ["安全规范"], paragraph: 0 } }] }));
  if (url === "/api/knowledge-bases/kb_default/citations/chunk_1") return Promise.resolve(json({ chunk_id: "chunk_1", knowledge_base_id: "kb_default", document_id: "doc_1", document_version_id: "ver_1", content_sha256: "a".repeat(64), filename: "profile.md", text: "系统资料全文", page: null, paragraph: 0, heading_path: ["系统设计"], sheet_name: null, row_start: null, row_end: null, source_url: null, external_resource_id: null }));
  if (url === "/api/knowledge-bases/kb_default/conversations") return Promise.resolve(json([]));
  if (url === "/api/evaluations/answers/reports") return Promise.resolve(json([answerSummary]));
  if (url === "/api/evaluation-center/overview") return Promise.resolve(json({
    passed: true, report_count: 2,
    retrieval_report: { report_id: "retrieval-official", dataset_id: "retrieval", dataset_version: "2.0.0", commit: "a".repeat(40), run_at: "2026-08-30T00:00:00Z", models: {}, passed: true },
    answer_report: answerSummary,
  }));
  if (url.startsWith("/api/evaluation-center/pipeline")) return Promise.resolve(json({ run_count: 2, added_count: 4, updated_count: 1, deleted_count: 1, skipped_count: 2, failed_count: 1, retry_count: 3, failure_rate: 0.5, average_duration_ms: 20000 }));
  if (url.startsWith("/api/evaluation-center/bad-cases")) return Promise.resolve(json([{ case_id: "case_1234567890abcdef", source_type: "online", source_record_id: "ans_1", knowledge_base_id: "kb_default", dataset_version: null, question: "为什么没有召回？", expected_source_ids: [], actual_source_ids: [], expected_answer_status: "answered", actual_answer_status: "insufficient_evidence", actual_answer: "资料不足。", failure_stage: "retrieval", root_cause: null, category: "没召回", severity: "high", assignee: null, fix_commit: null, status: "new", regression_added: false, created_at: "2026-08-30T00:00:00Z", confirmed_at: null, resolved_at: null, updated_at: "2026-08-30T00:00:00Z" }]));
  if (url.startsWith("/api/evaluation-center/acceptance-runs")) return Promise.resolve(json([{ acceptance_run_id: "acc_1", knowledge_base_id: "kb_default", status: "blocked", commit_sha: "local-working-tree", schema_version: 14, steps: [{ step_key: "external_source", title: "真实数据源", status: "blocked", summary: "缺少 S3 兼容外部数据源。", evidence: { external_source_count: 0 } }], limitations: ["缺少 S3 兼容外部数据源。"], created_by: admin.user_id, created_at: "2026-08-30T00:00:00Z" }]));
  if (url === "/api/knowledge-bases/kb_default/query" && init?.method === "POST")
    return Promise.resolve(
      json({
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
      }),
    );
  return Promise.resolve(json({ error: { message: "未找到" } }, 404));
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
  expect(screen.getByRole("columnheader", { name: "上传状态" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "索引状态" })).toBeInTheDocument();
  expect(screen.getByText("上传成功")).toBeInTheDocument();
  expect(screen.getByText("索引完成")).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "类型" })).not.toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "文档数" })).not.toBeInTheDocument();
  expect(screen.queryByText("会话历史")).not.toBeInTheDocument();
});

test("文件数据源使用更新文件创建新版本", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/data-sources");
  render(<App />);
  expect(screen.queryByRole("button", { name: "同步" })).not.toBeInTheDocument();
  // RowActions 统一了可访问名格式为「{rowLabel} 的{action.label}」（见 ui/RowActions.tsx），
  // 不再是页面自己拼的「更新 {name}」。
  await userEvent.upload(await screen.findByLabelText("profile.md 的更新文件"), new File(["updated"], "profile.md", { type: "text/markdown" }));
  expect(await screen.findByRole("status")).toHaveTextContent("新版本已上传并加入索引队列");
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
  expect(await screen.findByText("profile.md")).toBeInTheDocument();
  expect(screen.queryByText("正在读取知识库详情…")).not.toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /资料/ })).toHaveAttribute("aria-selected", "true");
  // profile.md 分类状态是 manual，只有「编辑/删除」两个操作，走平铺而非菜单。
  await userEvent.click(screen.getByRole("button", { name: "编辑" }));
  expect(screen.getByRole("dialog", { name: "编辑资料元数据" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "取消" }));
  await userEvent.click(screen.getByRole("tab", { name: /版本治理/ }));
  expect(screen.getByRole("tab", { name: /版本治理/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("heading", { name: "索引版本" })).toBeInTheDocument();
  expect(screen.getByText("iv_active")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "文档与版本" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: /解析与切片/ }));
  expect(await screen.findByRole("heading", { name: "文档结构" })).toBeInTheDocument();
  expect(screen.getAllByText("ACL 必须在召回前过滤。").length).toBeGreaterThan(0);
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
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);

  await userEvent.click(await screen.findByRole("tab", { name: /数据源/ }));

  expect(screen.getByRole("button", { name: "新建外部数据源" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "同步状态" })).toBeInTheDocument();
  expect(screen.getByText("profile.md")).toBeInTheDocument();
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
  expect(within(row).getByText("—")).toBeTruthy();
  expect(within(row).getByText("待分类")).toBeTruthy();
  expect(within(row).queryByText("未分类")).toBeNull();
});

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
  expect(within(row).getByText(/模型 30 秒未响应/)).toBeTruthy();

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

  expect(screen.queryByText("profile.md")).toBeNull();
  expect(screen.getByText("draft.md")).toBeTruthy();
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
  expect(fetchMock).toHaveBeenCalledWith("/api/knowledge-bases/kb_default/query", expect.objectContaining({ method: "POST" }));
  const queryCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/knowledge-bases/kb_default/query");
  expect(JSON.parse(String(queryCall?.[1]?.body))).toMatchObject({ filters: { category_ids: ["cat_1234567890abcdef"], tags: ["ACL"], source_types: ["file"] } });
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
    if (String(input) === "/api/knowledge-bases/kb_default/query") {
      return commonFetch(input, init).then(async (response) => json({
        ...await response.json(),
        answer: "当前资料不足以支持确定回答。",
        answer_status: "insufficient_evidence",
        sources: [],
      }));
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
  window.history.replaceState({}, "", "/evaluation");
  render(<App />);
  // 顶栏只留评测中心的统一质量门结论：三个 Section 各塞一个徽章会并排堆在一起，
  // 既重复又说不清哪个是哪个。分项结论回到各自小节内部。
  expect(await screen.findByText("回答质量门已通过")).toBeInTheDocument();
  expect(screen.getByText("回答质量门已通过").closest("header")).toBeNull();
  expect(screen.getByText("回答质量门已通过").closest('[aria-label="回答评测"]')).not.toBeNull();
  expect(screen.queryByText("只读质量证据")).not.toBeInTheDocument();
  expect(screen.queryByText("正确性、引用准确性、幻觉风险与失败策略的正式基线。")).not.toBeInTheDocument();
  expect(screen.getByText("回答正确性")).toBeInTheDocument();
  expect(screen.getByText("无支持声明率")).toBeInTheDocument();
  // Section 标题（h2）与组件内部的指标分组（h3）同名，按层级精确定位后者。
  expect(screen.getByRole("heading", { name: "回答质量", level: 3 })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "证据质量" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "幻觉风险" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "失败控制" })).toBeInTheDocument();
  expect(screen.getByText(/页面不会启动模型评测/)).toBeInTheDocument();
});

test("评测中心用纵向 Section 呈现四类质量，不再使用 Tabs", async () => {
  // 指标不是工作场景，不该各占一个 Tab 或一个左侧菜单。四类质量在同一页纵向排开，
  // 一屏就能回答「当前系统质量怎么样」，不必逐个 Tab 点过去拼图。
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/evaluation");
  render(<App />);

  expect(await screen.findByRole("heading", { name: "质量总览" })).toBeInTheDocument();
  for (const name of ["检索质量", "回答质量", "工程指标", "最近评测"]) {
    expect(screen.getByRole("heading", { name })).toBeInTheDocument();
  }
  expect(screen.queryByRole("tab")).toBeNull();

  // 工程指标不再需要点 Tab 才加载。
  expect(await screen.findByText("2 个同步批次")).toBeInTheDocument();

  // 锚点导航滚动到对应 Section，而不是切换内容。
  const anchors = screen.getByRole("navigation", { name: "评测中心小节" });
  expect(within(anchors).getByRole("link", { name: "检索质量" })).toHaveAttribute("href", "#retrieval");
});

test("Bad Case 是独立菜单与独立路由", async () => {
  // Bad Case 不是看指标，而是一条完整的治理工作流：发现 → 分类 → 定位根因 → 修复
  // → 回归 → 关闭。它有自己的工作场景，所以配得上一个左侧菜单。
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/evaluation/bad-cases");
  render(<App />);

  expect(await screen.findByText("为什么没有召回？")).toBeInTheDocument();
  expect(screen.getByLabelText("Bad Case 状态筛选")).toBeInTheDocument();
  expect(screen.getByLabelText("Bad Case 严重级别筛选")).toBeInTheDocument();
  expect(screen.getByText("治理详情")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Bad Case" })).toHaveAttribute("aria-current", "page");
});

test("链路验收是独立菜单与独立路由", async () => {
  // 链路验收承担版本放行职责，结论是 PASS / BLOCKED，与「看指标」不是一件事。
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/evaluation/acceptance");
  render(<App />);

  expect(await screen.findByText("缺少 S3 兼容外部数据源。")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "链路验收" })).toHaveAttribute("aria-current", "page");
});

test("概览页的评测入口指向评测中心的页面内锚点", async () => {
  // /evaluation/retrieval 与 /evaluation/answers 作为独立路由已删除，改为 Section 锚点。
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/overview");
  render(<App />);

  await userEvent.click(await screen.findByRole("button", { name: /查看回答评测详情/ }));

  expect(await screen.findByRole("heading", { name: "质量总览" })).toBeInTheDocument();
  expect(window.location.pathname).toBe("/evaluation");
  expect(window.location.hash).toBe("#answer");
});

test("保留检索评测页且可直接访问", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => (String(input) === "/api/auth/me" ? Promise.resolve(json(admin)) : String(input) === "/api/evaluations" ? Promise.resolve(json([])) : Promise.resolve(json({}, 404))));
  window.history.replaceState({}, "", "/evaluation");
  render(<App />);
  expect(await screen.findByText("还没有正式评测报告")).toBeInTheDocument();
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
