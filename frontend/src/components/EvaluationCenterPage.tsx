import { useEffect, useState } from "react";
import { api } from "../api";
import type { EvaluationCenterOverview, EvaluationReportSummary, PipelineEvaluation } from "../types";
import { AnswerEvaluationPage } from "./AnswerEvaluationPage";
import { EvaluationPage } from "./EvaluationPage";
import { TopbarPortal } from "./TopbarPortal";
import { Badge } from "./ui/Badge";
import { type Column, DataTable } from "./ui/DataTable";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Skeleton } from "./ui/Skeleton";

/**
 * 评测中心：一页看完「当前系统质量怎么样」。
 *
 * 四类质量纵向排开而不是各占一个 Tab——指标分类不是用户的工作场景，把它们做成 Tab
 * 或左侧菜单，等于要求人先知道去哪儿找，再逐个点开拼出全局印象。判断质量本来是
 * 一件事，就该在一屏里完成。
 *
 * Bad Case 与链路验收不在这里：前者是问题治理工作流，后者承担版本放行职责，
 * 各自是独立的工作场景，因此是独立菜单。
 */
const SECTIONS = [
  { id: "summary", label: "质量总览" },
  { id: "retrieval", label: "检索质量" },
  { id: "answer", label: "回答质量" },
  { id: "pipeline", label: "工程指标" },
] as const;

export function EvaluationCenterPage() {
  const [overview, setOverview] = useState<EvaluationCenterOverview | null>(null);
  const [pipeline, setPipeline] = useState<PipelineEvaluation | null>(null);
  const [reports, setReports] = useState<EvaluationReportSummary[] | null>(null);
  const [error, setError] = useState("");

  // 三块数据并行拉取，各 Section 自己显示加载态：整页 Loading 会让已经就绪的
  // 质量总览也跟着一起等最慢的那个请求。
  useEffect(() => {
    const fail = (message: string) => (reason: unknown) =>
      setError(reason instanceof Error ? reason.message : message);
    api.getEvaluationCenterOverview().then(setOverview, fail("无法读取评测总览。"));
    api.getPipelineEvaluation().then(setPipeline, fail("无法读取工程指标。"));
    api.listEvaluations().then(setReports, fail("无法读取最近评测。"));
  }, []);

  return (
    <section className="px-6 pt-5 pb-8" aria-label="评测中心">
      <TopbarPortal>
        {overview ? (
          <Badge tone={overview.passed ? "success" : "danger"} shape="status">
            {overview.passed ? "统一质量门已通过" : "统一质量门未通过"}
          </Badge>
        ) : null}
      </TopbarPortal>
      {/* 锚点导航吸顶：页面很长，滚到「工程指标」时仍要能一键跳回「质量总览」。 */}
      <nav
        className="sticky top-0 z-[2] mb-1 flex flex-wrap gap-3.5 border-b border-line bg-surface py-2.5"
        aria-label="评测中心小节"
      >
        {SECTIONS.map((item) => (
          <a key={item.id} href={`#${item.id}`} className="text-[12px] text-[#5738dc] no-underline hover:underline">
            {item.label}
          </a>
        ))}
      </nav>
      {error ? (
        <ErrorBanner>{error}</ErrorBanner>
      ) : null}

      <section id="summary" className="scroll-mt-14 pt-[18px]">
        <h2 className="mt-0 mb-0.5 text-[15px] font-bold">质量总览</h2>
        <OverviewPanel overview={overview} />
      </section>

      <section id="retrieval" className="scroll-mt-14 pt-[18px]">
        <h2 className="mt-0 mb-0.5 text-[15px] font-bold">检索质量</h2>
        <p className="mt-0 mb-2.5 text-[12px] text-ink-faint">正确证据有没有被召回，以及是否排在足够靠前的位置？</p>
        <EvaluationPage />
      </section>

      <section id="answer" className="scroll-mt-14 pt-[18px]">
        <h2 className="mt-0 mb-0.5 text-[15px] font-bold">回答质量</h2>
        <p className="mt-0 mb-2.5 text-[12px] text-ink-faint">模型是否基于检索证据生成了正确、可信、可引用的答案？</p>
        <AnswerEvaluationPage />
      </section>

      <section id="pipeline" className="scroll-mt-14 pt-[18px]">
        <h2 className="mt-0 mb-0.5 text-[15px] font-bold">工程指标</h2>
        <p className="mt-0 mb-2.5 text-[12px] text-ink-faint">RAG 链路的性能、稳定性和成本是否符合要求？</p>
        {pipeline ? <PipelinePanel summary={pipeline} /> : <Skeleton className="h-[180px] rounded-lg" />}
      </section>

      <section id="recent" className="scroll-mt-14 pt-[18px]">
        <h2 className="mt-0 mb-0.5 text-[15px] font-bold">最近评测</h2>
        <RecentRuns reports={reports} />
      </section>
    </section>
  );
}

