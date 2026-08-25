import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
};
const document = {
  knowledge_base_id: "kb_default",
  document_id: "doc_1",
  filename: "profile.md",
  chunk_count: 3,
  status: "ready",
};
const dataSource = {
  data_source_id: "src_1", name: "profile.md", source_type: "file",
  knowledge_base_id: "kb_default", knowledge_base_name: "默认知识库", enabled: true,
  upload_status: "succeeded", index_status: "succeeded",
  sync_status: "succeeded", document_count: 1, source_file_bytes: 2048,
  last_indexed_at: "2026-08-12T00:00:00Z", last_synced_at: "2026-08-12T00:00:00Z", failure_reason: null,
  updated_at: "2026-08-12T00:00:00Z", allowed_actions: ["detail", "edit", "disable", "sync"],
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
  if (url === "/api/audit/events?offset=0&limit=100")
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
  if (url === "/api/knowledge-bases/kb_default/documents/doc_1" && init?.method === "DELETE") return Promise.resolve(new Response(null, { status: 204 }));
  if ((url === "/api/knowledge-bases" || url.startsWith("/api/knowledge-bases?")) && !init?.method) return Promise.resolve(json([base]));
  if (url === "/api/data-sources?offset=0&limit=21") return Promise.resolve(json([dataSource]));
  if (url === "/api/knowledge-bases/kb_default") return Promise.resolve(json(base));
  if (url === "/api/knowledge-bases/kb_default/documents" && init?.method === "POST")
    return Promise.resolve(json({ ...document, status: "pending" }, 201));
  if (url === "/api/knowledge-bases/kb_default/documents") return Promise.resolve(json([document]));
  if (url === "/api/knowledge-bases/kb_default/document-versions?offset=0&limit=100") return Promise.resolve(json([]));
  if (url === "/api/knowledge-bases/kb_default/conversations") return Promise.resolve(json([]));
  if (url === "/api/evaluations/answers/reports") return Promise.resolve(json([answerSummary]));
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
  await userEvent.upload(await screen.findByLabelText("更新 profile.md"), new File(["updated"], "profile.md", { type: "text/markdown" }));
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
  expect(await screen.findByText("等待索引")).toHaveClass("index-loading");
  expect(screen.getByText("等待索引")).toHaveAttribute("aria-busy", "true");
  expect(await screen.findByText("索引完成", {}, { timeout: 2_000 })).toBeInTheDocument();
  expect(sourceRequests).toBe(2);
});

test("知识库列表可进入绑定 knowledge_base_id 的详情", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases");
  render(<App />);
  expect(await screen.findByRole("region", { name: "知识库管理" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "＋ 新建知识库" }).closest(".topbar")).not.toBeNull();
  expect(globalThis.document.querySelector(".page-heading.bases-toolbar")).toBeNull();
  expect(screen.queryByText("为不同项目建立隔离的资料、索引与会话空间。")).not.toBeInTheDocument();
  expect(globalThis.document.querySelector(".base-icon")).toBeNull();
  expect(await screen.findByRole("columnheader", { name: "知识库名称" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "存储空间" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "状态" })).toBeInTheDocument();
  expect(screen.getByText("默认知识库", { selector: ".base-type-tag" })).toHaveClass("is-default");
  await userEvent.click(await screen.findByRole("button", { name: "详情" }));
  expect(await screen.findByText("profile.md")).toBeInTheDocument();
  expect(window.location.pathname).toBe("/knowledge-bases/kb_default");
});

test("通过弹框创建知识库并支持取消", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases");
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "＋ 新建知识库" }));
  expect(screen.getByRole("dialog", { name: "新建知识库" })).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("知识库名称"), "产品资料");
  await userEvent.click(screen.getByRole("button", { name: "确认创建" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/knowledge-bases", expect.objectContaining({ method: "POST" })));
});

test("删除资料使用站内确认弹框", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases/kb_default");
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "删除 profile.md" }));
  expect(screen.getByRole("button", { name: "在此知识库提问 →" }).closest(".detail-toolbar")).not.toBeNull();
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
  expect(basePicker.closest(".question-box")).not.toBeNull();
  expect(screen.getByRole("tab", { name: /引用来源/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByRole("tab", { name: /资料库/ })).not.toBeInTheDocument();
  await userEvent.type(await screen.findByLabelText("向知识库提问"), "系统如何工作？");
  await userEvent.click(screen.getByRole("button", { name: /提问/ }));
  expect(await screen.findByText("系统使用可追溯检索。")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/knowledge-bases/kb_default/query", expect.objectContaining({ method: "POST" }));
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
  window.history.replaceState({}, "", "/evaluation/answers");
  render(<App />);
  expect(await screen.findByText("回答质量门已通过")).toBeInTheDocument();
  expect(screen.getByText("回答质量门已通过").closest(".topbar")).not.toBeNull();
  expect(screen.queryByText("只读质量证据")).not.toBeInTheDocument();
  expect(screen.queryByText("正确性、引用准确性、幻觉风险与失败策略的正式基线。")).not.toBeInTheDocument();
  expect(screen.getByText("回答正确性")).toBeInTheDocument();
  expect(screen.getByText("无支持声明率")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "回答质量" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "证据质量" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "幻觉风险" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "失败控制" })).toBeInTheDocument();
  expect(screen.getByText(/页面不会启动模型评测/)).toBeInTheDocument();
});

test("保留检索评测页且可直接访问", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => (String(input) === "/api/auth/me" ? Promise.resolve(json(admin)) : String(input) === "/api/evaluations" ? Promise.resolve(json([])) : Promise.resolve(json({}, 404))));
  window.history.replaceState({}, "", "/evaluation/retrieval");
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
  expect(screen.getByText("服务已就绪").closest(".topbar")).not.toBeNull();
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
  expect(screen.getByRole("button", { name: "新建成员" }).closest(".topbar")).not.toBeNull();
  await userEvent.click(screen.getAllByRole("button", { name: "停用" }).find((button) => !button.hasAttribute("disabled"))!);
  expect(screen.getByRole("dialog", { name: "停用成员" })).toBeInTheDocument();
});

test("审计页展示哈希链事件且不展示业务正文", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/settings/audit");
  render(<App />);
  expect(await screen.findByRole("heading", { name: "审计记录" })).toBeInTheDocument();
  expect(await screen.findByText("更新成员")).toBeInTheDocument();
  expect(screen.getByText("哈希链由服务端校验").closest(".topbar")).not.toBeNull();
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
