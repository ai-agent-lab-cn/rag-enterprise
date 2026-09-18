import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import type {
  DocumentIndexState,
  EvaluationReport,
  GovernedOperation,
  IndexBuild,
  IndexEvaluationRunDetail,
} from "../types";
import { OperationDetailDialog } from "./OperationDetailDialog";

afterEach(cleanup);

// fixture 逐字段齐全：组件对 GovernedOperation / IndexBuild 是直接取字段渲染的，
// 少一个字段就会在某一行读到 undefined。
const OPERATION_BUILD: GovernedOperation = {
  operation_id: "op_build_1",
  operation_type: "index_build",
  knowledge_base_id: "kb_1",
  data_source_id: null,
  document_id: null,
  document_version_id: null,
  status: "partial_failed",
  current_stage: "build",
  progress_mode: "count",
  progress_percent: 100,
  total_count: 2,
  completed_count: 1,
  processing_count: 0,
  failed_count: 1,
  error_code: null,
  error_message: null,
  started_at: "2026-09-08T01:00:00Z",
  finished_at: "2026-09-08T01:05:00Z",
  created_at: "2026-09-08T00:59:00Z",
  updated_at: "2026-09-08T01:05:00Z",
};

const BUILD: IndexBuild = {
  index_build_id: "ib_1",
  operation_id: "op_build_1",
  index_version_id: "iv_20260908_a",
  attempt_no: 1,
  build_type: "full",
  status: "partial_failed",
  total_documents: 2,
  queued_documents: 0,
  processing_documents: 0,
  succeeded_documents: 1,
  failed_documents: 1,
  failure_code: null,
  failure_reason: null,
  progress_percent: 100,
  current_stage: "build",
  started_at: "2026-09-08T01:00:00Z",
  finished_at: "2026-09-08T01:05:00Z",
  created_at: "2026-09-08T00:59:00Z",
  updated_at: "2026-09-08T01:05:00Z",
};

const DOC_READY: DocumentIndexState = {
  index_build_id: "ib_1",
  index_version_id: "iv_20260908_a",
  document_id: "doc_ok",
  document_version_id: "dv_ok",
  filename: "手册.md",
  vector_status: "ready",
  keyword_status: "ready",
  metadata_status: "ready",
  overall_status: "ready",
  chunk_count: 42,
  failure_stage: null,
  failure_code: null,
  failure_reason: null,
  updated_at: "2026-09-08T01:05:00Z",
};

const DOC_FAILED: DocumentIndexState = {
  index_build_id: "ib_1",
  index_version_id: "iv_20260908_a",
  document_id: "doc_bad",
  document_version_id: "dv_bad",
  filename: "损坏.pdf",
  vector_status: "failed",
  keyword_status: "pending",
  metadata_status: "pending",
  overall_status: "failed",
  chunk_count: 0,
  failure_stage: "vector",
  failure_code: "EMBEDDING_TIMEOUT",
  failure_reason: "向量化超时，已重试 3 次",
  updated_at: "2026-09-08T01:05:00Z",
};

const DOC_MISSING: DocumentIndexState = {
  ...DOC_FAILED,
  document_id: "doc_missing",
  document_version_id: "dv_missing",
  filename: "x.md",
  failure_stage: "parse",
  failure_code: "SOURCE_FILE_MISSING",
  failure_reason: "源文件在对象存储里已不存在",
};

const OPERATION_EVAL: GovernedOperation = {
  ...OPERATION_BUILD,
  operation_id: "op_eval_1",
  operation_type: "index_evaluation",
  status: "succeeded",
  current_stage: "persist_report",
  total_count: 1,
  completed_count: 1,
  failed_count: 0,
};

