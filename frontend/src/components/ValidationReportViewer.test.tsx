import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import type { ValidationReport } from "../types";
import { ValidationReportViewer } from "./ValidationReportViewer";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const REPORT_ID = "vr_9d8d8f01234567890abcdef8add0";
const REPORT: ValidationReport = {
  validation_report_id: REPORT_ID,
  index_version_id: "iv_1",
  index_build_id: "ib_1",
  status: "pass",
  policy_version: "v3",
  evaluation_set_version: "corpus_v2",
  baseline_version_id: null,
  integrity_result: { layer: "integrity", status: "pass", checks: [{ check_key: "document_coverage", status: "pass", expected: 5, actual: 5, severity: "error" }] },
  technical_result: { layer: "technical", status: "pass", checks: [] },
  retrieval_result: { layer: "retrieval", status: "pass", checks: [] },
  summary: "三层验证通过。",
  failure_items: [],
  report_source: "standard",
  started_at: "2026-09-15T05:00:00Z",
  finished_at: "2026-09-15T05:01:00Z",
  created_at: "2026-09-15T05:01:00Z",
};

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

test("点击已加载的验证报告 ID 打开三层报告详情", async () => {
  render(<ValidationReportViewer knowledgeBaseId="kb_default" versionId="iv_1" reportId={REPORT_ID} report={REPORT} />);

  await userEvent.click(screen.getByRole("button", { name: `查看验证报告 ${REPORT_ID}` }));

  const dialog = screen.getByRole("dialog", { name: "验证报告" });
  expect(dialog).toHaveTextContent(REPORT_ID);
  expect(dialog).toHaveTextContent("三层验证通过。");
  expect(dialog).toHaveTextContent("完整性检查");
  expect(dialog).toHaveTextContent("document_coverage");
});

test("激活弹框点击报告 ID 时按版本读取并打开匹配报告", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(json([REPORT]));
  render(<ValidationReportViewer knowledgeBaseId="kb_default" versionId="iv_1" reportId={REPORT_ID} />);

  await userEvent.click(screen.getByRole("button", { name: `查看验证报告 ${REPORT_ID}` }));

  expect(await screen.findByRole("dialog", { name: "验证报告" })).toHaveTextContent("三层验证通过。");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/knowledge-bases/kb_default/index-versions/iv_1/validations",
    expect.objectContaining({ headers: expect.any(Headers) }),
  );
});
