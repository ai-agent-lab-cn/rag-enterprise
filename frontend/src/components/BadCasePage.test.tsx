import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { api } from "../api";
import type { GovernedBadCase } from "../types";
import { BadCasePage } from "./BadCasePage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const RESOLVED_CASE: GovernedBadCase = {
  case_id: "case_1234567890abcdef",
  source_type: "online",
  source_record_id: "ans_1",
  knowledge_base_id: "kb_default",
  dataset_version: null,
  question: "为什么没有召回？",
  expected_source_ids: [],
  actual_source_ids: [],
  expected_answer_status: "answered",
  actual_answer_status: "insufficient_evidence",
  actual_answer: "资料不足。",
  failure_stage: "retrieval",
  root_cause: "过滤条件错误",
  category: "没召回",
  severity: "high",
  assignee: "owner",
  fix_commit: "abcdef1",
  status: "resolved",
  regression_added: false,
  regression_evaluation_run_id: null,
  regression_passed: null,
  regression_run_at: null,
  created_at: "2026-08-30T00:00:00Z",
  confirmed_at: "2026-08-30T00:00:00Z",
  resolved_at: "2026-08-30T00:00:00Z",
  updated_at: "2026-08-30T00:00:00Z",
};

test("已解决案例只能通过真实回归运行进入回归集", async () => {
  vi.spyOn(api, "listGovernedBadCases").mockResolvedValue([RESOLVED_CASE]);
  const run = vi.spyOn(api, "runGovernedBadCaseRegression").mockResolvedValue({
    ...RESOLVED_CASE,
    status: "regression_added",
    regression_added: true,
    regression_evaluation_run_id: "eval_regression_1",
    regression_passed: true,
    regression_run_at: "2026-09-21T00:00:00Z",
  });

  render(<BadCasePage isAdmin />);
  await userEvent.click(await screen.findByRole("button", { name: "治理详情" }));

  expect(screen.queryByRole("button", { name: "加入回归集" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "运行回归验证" }));

  expect(run).toHaveBeenCalledWith(RESOLVED_CASE.case_id);
  expect(await screen.findByText("eval_regression_1")).toBeInTheDocument();
  expect(screen.getByText("结论：通过")).toBeInTheDocument();
});