const REPORT: EvaluationReport = {
  report_id: "rpt_2026_09",
  dataset_id: "ds_default",
  dataset_version: "v3",
  commit: "abc1234",
  run_at: "2026-09-08T01:04:00Z",
  models: { embedding: "test/embedding" },
  official: true,
  passed: false,
  config_fingerprint: "f".repeat(64),
  parameters: { top_k: 10 },
  query_count: 120,
  recall_at_5: { value: 0.82, threshold: 0.8, baseline: 0.79, passed: true, regressed: false },
  recall_at_10: { value: 0.9, threshold: 0.85, baseline: null, passed: true, regressed: false },
  vector_mrr: { value: 0.61, threshold: 0.6, baseline: 0.63, passed: true, regressed: false },
  rerank_mrr: { value: 0.7, threshold: 0.75, baseline: 0.72, passed: false, regressed: true },
  ndcg_at_10: null,
  metadata_filter_accuracy: { value: 1, threshold: 0.95, baseline: null, passed: true, regressed: false },
};

const EVAL_RUN: IndexEvaluationRunDetail = {
  evaluation_run_id: "er_1",
  knowledge_base_id: "kb_1",
  index_version_id: "iv_20260908_a",
  operation_id: "op_eval_1",
  dataset_id: "ds_default",
  dataset_version: "v3",
  dataset_slug: "default",
  status: "succeeded",
  config_fingerprint: "f".repeat(64),
  baseline_report_id: "rpt_baseline",
  report_id: "rpt_2026_09",
  official: true,
  passed: false,
  attempt_count: 1,
  max_attempts: 3,
  requested_by: "admin",
  failure_code: null,
  failure_reason: null,
  available_at: "2026-09-08T01:00:00Z",
  started_at: "2026-09-08T01:00:00Z",
  finished_at: "2026-09-08T01:04:00Z",
  created_at: "2026-09-08T00:59:00Z",
  updated_at: "2026-09-08T01:04:00Z",
  // Record<string, unknown> 的字段用对象字面量，interface 没有隐式索引签名。
  models: { embedding: "test/embedding" },
  metrics: {},
  config_snapshot: {},
  component_manifest: {},
  report: REPORT,
};

/** 摘要项是 dt + dd 一组包在同一个 div 里，按 dt 文案定位整组。 */
function summaryGroup(label: string) {
  return screen.getByText(label).closest("div") as HTMLElement;
}

test("index_build 详情列出每份资料的三条 lane 状态、切片数与整体状态", () => {
  render(
    <OperationDetailDialog
      operation={OPERATION_BUILD}
      build={BUILD}
      buildDocuments={[DOC_READY, DOC_FAILED]}
      onClose={() => undefined}
    />,
  );

  expect(screen.getByRole("dialog", { name: "索引构建详情" })).toBeInTheDocument();
  // 构建进度来自 IndexBuild 的真实计数，不是页面自己推的。
  expect(screen.getByText("第 1 次构建 · 1/2 份完成 · 1 份失败")).toBeVisible();

  const table = screen.getByRole("table", { name: "资料索引状态" });
  expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
    "资料",
    "Vector",
    "Keyword",
    "Metadata",
    "切片数",
    "整体状态",
  ]);

  const rows = within(table).getAllByRole("row");
  expect(rows).toHaveLength(3); // 表头 + 2 份资料
  expect(within(rows[1]).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
    "手册.md",
    "可用",
    "可用",
    "可用",
    "42",
    "可用",
  ]);
  // 三条 lane 各自独立：向量失败时另外两条仍然停在「等待处理」，不能被整体状态盖掉。
  expect(within(rows[2]).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
    "损坏.pdf",
    "失败",
    "等待处理",
    "等待处理",
    "0",
    "失败",
  ]);
});

test("失败资料的原因挂在可点的 ⓘ 上，悬停即可见", async () => {
  render(
    <OperationDetailDialog
      operation={OPERATION_BUILD}
      build={BUILD}
      buildDocuments={[DOC_READY, DOC_FAILED]}
      onClose={() => undefined}
    />,
  );

  // 没有失败原因的那份资料不该长出一个点了没反应的 ⓘ。
  expect(screen.getAllByRole("button", { name: /的索引失败原因/ })).toHaveLength(1);

  const hint = screen.getByRole("button", { name: "损坏.pdf 的索引失败原因" });
  expect(hint).toBeEnabled();
  await userEvent.hover(hint);
  expect((await screen.findAllByText("向量化超时，已重试 3 次")).length).toBeGreaterThan(0);
});

