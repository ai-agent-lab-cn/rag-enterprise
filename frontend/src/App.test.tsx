import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { setAccessToken } from "./api";
import App from "./App";

const base = { knowledge_base_id: "kb_default", name: "默认知识库", description: "V2 迁移资料", created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-12T00:00:00Z", is_default: true, document_count: 1, chunk_count: 3 };
const document = { knowledge_base_id: "kb_default", document_id: "doc_1", filename: "profile.md", chunk_count: 3, status: "ready" };
const answerSummary = { report_id: "answer-official", dataset_id: "answers", dataset_version: "1.0.0", commit: "daca18509ca8f447aa00395ca88a58543ffb2cd4", run_at: "2026-08-12T08:52:33Z", models: { generation: "gemini-test", judge: "judge-test" }, prompt_version: "v3-grounded-answer-1", passed: true };
const admin = { user_id: "usr_1234567890abcdef", username: "test-admin", display_name: "测试管理员", role: "admin", active: true, created_at: "2026-08-16T00:00:00Z", updated_at: "2026-08-16T00:00:00Z" };

beforeEach(() => { window.scrollTo = vi.fn(); setAccessToken("test-token"); });
afterEach(() => { cleanup(); setAccessToken(null); vi.restoreAllMocks(); vi.unstubAllEnvs(); window.history.replaceState({}, "", "/"); });

function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function commonFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
  if (url === "/api/auth/me") return Promise.resolve(json(admin));
  if (url === "/api/auth/logout") return Promise.resolve(new Response(null, { status: 204 }));
  if (url === "/api/knowledge-bases" && init?.method === "POST") return Promise.resolve(json({ ...base, knowledge_base_id: "kb_created", name: "产品资料", is_default: false, document_count: 0, chunk_count: 0 }));
  if (url === "/api/knowledge-bases/kb_default/documents/doc_1" && init?.method === "DELETE") return Promise.resolve(new Response(null, { status: 204 }));
  if (url === "/api/knowledge-bases") return Promise.resolve(json([base]));
  if (url === "/api/knowledge-bases/kb_default") return Promise.resolve(json(base));
  if (url === "/api/knowledge-bases/kb_default/documents") return Promise.resolve(json([document]));
  if (url === "/api/knowledge-bases/kb_default/conversations") return Promise.resolve(json([]));
  if (url === "/api/evaluations/answers/reports") return Promise.resolve(json([answerSummary]));
  if (url === "/api/knowledge-bases/kb_default/query" && init?.method === "POST") return Promise.resolve(json({ answer: "系统使用可追溯检索。[来源 1]", answer_status: "answered", error_code: null, error_message: null, model: "gemini-test", latency_ms: { retrieval: 10, rerank: 5, generation: 20, total: 35 }, conversation_id: "conv_1234567890abcdef", record_id: "ans_1", models: {}, model_metadata: {}, prompt_version: "v3", prompt_hash: "abc", sources: [{ knowledge_base_id: "kb_default", chunk_id: "chunk_1", document_id: "doc_1", filename: "profile.md", page: null, paragraph: 0, chunk_index: 0, char_count: 12, summary: "系统资料", text: "系统资料全文", retrieval_score: .82, rerank_score: 1.31 }] }));
  return Promise.resolve(json({ error: { message: "未找到" } }, 404));
}

test("默认进入概览并汇总知识库、资料、会话和回答质量", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  render(<App/>);
  expect(await screen.findByRole("heading", { name: "项目概览" })).toBeInTheDocument();
  expect(await screen.findByText("默认知识库")).toBeInTheDocument();
  expect(await screen.findByText("全部指标通过")).toBeInTheDocument();
});

test("Demo 构建明确提示数据可能重置", async () => {
  vi.stubEnv("VITE_DEPLOYMENT_MODE", "demo");
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);

  render(<App/>);

  expect(await screen.findByText("演示环境 · 数据可能重置")).toBeInTheDocument();
  expect(screen.getByText("Demo · 数据会重置")).toBeInTheDocument();
});

test("知识库列表可进入绑定 knowledge_base_id 的详情", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases"); render(<App/>);
  await userEvent.click(await screen.findByRole("button", { name: /进入知识库/ }));
  expect(await screen.findByText("profile.md")).toBeInTheDocument();
  expect(window.location.pathname).toBe("/knowledge-bases/kb_default");
});

