import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { BadCasePage } from "./BadCasePage";
import type { GovernedBadCase } from "../types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/**
 * 又一处**视觉基线的盲区**：演示环境的 Bad Case 是 0 条，这一页的截图永远是空表，
 * 治理编辑区（三个输入框 + 状态流转按钮）一张基线都覆盖不到。见 SourceCard.test.tsx
 * 里同样的理由。
 */
const CASE: GovernedBadCase = {
  case_id: "bc_001",
  source_type: "evaluation",
  source_record_id: "rec_1",
  knowledge_base_id: "kb_default",
  dataset_version: "v2",
  question: "差旅费多久内报销？",
  expected_source_ids: ["chunk_a"],
  actual_source_ids: [],
  expected_answer_status: "answered",
  actual_answer_status: "insufficient_evidence",
  actual_answer: "没有找到相关资料。",
  failure_stage: "retrieval",
  root_cause: null,
  category: "召回缺失",
  severity: "high",
  assignee: null,
  fix_commit: null,
  status: "new",
  regression_added: false,
  created_at: "2026-08-01T00:00:00Z",
  confirmed_at: null,
  resolved_at: null,
  updated_at: "2026-08-01T00:00:00Z",
};

function mockApi(cases: GovernedBadCase[], onPut?: (body: unknown) => GovernedBadCase) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (init?.method === "PUT" || init?.method === "PATCH") {
      const body = JSON.parse(String(init.body));
      return new Response(JSON.stringify(onPut ? onPut(body) : cases[0]), { status: 200 });
    }
    if (url.includes("bad-case")) return new Response(JSON.stringify(cases), { status: 200 });
    return new Response("[]", { status: 200 });
  });
}

test("管理员能填根因并推进状态，请求带上刚输入的内容", async () => {
  let sent: Record<string, unknown> = {};
  mockApi([CASE], (body) => {
    sent = body as Record<string, unknown>;
    return { ...CASE, status: "confirmed" };
  });
  render(<BadCasePage isAdmin />);

  await userEvent.click(await screen.findByText("治理详情"));
  await userEvent.type(screen.getByLabelText(/根因/), "索引缺片段");
  await userEvent.type(screen.getByLabelText(/负责人/), "lucas");
  // status=new 的下一步是「确认」；按钮文案由状态机推出，不是写死的。
  await userEvent.click(screen.getByRole("button", { name: "确认" }));

  expect(sent.status).toBe("confirmed");
  expect(sent.root_cause).toBe("索引缺片段");
  expect(sent.assignee).toBe("lucas");
});

test("普通成员看不到治理编辑区", async () => {
  mockApi([CASE]);
  render(<BadCasePage isAdmin={false} />);

  await userEvent.click(await screen.findByText("治理详情"));
  expect(screen.queryByLabelText(/根因/)).toBeNull();
  expect(screen.queryByRole("button", { name: "确认" })).toBeNull();
});

test("已入回归集的案例不再提供「忽略」", async () => {
  mockApi([{ ...CASE, status: "regression_added" }]);
  render(<BadCasePage isAdmin />);

  await userEvent.click(await screen.findByText("治理详情"));
  expect(screen.queryByRole("button", { name: "忽略" })).toBeNull();
});

test("筛选按状态收窄列表", async () => {
  mockApi([CASE, { ...CASE, case_id: "bc_002", question: "年假怎么算？", status: "resolved" }]);
  render(<BadCasePage isAdmin />);

  const table = await screen.findByRole("table");
  expect(within(table).getAllByRole("row")).toHaveLength(3); // 表头 + 两行

  await userEvent.selectOptions(screen.getByLabelText("Bad Case 状态筛选"), "resolved");
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);
  expect(screen.getByText("年假怎么算？")).toBeVisible();
});