test("目标版本渲染成可点的链接，点击后交给调用方跳转", async () => {
  const onOpenVersion = vi.fn();
  render(
    <OperationDetailDialog
      operation={OPERATION_BUILD}
      build={BUILD}
      buildDocuments={[DOC_READY]}
      onOpenVersion={onOpenVersion}
      onClose={() => undefined}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "iv_20260908_a" }));

  expect(onOpenVersion).toHaveBeenCalledWith("iv_20260908_a");
});

test("源文件丢失时给出两条恢复入口，并说明必须重建索引版本", async () => {
  const onOpenDocuments = vi.fn();
  const onDeleteDocument = vi.fn();
  render(
    <OperationDetailDialog
      operation={OPERATION_BUILD}
      build={BUILD}
      buildDocuments={[DOC_READY, DOC_MISSING]}
      onOpenDocuments={onOpenDocuments}
      onDeleteDocument={onDeleteDocument}
      onClose={() => undefined}
    />,
  );

  expect(screen.getByText("1 份资料的源文件已丢失，索引无法构建。")).toBeVisible();
  // 「点了按钮什么也不会发生」的反面：这里要说清楚补救之后还得再建一个版本，
  // 否则用户会以为重新上传就能让这次构建自己好起来。
  expect(screen.getByText("重新创建一个索引版本")).toBeVisible();

  await userEvent.click(screen.getByRole("button", { name: "前往资料页重新上传" }));
  expect(onOpenDocuments).toHaveBeenCalledWith("doc_missing");

  await userEvent.click(screen.getByRole("button", { name: "删除「x.md」" }));
  expect(onDeleteDocument).toHaveBeenCalledWith("doc_missing", "x.md");
});

test("失败码不是 SOURCE_FILE_MISSING 时不出现恢复入口", () => {
  render(
    <OperationDetailDialog
      operation={OPERATION_BUILD}
      build={BUILD}
      buildDocuments={[DOC_FAILED]}
      onOpenDocuments={vi.fn()}
      onDeleteDocument={vi.fn()}
      onClose={() => undefined}
    />,
  );

  // 恢复入口只对「源文件没了」这一种失败成立，别的失败点了也没用。
  expect(screen.queryByRole("button", { name: "前往资料页重新上传" })).toBeNull();
  expect(screen.queryByRole("button", { name: /^删除「/ })).toBeNull();
});

test("buildDocuments 为 null 表示读取中，渲染骨架而不是空态", () => {
  render(
    <OperationDetailDialog
      operation={OPERATION_BUILD}
      build={BUILD}
      buildDocuments={null}
      buildLoading
      onClose={() => undefined}
    />,
  );

  // 「还在读」与「确实没有」必须长得不一样，否则构建刚开始那几秒会显示成
  // 「旧构建批次未记录单资料状态」。
  expect(screen.queryByRole("heading", { name: "暂无资料状态" })).toBeNull();
  expect(screen.getByRole("status")).toHaveTextContent("正在读取资料索引状态");
  const table = screen.getByRole("table", { name: "资料索引状态" });
  expect(table).toHaveAttribute("aria-busy", "true");
  expect(within(table).getAllByRole("row")).toHaveLength(4); // 表头 + 3 行骨架
});

test("buildDocuments 为空数组时才显示空态", () => {
  render(
    <OperationDetailDialog
      operation={OPERATION_BUILD}
      build={BUILD}
      buildDocuments={[]}
      onClose={() => undefined}
    />,
  );

  expect(screen.getByRole("heading", { name: "暂无资料状态" })).toBeVisible();
  expect(screen.queryByRole("table", { name: "资料索引状态" })).toBeNull();
});

