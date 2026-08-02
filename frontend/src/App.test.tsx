import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";

const documents = [{ document_id: "doc_1", filename: "profile.md", chunk_count: 3, status: "ready" }];

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("loads documents and renders query sources", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/documents") return new Response(JSON.stringify(documents), { status: 200 });
    if (url === "/api/query" && init?.method === "POST") {
      return new Response(JSON.stringify({
        answer: "系统使用可解释的检索流程。",
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
  expect(screen.getByText("召回 0.820")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/query", expect.objectContaining({ method: "POST" }));
});

test("shows API errors", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ error: { message: "后端不可用" } }), { status: 503 }),
  );
  render(<App />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("后端不可用"));
});
