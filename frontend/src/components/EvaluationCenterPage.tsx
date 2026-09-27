import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type {
  AnswerEvaluationReport,
  AnswerEvaluationSummary,
  EvaluationCenterOverview,
  EvaluationReport,
  EvaluationReportAssociations,
  EvaluationReportSummary,
  PipelineEvaluation,
} from "../types";
import {
  FormalReportDetailDialog,
  FormalReportDetailContent,
  type FormalReportKind,
  type FormalReportVersionTarget,
} from "./FormalReportDetailDialog";
import { IndexVersionDetailDialog } from "./IndexVersionDetailDialog";
import { TopbarPortal } from "./TopbarPortal";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { type Column, DataTable } from "./ui/DataTable";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Skeleton } from "./ui/Skeleton";
import { Tabs } from "./ui/Tabs";

type Workspace = "overview" | "reports" | "observations";
type ReportKind = FormalReportKind;
type VersionTarget = FormalReportVersionTarget;
type ReportRow = {
  key: string;
  kind: ReportKind;
  summary: EvaluationReportSummary | AnswerEvaluationSummary;
};

const WORKSPACES = [
  { value: "overview", label: "质量总览" },
  { value: "reports", label: "正式报告" },
  { value: "observations", label: "运行观测" },
];

function workspaceFromLocation(): Workspace {
  const value = new URLSearchParams(window.location.search).get("view");
  if (value === "reports" || value === "observations") return value;
  if (["#retrieval", "#answer", "#recent"].includes(window.location.hash)) return "reports";
  if (window.location.hash === "#pipeline") return "observations";
  return "overview";
}

function overviewReportFromLocation(): { kind: ReportKind; reportId: string } | null {
  const params = new URLSearchParams(window.location.search);
  if (params.get("view")) return null;
  const reportId = params.get("report");
  return reportId ? { kind: "answer", reportId } : null;
}

function evaluationUrl(view: Workspace, reportId?: string) {
  const search = new URLSearchParams();
  if (view !== "overview") search.set("view", view);
  if (reportId) search.set("report", reportId);
  return `/evaluation${search.size ? `?${search}` : ""}`;
}

function replaceEvaluationUrl(view: Workspace, reportId?: string) {
  window.history.replaceState({}, "", evaluationUrl(view, reportId));
}

function pushEvaluationUrl(view: Workspace, reportId?: string) {
  window.history.pushState({}, "", evaluationUrl(view, reportId));
}