test("index_evaluation 详情展示数据集、截断的配置指纹、基线与报告 ID、尝试次数", () => {
  render(
    <OperationDetailDialog
      operation={OPERATION_EVAL}
      evaluationRun={EVAL_RUN}
      onClose={() => undefined}
    />,
  );

  expect(screen.getByRole("dialog", { name: "正式评测详情" })).toBeInTheDocument();
  expect(screen.getByText("ds_default · v3")).toBeVisible();
  expect(screen.getByText("rpt_baseline")).toBeVisible();
  expect(screen.getByText("rpt_2026_09")).toBeVisible();
  expect(screen.getByText("1/3")).toBeVisible();

  // 64 位指纹只显示前 12 位，完整值留在 title 里——整串铺在弹框上会把这一行撑爆。
  const fingerprint = screen.getByText("ffffffffffff…");
  expect(fingerprint).toHaveAttribute("title", "f".repeat(64));
});

test("有报告时逐项展示指标百分比、阈值与回退标记，缺项显示「—」", () => {
  render(
    <OperationDetailDialog
      operation={OPERATION_EVAL}
      evaluationRun={EVAL_RUN}
      onClose={() => undefined}
    />,
  );

  const recall = summaryGroup("Recall@5");
  expect(recall.textContent).toContain("82.0%");
  expect(recall.textContent).toContain("阈值 80.0%");

  const rerank = summaryGroup("精排 MRR");
  expect(rerank.textContent).toContain("70.0%");
  expect(rerank.textContent).toContain("阈值 75.0%");
  expect(rerank.textContent).toContain("较基线回退");

  // 报告里没有这一项时显示「—」，不用 0 冒充——0% 是一个可怕的假结论。
  expect(summaryGroup("nDCG@10").textContent).toBe("nDCG@10—");
});

test("跑完但未达阈值时，说明这份报告仍可用于三层验证", () => {
  const { rerender } = render(
    <OperationDetailDialog
      operation={OPERATION_EVAL}
      evaluationRun={EVAL_RUN}
      onClose={() => undefined}
    />,
  );

  // official 与 passed 解耦在页面上的落点：未达阈值不等于这次运行作废，
  // 能不能发布由三层验证决定。此前后端把两者绑在一起，页面因此把
  // 「跑完没达标」显示成「缺少可用报告」。
  expect(
    screen.getByText(
      "部分指标未达到冻结阈值。这份报告仍是受控正式运行的证据，可用于三层验证——最终是否可发布由三层验证决定，不由绝对阈值单独决定。",
    ),
  ).toBeVisible();

  rerender(
    <OperationDetailDialog
      operation={OPERATION_EVAL}
      evaluationRun={{ ...EVAL_RUN, passed: true }}
      onClose={() => undefined}
    />,
  );

  expect(screen.getByText("全部指标达到冻结阈值。")).toBeVisible();
  expect(screen.queryByText(/仍是受控正式运行的证据/)).toBeNull();
});

test("评测失败时可以重新运行，并展示后端给的失败原因与失败码", async () => {
  const onRetryEvaluation = vi.fn();
  const failedRun: IndexEvaluationRunDetail = {
    ...EVAL_RUN,
    status: "failed",
    passed: null,
    report_id: null,
    report: null,
    attempt_count: 1,
    failure_code: "EVALUATION_TIMEOUT",
    failure_reason: "评测执行超时",
  };
  render(
    <OperationDetailDialog
      operation={{ ...OPERATION_EVAL, status: "failed" }}
      evaluationRun={failedRun}
      onRetryEvaluation={onRetryEvaluation}
      onClose={() => undefined}
    />,
  );

  expect(screen.getByText("EVALUATION_TIMEOUT").parentElement?.textContent).toBe(
    "评测执行超时EVALUATION_TIMEOUT",
  );

  const retry = screen.getByRole("button", { name: "重新运行评测" });
  expect(retry).toBeEnabled();
  await userEvent.click(retry);
  expect(onRetryEvaluation).toHaveBeenCalledWith("er_1");
});

