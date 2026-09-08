import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import type { IndexVersionCandidatePreview, IndexVersionCreationContext } from "../types";
import { IndexVersionCreationWizard } from "./IndexVersionCreationWizard";

afterEach(cleanup);

const CONTEXT: IndexVersionCreationContext = {
  scenario: "candidate",
  definition: {
    chunking: { version: "v1-700-100", chunk_size: 700, chunk_overlap: 100 },
    parser: { schema_version: "structured-v2" },
    embedding: { model: "test/embedding", dimension: 1024 },
    components: { keyword_index_schema_version: "bm25-cache-v1" },
    processing_options: { chunk_size: 700, chunk_overlap: 100 },
    config_fingerprint: "a".repeat(64),
    capabilities: [],
  },
  active_version: { index_version_id: "iv_active" },
  candidate_version: null,
  latest_document_snapshot: null,
  document_scope: {
    included: 12,
    excluded: 2,
    source_bytes: 32000,
    parse_failed: 1,
    missing_current_revision: 1,
  },
  document_exclusions: [
    { document_id: "doc_failed", filename: "解析失败.pdf", reason: "parse_failed", latest_status: "failed", parse_failure_code: "PARSER_FAILED" },
    { document_id: "doc_missing", filename: "等待处理.docx", reason: "missing_current_revision", latest_status: "pending", parse_failure_code: null },
  ],
  build_capacity: { active_builds: 0, max_concurrent_builds: 2, remaining_build_slots: 2, max_documents: 10000 },
  document_diff: { added: 3, removed: 1, updated: 2, unchanged: 7 },
  document_set_fingerprint: "b".repeat(64),
  config_changed: true,
  document_changed: true,
  creation_allowed: true,
  blocked_reasons: [],
};

const PREVIEW: IndexVersionCandidatePreview = {
  ...CONTEXT,
  reason: "config_changed",
  force: false,
  force_reason: null,
  config_fingerprint: "a".repeat(64),
  release_fingerprint: "c".repeat(64),
  // 展开而不是直接赋值：config_snapshot 声明为 Record<string, unknown>（对应后端的
  // dict[str, object]），而 IndexDefinitionView 是 interface——interface 没有隐式索引签名，
  // 不能赋给 Record。展开后是匿名对象类型，有隐式索引签名，检查通过且数据不变。
  config_snapshot: { ...CONTEXT.definition },
  component_manifest: CONTEXT.definition.components,
  config_diff: [{ field: "chunking_version", active: "v1-500-50", candidate: "v1-700-100" }],
  estimated_documents: 12,
  estimated_chunks: 48,
  estimated_embedding_units: 32000,
};

test("五步向导展示真实配置与文档范围，并只提交后端预览结果", async () => {
  const onPreview = vi.fn().mockResolvedValue(PREVIEW);
  const onCreate = vi.fn().mockResolvedValue(undefined);
  render(
    <IndexVersionCreationWizard
      open
      context={CONTEXT}
      busy={false}
      onClose={() => undefined}
      onPreview={onPreview}
      onCreate={onCreate}
    />,
  );

  expect(screen.getByText("1. 创建原因")).toBeVisible();
  await userEvent.selectOptions(screen.getByLabelText("创建原因"), "config_changed");
  await userEvent.click(screen.getByRole("button", { name: "下一步" }));

  expect(screen.getByLabelText("Chunk Size")).toHaveValue(700);
  expect(screen.getByLabelText("Chunk Overlap")).toHaveValue(100);
  await userEvent.click(screen.getByRole("button", { name: "生成预览" }));

  await waitFor(() => expect(onPreview).toHaveBeenCalled());
  expect(await screen.findByText("纳入 12 份")).toBeVisible();
  expect(screen.getByText("解析失败.pdf · 解析失败（PARSER_FAILED）")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "下一步" }));
  expect(screen.getByText("预计处理 12 份资料")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "下一步" }));
  expect(screen.getByText("发布指纹")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "创建并开始构建" }));

  expect(onCreate).toHaveBeenCalledWith(PREVIEW);
});
