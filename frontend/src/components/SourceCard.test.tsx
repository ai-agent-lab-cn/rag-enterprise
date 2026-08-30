import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { SourceCard } from "./SourceCard";
import type { Source } from "../types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/**
 * 这个文件补的是**视觉基线的盲区**。
 *
 * 演示数据里没有历史对话，问答工作台的截图永远是空态，来源卡和引用弹层一张基线都覆盖
 * 不到。而 SourceCard 恰恰做了 Modal → Dialog 的迁移——正是「测试全绿但功能不可用」
 * 最容易发生的地方（见 CLAUDE.md 第九条）。
 */
const SOURCE: Source = {
  knowledge_base_id: "kb_default",
  chunk_id: "chunk_1",
  document_id: "doc_1",
  filename: "报销制度.md",
  page: null,
  paragraph: 2,
  chunk_index: 3,
  char_count: 120,
  summary: "差旅报销的时限与凭证要求。",
  text: "出差结束后 30 日内提交报销单。",
  retrieval_score: 0.812,
  rerank_score: 0.934,
  retrieval_channels: ["vector", "lexical"],
  lexical_score: 4.21,
};

const CITATION = {
  ...SOURCE,
  document_version_id: "dv_001",
  content_sha256: "a".repeat(64),
};

function mockFetch(payload: unknown) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } }),
  );
}

test("点「查看原文」后弹出引用原文，且带版本与哈希", async () => {
  mockFetch(CITATION);
  render(<SourceCard source={SOURCE} index={0} defaultOpen />);

  await userEvent.click(screen.getByRole("button", { name: /查看 报销制度.md 原文/ }));

  const dialog = await screen.findByRole("dialog");
  expect(dialog).toHaveTextContent("可信引用原文");
  expect(dialog).toHaveTextContent("出差结束后 30 日内提交报销单。");
  // 版本与哈希是「可信引用」的全部意义所在，少一个这个弹层就白开了。
  expect(dialog).toHaveTextContent("dv_001");
  expect(dialog).toHaveTextContent("aaaaaaaaaaaaaaaa…");
});

test("定位失败时在卡片里报错，不弹空弹层", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ detail: "原文已被删除。" }), { status: 404 }),
  );
  render(<SourceCard source={SOURCE} index={0} defaultOpen />);

  await userEvent.click(screen.getByRole("button", { name: /查看 报销制度.md 原文/ }));

  await waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("双路召回时分别标出向量与词法分数", () => {
  render(<SourceCard source={SOURCE} index={0} defaultOpen />);

  expect(screen.getByTitle("向量召回相似度")).toHaveTextContent("0.812");
  expect(screen.getByTitle("BM25 词法召回分数")).toHaveTextContent("4.21");
  expect(screen.getByTitle("CrossEncoder 精排分数")).toHaveTextContent("0.934");
});

test("历史记录没有通路标记时只显示一个「召回」分数", () => {
  // V5 之前的回答没有 retrieval_channels，缺失表示通路未知，不能当成向量召回展示。
  render(<SourceCard source={{ ...SOURCE, retrieval_channels: undefined }} index={0} defaultOpen />);

  expect(screen.getByTitle("向量粗召回相似度")).toHaveTextContent("0.812");
  expect(screen.queryByTitle("BM25 词法召回分数")).toBeNull();
});