test("重试次数用尽时按钮禁用，且说得出为什么", () => {
  render(
    <OperationDetailDialog
      operation={{ ...OPERATION_EVAL, status: "failed" }}
      evaluationRun={{
        ...EVAL_RUN,
        status: "failed",
        passed: null,
        report: null,
        attempt_count: 3,
        max_attempts: 3,
      }}
      onRetryEvaluation={vi.fn()}
      onClose={() => undefined}
    />,
  );

  // CLAUDE.md 第一条：禁用必须说得出原因，而且原因要在光标不动时也能被发现——
  // Button 把它放进一个独立的、可聚焦的 ⓘ。
  expect(screen.getByRole("button", { name: "重新运行评测" })).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "为什么不可用：已达最大重试次数，请创建新的评测任务" }),
  ).toBeEnabled();
});

test("排队中的评测可以取消，此时没有重试入口", async () => {
  const onCancelEvaluation = vi.fn();
  const onRetryEvaluation = vi.fn();
  render(
    <OperationDetailDialog
      operation={{ ...OPERATION_EVAL, status: "queued", current_stage: "queued" }}
      evaluationRun={{
        ...EVAL_RUN,
        status: "queued",
        passed: null,
        report_id: null,
        report: null,
        started_at: null,
        finished_at: null,
      }}
      onCancelEvaluation={onCancelEvaluation}
      onRetryEvaluation={onRetryEvaluation}
      onClose={() => undefined}
    />,
  );

  expect(screen.queryByRole("button", { name: "重新运行评测" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "取消评测" }));

  expect(onCancelEvaluation).toHaveBeenCalledWith("er_1");
});

test("长错误完整显示在弹框里", () => {
  const message = `索引构建在向量化阶段失败：${"上游模型返回 503。".repeat(12)}`;
  render(
    <OperationDetailDialog
      operation={{ ...OPERATION_BUILD, status: "failed", error_message: message }}
      build={BUILD}
      buildDocuments={[]}
      onClose={() => undefined}
    />,
  );

  // 主表格那一行装不下这种长度，弹框是它唯一能被完整读到的地方。
  expect(screen.getByText(message)).toBeVisible();
});

test("progress_percent 为 null 时显示「—」，不是 0%", () => {
  const { rerender } = render(
    <OperationDetailDialog
      operation={{
        ...OPERATION_BUILD,
        operation_type: "sync_run",
        status: "queued",
        current_stage: "queued",
        progress_percent: null,
        total_count: 10,
        completed_count: 3,
        processing_count: 2,
        finished_at: null,
      }}
      onClose={() => undefined}
    />,
  );

  // 「还不知道进度」与「一点没跑」是两件事，用 0% 表达前者会让用户以为任务卡死了。
  expect(summaryGroup("进度").textContent).toBe("进度—");
  expect(summaryGroup("目标版本").textContent).toBe("目标版本—");
  expect(summaryGroup("结束时间").textContent).toBe("结束时间—");
  expect(summaryGroup("处理数量").textContent).toBe("处理数量3/10 · 处理中 2");
  // 状态胶囊与「当前阶段」都会写「等待处理」，所以按 summary 项精确断言——
  // getByText 在这里会撞上两个节点。
  expect(summaryGroup("状态").textContent).toBe("状态等待处理");
  expect(summaryGroup("当前阶段").textContent).toBe("当前阶段等待处理");

  rerender(
    <OperationDetailDialog
      operation={{ ...OPERATION_BUILD, operation_type: "sync_run", progress_percent: 66.6 }}
      onClose={() => undefined}
    />,
  );

  expect(summaryGroup("进度").textContent).toBe("进度67%");
});

test("关闭按钮回调调用方", async () => {
  const onClose = vi.fn();
  render(
    <OperationDetailDialog
      operation={{ ...OPERATION_BUILD, operation_type: "sync_run" }}
      onClose={onClose}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "关闭" }));

  expect(onClose).toHaveBeenCalled();
});
