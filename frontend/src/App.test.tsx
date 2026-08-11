import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

const documents = [{ document_id: "doc_1", filename: "profile.md", chunk_count: 3, status: "ready" }];

beforeEach(() => {
  window.scrollTo = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

test("loads documents and renders query sources", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/documents") return new Response(JSON.stringify(documents), { status: 200 });
    if (url === "/api/query" && init?.method === "POST") {
      return new Response(JSON.stringify({
        answer: "系统使用可解释的检索流程。[来源 1]",
        answer_status: "answered",
        error_code: null,
        error_message: null,
        model: "gemini-test",
        latency_ms: { retrieval: 10, rerank: 5, generation: 20, total: 35 },
        sources: [{
          chunk_id: "chunk_1", document_id: "doc_1", filename: "profile.md", page: null,
          paragraph: 0, chunk_index: 0, char_count: 12, summary: "系统资料", text: "系统资料全文",
          retrieval_score: 0.82, rerank_score: 1.31,
        }],
      }), { status: 200 });
    }
    return new Response(null, { status: 404 });
  });

  render(<App />);
  expect(await screen.findByText("profile.md")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("向知识库提问"), "系统如何工作？");
  await userEvent.click(screen.getByRole("button", { name: /提问/ }));
  expect(await screen.findByText("系统使用可解释的检索流程。")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "[来源 1]" })).toHaveAttribute("href", "#source-1");
  expect(screen.getByText("召回 0.820")).toBeInTheDocument();
  expect(screen.getByText("召回")).toBeInTheDocument();
  expect(screen.getByText("精排")).toBeInTheDocument();
  expect(screen.getByText("生成")).toBeInTheDocument();
  expect(screen.getAllByText("总耗时")).toHaveLength(2);
  expect(screen.getByText("模型")).toBeInTheDocument();
  expect(screen.getByText("第 1 段 · 片段 0")).toBeInTheDocument();
  expect(screen.getByText("查看技术细节")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/query", expect.objectContaining({ method: "POST" }));
});

test("renders the localized interface without legacy English labels", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200 }),
  );
  render(<App />);
  expect(await screen.findByText("资料库")).toBeInTheDocument();
  expect(screen.getByText("单知识库问答工作台")).toBeInTheDocument();
  expect(screen.getByRole("navigation", { name: "主导航" })).toBeInTheDocument();
  expect(screen.queryByText("STUDIO")).not.toBeInTheDocument();
  expect(screen.queryByText("KNOWLEDGE BASE")).not.toBeInTheDocument();
  expect(screen.queryByText("RETRIEVE · RERANK · RESPOND")).not.toBeInTheDocument();
});

test("navigates to retrieval evaluation and renders the frozen quality gate", async () => {
  const summary = {
    report_id: "retrieval-official",
    dataset_id: "rag-enterprise-retrieval",
    dataset_version: "1.0.0",
    commit: "88c5e1e825f1678c74c193591621a26ae05c84ab",
    run_at: "2026-08-08T15:39:41Z",
    models: { embedding: "embedding@revision", reranker: "reranker@revision" },
    passed: true,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url === "/api/documents") return new Response(JSON.stringify([]), { status: 200 });
    if (url === "/api/evaluations") return new Response(JSON.stringify([summary]), { status: 200 });
    if (url === "/api/evaluations/retrieval-official") {
      return new Response(JSON.stringify({
        ...summary,
        parameters: { retrieve_k: 10, rerank_k: 5 },
        query_count: 20,
        recall_at_5: { value: 1, threshold: .8, baseline: 1, passed: true, regressed: false },
        vector_mrr: { value: .9083, threshold: .6, baseline: .9083, passed: true, regressed: false },
        rerank_mrr: { value: .975, threshold: .7, baseline: .9667, passed: true, regressed: false },
      }), { status: 200 });
    }
    return new Response(null, { status: 404 });
  });

  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "检索评测" }));

  expect(await screen.findByText("质量门已通过")).toBeInTheDocument();
  expect(screen.getByText("Recall@5")).toBeInTheDocument();
  expect(screen.getByText("最终排序 MRR")).toBeInTheDocument();
  expect(screen.getAllByText("通过")).toHaveLength(3);
  expect(screen.getByText(/页面只读取已生成的正式报告/)).toBeInTheDocument();
  expect(window.location.pathname).toBe("/evaluation/retrieval");
  expect(screen.queryByText("系统状态")).not.toBeInTheDocument();
});

test("shows the evaluation empty state", async () => {
  window.history.replaceState({}, "", "/evaluation/retrieval");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url === "/api/evaluations") return new Response(JSON.stringify([]), { status: 200 });
    return new Response(JSON.stringify([]), { status: 200 });
  });

  render(<App />);

  expect(await screen.findByText("还没有正式评测报告")).toBeInTheDocument();
  expect(screen.getByText(/页面不会启动重量评测任务/)).toBeInTheDocument();
});

test("shows API errors", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ error: { message: "后端不可用" } }), { status: 503 }),
  );
  render(<App />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("后端不可用"));
});

test("requires confirmation before deleting a document", async () => {
  const confirmMock = vi.spyOn(window, "confirm").mockReturnValue(false);
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(documents), { status: 200 }),
  );
  render(<App />);

  await userEvent.click(await screen.findByRole("button", { name: "删除 profile.md" }));

  expect(confirmMock).toHaveBeenCalledWith("确认删除“profile.md”及其索引吗？");
  expect(fetchMock).not.toHaveBeenCalledWith("/api/documents/doc_1", expect.anything());
});
