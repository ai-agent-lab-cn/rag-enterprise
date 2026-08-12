import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

const base = { knowledge_base_id: "kb_default", name: "默认知识库", description: "V2 迁移资料", created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-12T00:00:00Z", is_default: true, document_count: 1, chunk_count: 3 };
const document = { knowledge_base_id: "kb_default", document_id: "doc_1", filename: "profile.md", chunk_count: 3, status: "ready" };
const answerSummary = { report_id: "answer-official", dataset_id: "answers", dataset_version: "1.0.0", commit: "daca18509ca8f447aa00395ca88a58543ffb2cd4", run_at: "2026-08-12T08:52:33Z", models: { generation: "gemini-test", judge: "judge-test" }, prompt_version: "v3-grounded-answer-1", passed: true };

beforeEach(() => { window.scrollTo = vi.fn(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function commonFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
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
  expect(await screen.findByText("工作空间概览")).toBeInTheDocument();
  expect(screen.getByText("默认知识库")).toBeInTheDocument();
  expect(screen.getByText("全部指标通过")).toBeInTheDocument();
});

test("知识库列表可进入绑定 knowledge_base_id 的详情", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(commonFetch);
  window.history.replaceState({}, "", "/knowledge-bases"); render(<App/>);
  await userEvent.click(await screen.findByRole("button", { name: /进入知识库/ }));
  expect(await screen.findByText("profile.md")).toBeInTheDocument();
  expect(window.location.pathname).toBe("/knowledge-bases/kb_default");
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
    if (String(input) === "/api/evaluations/answers/reports") return Promise.resolve(json([answerSummary]));
    if (String(input) === "/api/evaluations/answers/reports/answer-official") return Promise.resolve(json({ ...answerSummary, prompt_hash: "a".repeat(64), parameters: { temperature: 0 }, case_count: 30, metrics: { answer_correctness: { value: 1, threshold: .8, baseline: null, passed: true, regressed: false, direction: "minimum" }, unsupported_claim_rate: { value: 0, threshold: .05, baseline: null, passed: true, regressed: false, direction: "maximum" } } }));
    return Promise.resolve(json({}, 404));
  });
  window.history.replaceState({}, "", "/evaluation/answers"); render(<App/>);
  expect(await screen.findByText("回答质量门已通过")).toBeInTheDocument();
  expect(screen.getByText("回答正确性")).toBeInTheDocument();
  expect(screen.getByText("无支持声明率")).toBeInTheDocument();
  expect(screen.getByText(/页面不会启动模型评测/)).toBeInTheDocument();
});

test("保留检索评测页且可直接访问", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => String(input) === "/api/evaluations" ? Promise.resolve(json([])) : Promise.resolve(json({}, 404)));
  window.history.replaceState({}, "", "/evaluation/retrieval"); render(<App/>);
  expect(await screen.findByText("还没有正式评测报告")).toBeInTheDocument();
});

test("页面显示稳定 API 错误", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(json({ error: { message: "后端不可用" } }, 503)));
  render(<App/>);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("后端不可用"));
});