test("通过弹框创建知识库并支持取消", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases"); render(<App/>);
  await userEvent.click(await screen.findByRole("button", { name: "＋ 新建知识库" }));
  expect(screen.getByRole("dialog", { name: "新建知识库" })).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("知识库名称"), "产品资料");
  await userEvent.click(screen.getByRole("button", { name: "确认创建" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/knowledge-bases", expect.objectContaining({ method: "POST" })));
});

test("删除资料使用站内确认弹框", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases/kb_default"); render(<App/>);
  await userEvent.click(await screen.findByRole("button", { name: "删除 profile.md" }));
  expect(screen.getByRole("dialog", { name: "删除资料" })).toHaveTextContent("profile.md");
  await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/knowledge-bases/kb_default/documents/doc_1", expect.objectContaining({ method: "DELETE" })));
});

test("问答工作台使用所选知识库接口并渲染来源", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/chat?knowledge_base_id=kb_default"); render(<App/>);
  await userEvent.type(await screen.findByLabelText("向知识库提问"), "系统如何工作？");
  await userEvent.click(screen.getByRole("button", { name: /提问/ }));
  expect(await screen.findByText("系统使用可追溯检索。")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/knowledge-bases/kb_default/query", expect.objectContaining({ method: "POST" }));
});

test("回答评测页只读展示正式指标", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    if (String(input) === "/api/auth/me") return Promise.resolve(json(admin));
    if (String(input) === "/api/evaluations/answers/reports") return Promise.resolve(json([answerSummary]));
    if (String(input) === "/api/evaluations/answers/reports/answer-official") return Promise.resolve(json({ ...answerSummary, prompt_hash: "a".repeat(64), parameters: { temperature: 0 }, case_count: 30, metrics: { answer_correctness: { value: 1, threshold: .8, baseline: null, passed: true, regressed: false, direction: "minimum" }, unsupported_claim_rate: { value: 0, threshold: .05, baseline: null, passed: true, regressed: false, direction: "maximum" } } }));
    return Promise.resolve(json({}, 404));
  });
  window.history.replaceState({}, "", "/evaluation/answers"); render(<App/>);
  expect(await screen.findByText("回答质量门已通过")).toBeInTheDocument();
  expect(screen.getByText("回答正确性")).toBeInTheDocument();
  expect(screen.getByText("无支持声明率")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "回答质量" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "证据质量" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "幻觉风险" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "失败控制" })).toBeInTheDocument();
  expect(screen.getByText(/页面不会启动模型评测/)).toBeInTheDocument();
});

test("保留检索评测页且可直接访问", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => String(input) === "/api/auth/me" ? Promise.resolve(json(admin)) : String(input) === "/api/evaluations" ? Promise.resolve(json([])) : Promise.resolve(json({}, 404)));
  window.history.replaceState({}, "", "/evaluation/retrieval"); render(<App/>);
  expect(await screen.findByText("还没有正式评测报告")).toBeInTheDocument();
});

test("页面显示稳定 API 错误", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => String(input) === "/api/auth/me" ? Promise.resolve(json(admin)) : Promise.resolve(json({ error: { message: "后端不可用" } }, 503)));
  render(<App/>);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("后端不可用"));
});

test("首次启动可创建管理员并进入工作台", async () => {
  setAccessToken(null);
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/auth/bootstrap" && init?.method === "POST") return Promise.resolve(json({ access_token: "new-token", token_type: "bearer", expires_at: "2026-08-17T00:00:00Z", user: admin }, 201));
    if (url === "/api/auth/bootstrap") return Promise.resolve(json({ required: true }));
    return commonFetch(input, init);
  });
  render(<App/>);

  expect(await screen.findByRole("heading", { name: "创建首位管理员" })).toBeInTheDocument();
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
  render(<App/>);

  expect(await screen.findByRole("heading", { name: "登录 RAG 工作台" })).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("用户名"), "test-admin");
  await userEvent.type(screen.getByLabelText("密码"), "incorrect-password");
  await userEvent.click(screen.getByRole("button", { name: "登录" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("用户名或密码错误");
  expect(screen.queryByRole("heading", { name: "项目概览" })).not.toBeInTheDocument();
});

test("退出后清除当前会话并返回登录入口", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  render(<App/>);

  await screen.findByRole("heading", { name: "项目概览" });
  await userEvent.click(screen.getByRole("button", { name: "退出登录" }));

  expect(await screen.findByRole("heading", { name: "登录 RAG 工作台" })).toBeInTheDocument();
});