function RecentRuns({ reports }: { reports: EvaluationReportSummary[] | null }) {
  const columns: Column<EvaluationReportSummary>[] = [
    {
      key: "report",
      header: "评测",
      truncate: false,
      render: (item) => (
        <>
          <strong>{item.report_id}</strong>
          <small className="block text-ink-faint">{item.dataset_id}</small>
        </>
      ),
    },
    { key: "dataset_version", header: "数据集版本", render: (item) => item.dataset_version },
    { key: "run_at", header: "运行时间", render: (item) => new Date(item.run_at).toLocaleString("zh-CN") },
    {
      key: "passed",
      header: "结论",
      truncate: false,
      render: (item) => (
        <Badge tone={item.passed ? "success" : "danger"} shape="status">
          {item.passed ? "PASS" : "FAIL"}
        </Badge>
      ),
    },
  ];
  return (
    <DataTable
      rows={reports}
      columns={columns}
      rowKey={(item) => item.report_id}
      label="最近评测"
      emptyState={{
        kind: "empty",
        title: "还没有正式评测记录。",
        description: "评测报告需要由离线评测任务生成，本页不会自动触发评测。",
      }}
    />
  );
}

/** 结论徽章的三态映射：`null` 是「待核对」，不能塌缩成未通过。 */
function verdictBadge(passed: boolean | null | undefined) {
  return (
    <Badge tone={passed === false ? "danger" : passed === true ? "success" : "neutral"} shape="status">
      {passed === false ? "未通过" : passed === true ? "通过" : "待核对"}
    </Badge>
  );
}

type OverviewRow = { scope: string; rule: string; passed: boolean | null | undefined };

const OVERVIEW_COLUMNS: Column<OverviewRow>[] = [
  { key: "scope", header: "范围", width: "24%", render: (row) => row.scope },
  { key: "rule", header: "版本/规则", width: "52%", render: (row) => row.rule },
  { key: "verdict", header: "结论", width: "24%", truncate: false, render: (row) => verdictBadge(row.passed) },
];

/**
 * 质量总览。
 *
 * 这里曾照抄 `ui/DataTable` 的视觉类名而不接组件，理由是「行永远是写死的 4 行常量数组，
 * 给必填的 `emptyState` 编一个永不命中的文案属于防御不可能场景」。**2026-09-03 改判**：
 * 那个理由没算复制视觉类名的代价，而代价已经兑现——照抄的行高是 `h-11`，`DataTable`
 * 的默认行高是 `h-14`，两者早已分叉，原注释里「视觉上仍与全站表格一致」这句是错的。
 * `emptyState` 是类型契约的一部分，填一次不会被执行；照抄的 6 处类名是每次改 DataTable
 * 都会静默腐烂的复制品（CLAUDE.md 第五条）。
 */
function OverviewPanel({ overview }: { overview: EvaluationCenterOverview | null }) {
  if (!overview) {
    return (
      <div className="grid gap-2 pb-2.5">
        <Skeleton className="h-5 w-56" />
        <Skeleton className="h-[124px] rounded-lg" />
      </div>
    );
  }
  const rows: OverviewRow[] = [
    { scope: "检索质量", rule: overview.retrieval_report?.dataset_version ?? "无正式报告", passed: overview.retrieval_report?.passed },
    { scope: "回答质量", rule: overview.answer_report?.dataset_version ?? "无正式报告", passed: overview.answer_report?.passed },
    { scope: "工程指标", rule: "读取同步运行记录", passed: null },
    { scope: "安全边界", rule: "ACL 泄漏必须为 0", passed: overview.passed },
  ];
  return (
    <div>
      <header className="mb-3.5 flex items-start justify-between gap-2">
        <div>
          <h3 className="my-[16.38px] text-[16.38px] font-bold">
            {overview.passed ? "统一质量门已通过" : "统一质量门未通过"}
          </h3>
          <p className="mt-1 mb-0 text-[13.12px] text-ink-muted">{overview.report_count} 份正式报告已纳入治理结论。</p>
        </div>
      </header>
      <DataTable
        label="统一质量门结论"
        rows={rows}
        rowKey={(row) => row.scope}
        columns={OVERVIEW_COLUMNS}
        emptyState={{ kind: "empty", title: "没有质量门结论。", description: "四个范围的结论是固定的，出现这句说明数据构造失败。" }}
      />
    </div>
  );
}

type PipelineRow = { metric: string; count: number };

/* numeric 开着才有等宽数字 + 右对齐——六行计数不等宽时整列看着是歪的。 */
const PIPELINE_COLUMNS: Column<PipelineRow>[] = [
  { key: "metric", header: "工程指标", width: "70%", render: (row) => row.metric },
  { key: "count", header: "数量", width: "30%", numeric: true, render: (row) => row.count },
];

function PipelinePanel({ summary }: { summary: PipelineEvaluation }) {
  const rows: PipelineRow[] = [
    { metric: "新增", count: summary.added_count },
    { metric: "更新", count: summary.updated_count },
    { metric: "删除", count: summary.deleted_count },
    { metric: "跳过", count: summary.skipped_count },
    { metric: "失败", count: summary.failed_count },
    { metric: "重试", count: summary.retry_count },
  ];
  return (
    <div>
      <header className="mb-3.5 flex items-start justify-between gap-2">
        <div>
          <h3 className="my-[16.38px] text-[16.38px] font-bold">{summary.run_count} 个同步批次</h3>
          <p className="mt-1 mb-0 text-[13.12px] text-ink-muted">
            平均耗时 {(summary.average_duration_ms / 1000).toFixed(1)} 秒 · 失败率{" "}
            {(summary.failure_rate * 100).toFixed(1)}%
          </p>
        </div>
      </header>
      <DataTable
        label="同步批次工程指标"
        rows={rows}
        rowKey={(row) => row.metric}
        columns={PIPELINE_COLUMNS}
        emptyState={{ kind: "empty", title: "没有工程指标。", description: "六项计数是固定的，出现这句说明数据构造失败。" }}
      />
    </div>
  );
}