function errorMessage(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

function formatTime(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
}

/**
 * 评测中心是横向质量治理层：这里负责质量结论、正式证据与运行观测，不承接问题处置和
 * 发布验收。Bad Case 与链路验收仍保留独立入口，避免把“看证据”和“做处置”混成一页。
 */
export function EvaluationCenterPage({ onOpen }: { onOpen: (path: string) => void }) {
  const [workspace, setWorkspace] = useState<Workspace>(() => workspaceFromLocation());
  const [overview, setOverview] = useState<EvaluationCenterOverview | null>(null);
  const [overviewError, setOverviewError] = useState("");
  const [overviewReport, setOverviewReport] = useState<{ kind: ReportKind; reportId: string } | null>(() => overviewReportFromLocation());
  const [overviewVersion, setOverviewVersion] = useState<VersionTarget | null>(null);

  useEffect(() => {
    const sync = () => {
      setWorkspace(workspaceFromLocation());
      setOverviewReport(overviewReportFromLocation());
    };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  useEffect(() => {
    if (!window.location.hash) return;
    replaceEvaluationUrl(workspace, new URLSearchParams(window.location.search).get("report") ?? undefined);
  }, [workspace]);

  useEffect(() => {
    let active = true;
    api.getEvaluationCenterOverview().then(
      (value) => {
        if (!active) return;
        setOverview(value);
        setOverviewError("");
      },
      (reason) => {
        if (!active) return;
        setOverview(null);
        setOverviewError(errorMessage(reason, "无法读取评测总览。"));
      },
    );
    return () => { active = false; };
  }, []);

  const changeWorkspace = (next: string) => {
    const value = next as Workspace;
    setWorkspace(value);
    pushEvaluationUrl(value);
  };

  return (
    <section className="px-6 pt-5 pb-8" aria-label="评测中心">
      <TopbarPortal>
        {overview && workspace !== "overview" ? <CenterStatusBadge status={overview.status} contextual /> : null}
      </TopbarPortal>
      <Tabs items={WORKSPACES} value={workspace} onChange={changeWorkspace} label="评测中心工作区">
        {workspace === "overview" ? (
          <QualityOverview
            overview={overview}
            error={overviewError}
            onOpenReport={setOverviewReport}
          />
        ) : null}
        {workspace === "reports" ? <OfficialReportsWorkspace onOpen={onOpen} /> : null}
        {workspace === "observations" ? <RuntimeObservationsWorkspace /> : null}
      </Tabs>
      {overviewReport ? (
          <FormalReportDetailDialog
          open
          kind={overviewReport.kind}
          reportId={overviewReport.reportId}
          onClose={() => {
            setOverviewReport(null);
            replaceEvaluationUrl("overview");
          }}
          onOpenVersion={setOverviewVersion}
        />
      ) : null}
      {overviewVersion ? (
        <IndexVersionDetailDialog
          open
          knowledgeBaseId={overviewVersion.knowledge_base_id}
          versionId={overviewVersion.index_version_id}
          onClose={() => setOverviewVersion(null)}
        />
      ) : null}
    </section>
  );
}

function CenterStatusBadge({ status, contextual = false }: { status: EvaluationCenterOverview["status"]; contextual?: boolean }) {
  const state = {
    passed: { tone: "success" as const, label: contextual ? "正式质量：通过" : "通过" },
    failed: { tone: "danger" as const, label: contextual ? "正式质量：未通过" : "未通过" },
    incomplete: { tone: "warning" as const, label: contextual ? "正式质量：证据不完整" : "证据不完整" },
  }[status];
  return <Badge tone={state.tone} shape="status">{state.label}</Badge>;
}

const METRIC_LABELS: Record<string, string> = {
  recall_at_5: "Recall@5",
  recall_at_10: "Recall@10",
  vector_mrr: "Vector MRR",
  rerank_mrr: "Rerank MRR",
  rerank_recall_at_5: "Rerank Recall@5",
  hybrid_mrr: "Hybrid MRR",
  ndcg_at_5: "NDCG@5",
  ndcg_at_10: "NDCG@10",
  metadata_filter_accuracy: "元数据过滤准确率",
  query_rewrite_success_rate: "Query Rewrite 成功率",
  query_rewrite_fallback_rate: "Query Rewrite 降级率",
  no_result_rate: "无结果率",
  acl_leak_count: "ACL 泄漏",
  answer_correctness: "答案正确性",
  completeness: "答案完整性",
  faithfulness: "忠实度",
  citation_validity: "引用有效性",
  citation_support: "引用支持度",
  claim_citation_coverage: "声明引用覆盖率",
  unsupported_claim_rate: "无支持声明率",
  contradiction_rate: "矛盾率",
  refusal_accuracy: "拒答准确率",
  source_conflict_accuracy: "来源冲突识别率",
  failure_strategy_stability: "失败策略稳定性",
};

type QualityRow = {
  scope: "retrieval" | "answer";
  label: string;
  report: EvaluationReportSummary | AnswerEvaluationSummary | null;
};

function QualityOverview({
  overview,
  error,
  onOpenReport,
}: {
  overview: EvaluationCenterOverview | null;
  error: string;
  onOpenReport: (target: { kind: ReportKind; reportId: string }) => void;
}) {
  if (error) return <ErrorBanner>{error}</ErrorBanner>;
  if (!overview) return <Skeleton className="h-[220px] rounded-lg" />;

  const rows: QualityRow[] = [
    { scope: "retrieval", label: "检索质量", report: overview.retrieval_report },
    { scope: "answer", label: "回答质量", report: overview.answer_report },
  ];
  const coverage = `${overview.available_scopes.length}/${overview.required_scopes.length}`;
  const evidenceSummary = overview.status === "incomplete"
    ? `正式证据仅覆盖 ${coverage} 个必需质量域；缺失证据不会按“通过”处理。`
    : overview.status === "failed"
      ? `正式证据已覆盖 ${coverage} 个必需质量域；缺失证据不会按“通过”处理。`
      : `正式证据已覆盖 ${coverage} 个必需质量域，当前没有质量阻塞。`;
  const overallLabel = overview.status === "failed"
    ? `存在 ${overview.failed_scopes.length} 个质量阻塞`
    : overview.status === "incomplete" ? "正式证据待补齐" : "正式质量通过";

  return (
    <div className="grid gap-4">
      <header>
        <p className="mt-1 mb-0 text-sm text-ink-muted">汇总检索与回答两个必需质量域的最新正式证据。</p>
      </header>
      <div className="grid gap-3 rounded-lg border border-line bg-surface p-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="m-0 text-lg font-bold text-ink">最新正式证据状态</h3>
            <Badge tone={overview.status === "passed" ? "success" : overview.status === "failed" ? "danger" : "warning"} shape="status">{overallLabel}</Badge>
          </div>
          <p className="mt-1 mb-0 text-sm text-ink-muted">{evidenceSummary}</p>
        </div>
        <span className="text-sm text-ink-faint">汇总于 {formatTime(overview.generated_at)}</span>
      </div>
      <div className="grid gap-3 md:grid-cols-2" aria-label="评测治理质量总览">
        {rows.map((row) => {
          const failedLabels = row.report?.failed_metrics?.map((key) => METRIC_LABELS[key] ?? key) ?? [];
          const reason = !row.report
            ? "尚无正式证据；该质量域按待补齐处理。"
            : row.report.passed
              ? "全部已记录质量指标达到冻结阈值。"
              : failedLabels.length
                ? `未达到冻结阈值：${failedLabels.join("、")}`
                : "存在未达到冻结阈值的质量指标。";
          return (
            <article key={row.scope} className="grid gap-3 rounded-lg border border-line bg-surface p-4">
              <div className="flex items-center justify-between gap-3">
                <h3 className="m-0 text-md font-semibold text-ink">{row.label}</h3>
                {row.report ? <Badge tone={row.report.passed ? "success" : "danger"} shape="status">{row.report.passed ? "通过" : "未通过"}</Badge> : <Badge tone="warning" shape="status">待补齐</Badge>}
              </div>
              <p className="m-0 text-sm text-ink-muted">{reason}</p>
              <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-ink-faint">
                <span><span>{row.report?.sample_count ? `${row.report.sample_count} ${row.scope === "retrieval" ? "条问题" : "个案例"}` : "覆盖量未记录"}</span> · 证据时间 {formatTime(row.report?.run_at)}</span>
                {row.report ? <Button variant="link" className="h-auto px-0 py-0" aria-label={`查看${row.label}正式证据`} onClick={() => onOpenReport({ kind: row.scope, reportId: row.report!.report_id })}>查看正式证据</Button> : null}
              </div>
            </article>
          );
        })}
      </div>
      <p className="m-0 text-sm text-ink-faint">
        工程运行状态不参与正式质量结论；需要排查同步或 RAG 链路时，请进入“运行观测”。
      </p>
    </div>
  );
}

function OfficialReportsWorkspace({ onOpen }: { onOpen: (path: string) => void }) {
  const [rows, setRows] = useState<ReportRow[] | null>(null);
  const [selected, setSelected] = useState<{ kind: ReportKind; reportId: string } | null>(null);
  const [listError, setListError] = useState("");
  const [retrievalDetail, setRetrievalDetail] = useState<EvaluationReport | null>(null);
  const [answerDetail, setAnswerDetail] = useState<AnswerEvaluationReport | null>(null);
  const [associations, setAssociations] = useState<EvaluationReportAssociations | null>(null);
  const [detailError, setDetailError] = useState("");
  const [associationError, setAssociationError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState<VersionTarget | null>(null);
  const prepareSelection = useCallback((target: { kind: ReportKind; reportId: string }) => {
    setDetailLoading(true);
    setDetailError("");
    setAssociationError("");
    setRetrievalDetail(null);
    setAnswerDetail(null);
    setAssociations(null);
    setSelected(target);
  }, []);

  useEffect(() => {
    let active = true;
    Promise.allSettled([api.listEvaluations(), api.listAnswerEvaluations()]).then((results) => {
      if (!active) return;
      const nextRows: ReportRow[] = [];
      const messages: string[] = [];
      const [retrieval, answer] = results;
      if (retrieval.status === "fulfilled") {
        nextRows.push(...retrieval.value.map((summary) => ({ key: `retrieval:${summary.report_id}`, kind: "retrieval" as const, summary })));
      } else {
        messages.push(errorMessage(retrieval.reason, "无法读取检索报告。"));
      }
      if (answer.status === "fulfilled") {
        nextRows.push(...answer.value.map((summary) => ({ key: `answer:${summary.report_id}`, kind: "answer" as const, summary })));
      } else {
        messages.push(errorMessage(answer.reason, "无法读取回答报告。"));
      }
      nextRows.sort((left, right) => new Date(right.summary.run_at).getTime() - new Date(left.summary.run_at).getTime());
      setRows(nextRows);
      setListError(messages.join(" "));
      const reportId = new URLSearchParams(window.location.search).get("report");
      if (reportId) {
        const target = nextRows.find((row) => row.summary.report_id === reportId);
        if (target) prepareSelection({ kind: target.kind, reportId: target.summary.report_id });
        else if (nextRows.length) setDetailError("链接中的正式报告不存在或当前账号不可访问。");
      }
    });
    return () => { active = false; };
  }, [prepareSelection]);

  useEffect(() => {
    const sync = () => {
      const reportId = new URLSearchParams(window.location.search).get("report");
      if (!reportId) {
        setSelected(null);
        setSelectedVersion(null);
        return;
      }
      const target = rows?.find((row) => row.summary.report_id === reportId);
      if (target) prepareSelection({ kind: target.kind, reportId: target.summary.report_id });
      else {
        setSelected(null);
        setDetailError("链接中的正式报告不存在或当前账号不可访问。");
      }
    };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, [prepareSelection, rows]);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    if (selected.kind === "retrieval") {
      api.getEvaluation(selected.reportId).then(
        (value) => active && setRetrievalDetail(value),
        (reason) => active && setDetailError(errorMessage(reason, "无法读取检索报告详情。")),
      ).finally(() => active && setDetailLoading(false));
      api.getEvaluationAssociations(selected.reportId).then(
        (value) => active && setAssociations(value),
        (reason) => active && setAssociationError(errorMessage(reason, "无法读取索引关联证据。")),
      );
    } else {
      api.getAnswerEvaluation(selected.reportId).then(
        (value) => active && setAnswerDetail(value),
        (reason) => active && setDetailError(errorMessage(reason, "无法读取回答报告详情。")),
      ).finally(() => active && setDetailLoading(false));
    }
    return () => { active = false; };
  }, [selected]);

  useEffect(() => {
    if (!selected || selectedVersion) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelected(null);
        replaceEvaluationUrl("reports");
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [selected, selectedVersion]);

  const chooseReport = (row: ReportRow) => {
    prepareSelection({ kind: row.kind, reportId: row.summary.report_id });
    pushEvaluationUrl("reports", row.summary.report_id);
  };
  const closeDetail = () => {
    setSelected(null);
    replaceEvaluationUrl("reports");
  };

  const columns: Column<ReportRow>[] = [
    {
      key: "report",
      header: "报告 ID",
      width: "24%",
      truncate: false,
      render: (row) => <Button variant="link" className="h-auto max-w-full justify-start truncate px-0 py-0 text-left" title={row.summary.report_id} onClick={() => chooseReport(row)}>{row.summary.report_id}</Button>,
    },
    { key: "kind", header: "质量域", width: "9%", render: (row) => row.kind === "retrieval" ? "检索" : "回答" },
    { key: "dataset", header: "数据集 / 版本", width: "24%", tooltip: (row) => `${row.summary.dataset_id} · ${row.summary.dataset_version}`, render: (row) => <span className="grid gap-0.5"><span>{row.summary.dataset_id}</span><small className="text-ink-faint">v{row.summary.dataset_version}</small></span> },
    { key: "coverage", header: "覆盖", width: "11%", render: (row) => row.summary.sample_count ? `${row.summary.sample_count} ${row.kind === "retrieval" ? "条问题" : "个案例"}` : "未记录" },
    { key: "official", header: "证据属性", width: "9%", truncate: false, render: () => <Badge tone="brand" shape="type">正式</Badge> },
    { key: "run_at", header: "证据时间", width: "15%", render: (row) => formatTime(row.summary.run_at) },
    { key: "passed", header: "质量结论", width: "8%", truncate: false, render: (row) => <Badge tone={row.summary.passed ? "success" : "danger"} shape="status">{row.summary.passed ? "通过" : "未通过"}</Badge> },
  ];

  const detail = selected ? (
    <ReportDetailPanel
      selected={selected}
      retrieval={retrievalDetail}
      answer={answerDetail}
      associations={associations}
      loading={detailLoading}
      error={detailError}
      associationError={associationError}
      onClose={closeDetail}
      onOpenVersion={setSelectedVersion}
    />
  ) : null;
  return (
    <div className="grid gap-4">
      <header>
        <p className="mt-1 mb-0 text-sm text-ink-muted">{rows ? `当前展示 ${rows.length} 份正式报告；选择报告查看指标证据及其治理关联。` : "正在读取正式报告…"}</p>
      </header>
      {listError ? <ErrorBanner>{listError}</ErrorBanner> : null}
      <div className="relative grid gap-4 min-[1440px]:grid-cols-[minmax(720px,1fr)_360px]">
        <DataTable
          label="正式评测报告"
          rows={rows}
          rowKey={(row) => row.key}
          columns={columns}
          emptyState={{ kind: "empty", title: "还没有正式报告。", description: "正式报告由受控评测任务生成，本页不会自动触发运行。" }}
        />
        {detail}
        {!selected ? <aside className="hidden rounded-lg border border-dashed border-line p-5 text-sm text-ink-muted min-[1440px]:block">选择一份报告，查看指标及其与索引治理的关联。</aside> : null}
      </div>
      {selectedVersion ? <IndexVersionDetailDialog
        open
        knowledgeBaseId={selectedVersion.knowledge_base_id}
        versionId={selectedVersion.index_version_id}
        onClose={() => setSelectedVersion(null)}
        onOpen={(path) => {
          setSelectedVersion(null);
          onOpen(path);
        }}
      /> : null}
    </div>
  );
}

function ReportDetailPanel({
  selected,
  retrieval,
  answer,
  associations,
  loading,
  error,
  associationError,
  onClose,
  onOpenVersion,
}: {
  selected: { kind: ReportKind; reportId: string };
  retrieval: EvaluationReport | null;
  answer: AnswerEvaluationReport | null;
  associations: EvaluationReportAssociations | null;
  loading: boolean;
  error: string;
  associationError: string;
  onClose: () => void;
  onOpenVersion: (version: VersionTarget) => void;
}) {
  return (
    <>
      <Button variant="ghost" aria-label="关闭报告详情" onClick={onClose} className="fixed inset-0 z-30 h-auto w-auto rounded-none bg-ink/35 p-0 hover:bg-ink/35 min-[1440px]:hidden">
        <span className="sr-only">关闭报告详情</span>
      </Button>
      <aside
        role="dialog"
        aria-label="正式报告详情"
        className="fixed inset-y-0 right-0 z-40 w-[min(400px,100vw)] overflow-y-auto border-l border-line bg-surface p-4 shadow-xl max-[600px]:w-full min-[1440px]:sticky min-[1440px]:top-4 min-[1440px]:z-auto min-[1440px]:h-fit min-[1440px]:max-h-[calc(100vh-110px)] min-[1440px]:w-auto min-[1440px]:rounded-lg min-[1440px]:border min-[1440px]:shadow-none"
      >
        <header className="mb-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <Badge tone="brand" shape="type">{selected.kind === "retrieval" ? "检索报告" : "回答报告"}</Badge>
            <h3 className="mt-2 mb-0 truncate text-md font-bold text-ink" title={selected.reportId}>{selected.reportId}</h3>
          </div>
          <Button variant="ghost" size="sm" className="min-[1440px]:hidden" onClick={onClose}>关闭</Button>
        </header>
        <FormalReportDetailContent
          kind={selected.kind}
          retrieval={retrieval}
          answer={answer}
          associations={associations}
          loading={loading}
          error={error}
          associationError={associationError}
          onOpenVersion={onOpenVersion}
          showKindBadge={false}
        />
      </aside>
    </>
  );
}

function RuntimeObservationsWorkspace() {
  const [summary, setSummary] = useState<PipelineEvaluation | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    api.getPipelineEvaluation().then(
      (value) => active && setSummary(value),
      (reason) => active && setError(errorMessage(reason, "无法读取运行观测数据。")),
    );
    return () => { active = false; };
  }, []);

  return (
    <div className="grid gap-6">
      <header>
        <p className="mt-1 mb-0 text-sm text-ink-muted">用于定位性能、稳定性和成本问题，不参与正式质量门结论。</p>
      </header>
      {error ? <ErrorBanner>{error}</ErrorBanner> : null}
      {!summary && !error ? <Skeleton className="h-[420px] rounded-lg" /> : null}
      {summary ? <>
        <section className="grid gap-4" aria-label="Data Sync">
          <div>
            <h2 className="m-0 text-md font-semibold text-ink">Data Sync</h2>
            <p className="mt-1 mb-0 text-sm text-ink-muted">统计当前可访问知识库最近 1,000 个同步批次。</p>
          </div>
          <DataSyncObservations summary={summary} />
        </section>
        <section className="grid gap-4 border-t border-divider pt-5" aria-label="RAG Runtime">
          <div>
            <h2 className="m-0 text-md font-semibold text-ink">RAG Runtime</h2>
            <p className="mt-1 mb-0 text-sm text-ink-muted">统计当前可访问知识库的全部历史在线执行记录。</p>
          </div>
          <RagRuntimeObservations summary={summary} />
        </section>
      </> : null}
    </div>
  );
}

function DataSyncObservations({ summary }: { summary: PipelineEvaluation }) {
  if (summary.run_count === 0) {
    return (
      <div className="rounded-lg border border-dashed border-line bg-surface px-4 py-8 text-center">
        <strong className="text-md text-ink">暂无 Data Sync 观测数据</strong>
        <p className="mt-1 mb-0 text-sm text-ink-muted">产生同步批次后，这里会展示批次结果、资源处理数量与耗时。</p>
        <div className="text-md text-ink">（建设中...）</div>
      </div>
    );
  }
  const results = [
    { label: "新增", value: summary.added_count },
    { label: "更新", value: summary.updated_count },
    { label: "删除", value: summary.deleted_count },
    { label: "跳过", value: summary.skipped_count },
    { label: "失败", value: summary.failed_count, danger: summary.failed_count > 0 },
    { label: "重试", value: summary.retry_count },
  ];
  return (
    <div className="grid gap-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <ObservationCard label="同步批次" value={String(summary.run_count)} />
        <ObservationCard label="平均耗时" value={`${(summary.average_duration_ms / 1000).toFixed(1)} 秒`} />
        <ObservationCard label="批次失败率" value={`${(summary.failure_rate * 100).toFixed(1)}%`} danger={summary.failure_rate > 0} />
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6" aria-label="Data Sync 同步结果">
        {results.map((item) => <ObservationCard key={item.label} label={item.label} value={String(item.value)} danger={item.danger} compact />)}
      </div>
    </div>
  );
}

type RagProfile = PipelineEvaluation["rag_profiles"][number];

function RagRuntimeObservations({ summary }: { summary: PipelineEvaluation }) {
  const columns: Column<RagProfile>[] = [
    { key: "profile", header: "意图 / Profile", width: "28%", truncate: false, render: (row) => <span className="grid gap-0.5"><strong className="font-medium">{row.intent ?? "受控返回"}</strong><small className="text-ink-faint">{row.pipeline_profile ?? "—"}@{row.profile_version ?? "—"}</small></span> },
    { key: "executions", header: "执行", width: "12%", numeric: true, render: (row) => row.execution_count },
    { key: "success", header: "任务成功率", width: "16%", numeric: true, render: (row) => `${(row.task_success_rate * 100).toFixed(1)}%` },
    { key: "evidence", header: "证据不足率", width: "16%", numeric: true, render: (row) => `${(row.insufficient_evidence_rate * 100).toFixed(1)}%` },
    { key: "fallback", header: "降级率", width: "14%", numeric: true, render: (row) => `${(row.fallback_rate * 100).toFixed(1)}%` },
    { key: "latency", header: "P95", width: "14%", numeric: true, render: (row) => `${(row.p95_latency_ms / 1000).toFixed(2)} s` },
  ];
  return <DataTable label="RAG Runtime 分管线观测" rows={summary.rag_profiles} rowKey={(row) => `${row.intent}-${row.pipeline_profile}-${row.profile_version}`} columns={columns} emptyState={{ kind: "empty", title: "尚无 RAG Runtime 记录。", description: "在线执行后会按意图和 Profile 汇总运行指标。" }} />;
}

function ObservationCard({ label, value, danger = false, compact = false }: { label: string; value: string; danger?: boolean; compact?: boolean }) {
  return (
    <div className={`rounded-lg border border-line bg-surface ${compact ? "px-3 py-2.5" : "p-4"}`}>
      <span className="text-sm text-ink-faint">{label}</span>
      <strong className={`${compact ? "mt-0.5 text-lg" : "mt-1 text-xl"} block tabular-nums ${danger ? "text-danger-text" : "text-ink"}`}>{value}</strong>
    </div>
  );
}
