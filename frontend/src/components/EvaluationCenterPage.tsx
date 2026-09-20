import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type {
  AnswerEvaluationReport,
  AnswerEvaluationSummary,
  EvaluationAssociationVersion,
  EvaluationCenterOverview,
  EvaluationMetric,
  EvaluationReport,
  EvaluationReportAssociations,
  EvaluationReportSummary,
  PipelineEvaluation,
} from "../types";
import { TopbarPortal } from "./TopbarPortal";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { type Column, DataTable } from "./ui/DataTable";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Skeleton } from "./ui/Skeleton";
import { Tabs } from "./ui/Tabs";

type Workspace = "overview" | "reports" | "observations";
type ReportKind = "retrieval" | "answer";
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

function shortFingerprint(value: string | null | undefined) {
  return value ? `${value.slice(0, 10)}…${value.slice(-6)}` : "—";
}

/**
 * 评测中心是横向质量治理层：这里负责质量结论、正式证据与运行观测，不承接问题处置和
 * 发布验收。Bad Case 与链路验收仍保留独立入口，避免把“看证据”和“做处置”混成一页。
 */
export function EvaluationCenterPage({ onOpen }: { onOpen: (path: string) => void }) {
  const [workspace, setWorkspace] = useState<Workspace>(() => workspaceFromLocation());
  const [overviewStatus, setOverviewStatus] = useState<EvaluationCenterOverview["status"] | null>(null);

  useEffect(() => {
    const sync = () => setWorkspace(workspaceFromLocation());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  useEffect(() => {
    if (!window.location.hash) return;
    replaceEvaluationUrl(workspace, new URLSearchParams(window.location.search).get("report") ?? undefined);
  }, [workspace]);

  const changeWorkspace = (next: string) => {
    const value = next as Workspace;
    setWorkspace(value);
    pushEvaluationUrl(value);
  };

  return (
    <section className="px-6 pt-5 pb-8" aria-label="评测中心">
      <TopbarPortal>
        {overviewStatus ? <CenterStatusBadge status={overviewStatus} /> : null}
      </TopbarPortal>
      <header className="mb-4">
        <h1 className="m-0 text-lg font-bold text-ink">评测中心</h1>
        <p className="mt-1 mb-0 text-sm text-ink-muted">
          横向治理检索与回答质量；正式证据与索引版本关联，运行数据只用于观测。
        </p>
      </header>
      <Tabs items={WORKSPACES} value={workspace} onChange={changeWorkspace} label="评测中心工作区">
        {workspace === "overview" ? (
          <QualityOverview
            onStatus={setOverviewStatus}
            onOpenReport={(reportId) => {
              setWorkspace("reports");
              pushEvaluationUrl("reports", reportId);
            }}
          />
        ) : null}
        {workspace === "reports" ? <OfficialReportsWorkspace onOpen={onOpen} /> : null}
        {workspace === "observations" ? <RuntimeObservationsWorkspace /> : null}
      </Tabs>
    </section>
  );
}

function CenterStatusBadge({ status }: { status: EvaluationCenterOverview["status"] }) {
  const state = {
    passed: { tone: "success" as const, label: "正式证据完整" },
    failed: { tone: "danger" as const, label: "存在质量阻塞" },
    incomplete: { tone: "warning" as const, label: "正式证据待补齐" },
  }[status];
  return <Badge tone={state.tone} shape="status">{state.label}</Badge>;
}

type QualityRow = {
  scope: "retrieval" | "answer";
  label: string;
  report: EvaluationReportSummary | AnswerEvaluationSummary | null;
};

function QualityOverview({
  onStatus,
  onOpenReport,
}: {
  onStatus: (status: EvaluationCenterOverview["status"]) => void;
  onOpenReport: (reportId: string) => void;
}) {
  const [overview, setOverview] = useState<EvaluationCenterOverview | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    api.getEvaluationCenterOverview().then(
      (value) => {
        if (!active) return;
        setOverview(value);
        onStatus(value.status);
      },
      (reason) => active && setError(errorMessage(reason, "无法读取评测总览。")),
    );
    return () => { active = false; };
  }, [onStatus]);

  const columns: Column<QualityRow>[] = [
    { key: "scope", header: "质量域", width: "20%", render: (row) => row.label },
    {
      key: "evidence",
      header: "最新正式证据",
      width: "44%",
      truncate: false,
      render: (row) => row.report ? (
        <span className="grid justify-items-start gap-0.5">
          <Button variant="link" className="h-auto justify-start px-0 py-0 text-left" onClick={() => onOpenReport(row.report!.report_id)}>
            {row.report.report_id}
          </Button>
          <small className="text-ink-faint">{row.report.dataset_id} · {row.report.dataset_version}</small>
        </span>
      ) : <span className="text-ink-faint">无正式报告</span>,
    },
    { key: "time", header: "证据时间", width: "22%", render: (row) => formatTime(row.report?.run_at) },
    {
      key: "status",
      header: "结论",
      width: "14%",
      truncate: false,
      render: (row) => row.report ? (
        <Badge tone={row.report.passed ? "success" : "danger"} shape="status">
          {row.report.passed ? "通过" : "未通过"}
        </Badge>
      ) : <Badge tone="warning" shape="status">待补齐</Badge>,
    },
  ];

  if (error) return <ErrorBanner>{error}</ErrorBanner>;
  if (!overview) return <Skeleton className="h-[220px] rounded-lg" />;

  const rows: QualityRow[] = [
    { scope: "retrieval", label: "检索质量", report: overview.retrieval_report },
    { scope: "answer", label: "回答质量", report: overview.answer_report },
  ];
  const coverage = `${overview.available_scopes.length}/${overview.required_scopes.length}`;
  const headline = overview.status === "passed"
    ? "正式证据完整，当前质量门通过"
    : overview.status === "failed"
      ? "正式证据存在未通过项"
      : "正式证据不完整，不能判定整体通过";

  return (
    <div className="grid gap-4">
      <header>
        <h2 className="m-0 text-lg font-bold text-ink">质量总览</h2>
        <p className="mt-1 mb-0 text-sm text-ink-muted">汇总检索与回答两个必需质量域的最新正式证据。</p>
      </header>
      <div className="grid gap-3 rounded-lg border border-line bg-surface p-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
        <div>
          <div className="mb-2"><CenterStatusBadge status={overview.status} /></div>
          <h3 className="m-0 text-lg font-bold text-ink">{headline}</h3>
          <p className="mt-1 mb-0 text-sm text-ink-muted">
            证据覆盖 {coverage} · 共 {overview.report_count} 份正式报告。缺失证据不会按“通过”处理。
          </p>
        </div>
        <span className="text-sm text-ink-faint">结论生成于 {formatTime(overview.generated_at)}</span>
      </div>
      <DataTable
        label="评测治理质量总览"
        rows={rows}
        rowKey={(row) => row.scope}
        columns={columns}
        emptyState={{ kind: "empty", title: "没有质量结论。", description: "质量域配置异常，请检查评测中心接口。" }}
      />
      <p className="m-0 text-sm text-ink-faint">
        工程运行状态不参与正式质量结论；需要排查同步或 RAG 链路时，请进入“运行观测”。
      </p>
    </div>
  );
}

function OfficialReportsWorkspace({ onOpen }: { onOpen: (path: string) => void }) {
  const [initialReportId] = useState(() => new URLSearchParams(window.location.search).get("report"));
  const [rows, setRows] = useState<ReportRow[] | null>(null);
  const [selected, setSelected] = useState<{ kind: ReportKind; reportId: string } | null>(null);
  const [listError, setListError] = useState("");
  const [retrievalDetail, setRetrievalDetail] = useState<EvaluationReport | null>(null);
  const [answerDetail, setAnswerDetail] = useState<AnswerEvaluationReport | null>(null);
  const [associations, setAssociations] = useState<EvaluationReportAssociations | null>(null);
  const [detailError, setDetailError] = useState("");
  const [associationError, setAssociationError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
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
      if (initialReportId) {
        const target = nextRows.find((row) => row.summary.report_id === initialReportId);
        if (target) prepareSelection({ kind: target.kind, reportId: target.summary.report_id });
        else if (nextRows.length) setDetailError("链接中的正式报告不存在或当前账号不可访问。");
      }
    });
    return () => { active = false; };
  }, [initialReportId, prepareSelection]);

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
    if (!selected) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelected(null);
        replaceEvaluationUrl("reports");
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [selected]);

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
      header: "正式报告",
      width: "34%",
      truncate: false,
      render: (row) => (
        <span className="grid justify-items-start gap-0.5">
          <Button variant="link" className="h-auto justify-start px-0 py-0 text-left" onClick={() => chooseReport(row)}>
            {row.summary.report_id}
          </Button>
          <small className="text-ink-faint">{row.summary.dataset_id} · {row.summary.dataset_version}</small>
        </span>
      ),
    },
    { key: "kind", header: "质量域", width: "14%", render: (row) => row.kind === "retrieval" ? "检索" : "回答" },
    { key: "run_at", header: "证据时间", width: "26%", render: (row) => formatTime(row.summary.run_at) },
    { key: "official", header: "证据属性", width: "13%", truncate: false, render: () => <Badge tone="brand" shape="type">正式</Badge> },
    { key: "passed", header: "阈值结论", width: "13%", truncate: false, render: (row) => <Badge tone={row.summary.passed ? "success" : "danger"} shape="status">{row.summary.passed ? "通过" : "未通过"}</Badge> },
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
      onOpen={onOpen}
    />
  ) : null;

  return (
    <div className="grid gap-4">
      <header>
        <h2 className="m-0 text-lg font-bold text-ink">正式报告</h2>
        <p className="mt-1 mb-0 text-sm text-ink-muted">只展示受控运行形成的正式证据；“正式”与“通过”是两个独立事实。</p>
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
        {!selected ? <aside className="hidden rounded-lg border border-dashed border-line p-5 text-sm text-ink-muted min-[1440px]:block">选择一份报告，查看指标、证据属性及其与索引治理的关联。</aside> : null}
      </div>
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
  onOpen,
}: {
  selected: { kind: ReportKind; reportId: string };
  retrieval: EvaluationReport | null;
  answer: AnswerEvaluationReport | null;
  associations: EvaluationReportAssociations | null;
  loading: boolean;
  error: string;
  associationError: string;
  onClose: () => void;
  onOpen: (path: string) => void;
}) {
  const report = selected.kind === "retrieval" ? retrieval : answer;
  const metricRows = useMemo(() => {
    if (retrieval) return retrievalMetricRows(retrieval);
    if (answer) return Object.entries(answer.metrics)
      .filter((entry): entry is [string, NonNullable<typeof entry[1]>] => entry[1] !== null)
      .map(([key, metric]) => ({ key, label: ANSWER_METRIC_LABELS[key] ?? key, metric }));
    return [];
  }, [answer, retrieval]);

  return (
    <>
      <Button variant="ghost" aria-label="关闭报告详情" onClick={onClose} className="fixed inset-0 z-30 h-auto w-auto rounded-none bg-ink/35 p-0 min-[1440px]:hidden">
        <span className="sr-only">关闭报告详情</span>
      </Button>
      <aside
        role="dialog"
        aria-modal="true"
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
        {error ? <ErrorBanner>{error}</ErrorBanner> : null}
        {loading ? <Skeleton className="h-[320px] rounded-lg" /> : null}
        {report ? (
          <div className="grid gap-5">
            <dl className="m-0 grid grid-cols-[92px_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
              <dt className="text-ink-faint">证据属性</dt><dd className="m-0"><Badge tone="brand" shape="type">正式</Badge></dd>
              <dt className="text-ink-faint">阈值结论</dt><dd className="m-0"><Badge tone={report.passed ? "success" : "danger"} shape="status">{report.passed ? "通过" : "未通过"}</Badge></dd>
              <dt className="text-ink-faint">数据集</dt><dd className="m-0 break-all text-ink">{report.dataset_id} · {report.dataset_version}</dd>
              <dt className="text-ink-faint">运行时间</dt><dd className="m-0 text-ink">{formatTime(report.run_at)}</dd>
              <dt className="text-ink-faint">Commit</dt><dd className="m-0 break-all font-mono text-xs text-ink">{report.commit}</dd>
              {retrieval ? <><dt className="text-ink-faint">配置指纹</dt><dd className="m-0 font-mono text-xs text-ink" title={retrieval.config_fingerprint ?? undefined}>{shortFingerprint(retrieval.config_fingerprint)}</dd></> : null}
              {retrieval ? <><dt className="text-ink-faint">ACL 泄漏</dt><dd className="m-0 text-ink">{retrieval.acl_leak_count === null || retrieval.acl_leak_count === undefined ? "未记录" : <Badge tone={retrieval.acl_leak_count === 0 ? "success" : "danger"} shape="status">{retrieval.acl_leak_count}</Badge>}</dd></> : null}
            </dl>
            <section>
              <h4 className="mt-0 mb-2 text-sm font-semibold text-ink">指标证据</h4>
              <div className="grid gap-2">
                {metricRows.map((row) => (
                  <div key={row.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-md bg-canvas px-3 py-2 text-sm">
                    <span className="text-ink-muted">{row.label}</span>
                    <span className="text-right tabular-nums text-ink">{formatMetric(row.metric)} <Badge tone={row.metric.passed ? "success" : "danger"} shape="status">{row.metric.passed ? "通过" : "未通过"}</Badge></span>
                  </div>
                ))}
              </div>
            </section>
            {retrieval ? <AssociationEvidence report={retrieval} associations={associations} error={associationError} onOpen={onOpen} /> : <p className="m-0 rounded-md bg-canvas p-3 text-sm text-ink-muted">回答报告是横向质量证据，不参与索引版本放行。</p>}
          </div>
        ) : null}
      </aside>
    </>
  );
}

function AssociationEvidence({
  report,
  associations,
  error,
  onOpen,
}: {
  report: EvaluationReport;
  associations: EvaluationReportAssociations | null;
  error: string;
  onOpen: (path: string) => void;
}) {
  const openVersion = (version: EvaluationAssociationVersion) => onOpen(
    `/knowledge-bases/${encodeURIComponent(version.knowledge_base_id)}/index-versions/${encodeURIComponent(version.index_version_id)}`,
  );
  return (
    <section className="border-t border-divider pt-4">
      <h4 className="mt-0 mb-1 text-sm font-semibold text-ink">与索引治理的关联</h4>
      {!report.config_fingerprint ? <p className="m-0 rounded-md bg-warning/10 p-3 text-sm text-warning">历史报告 · 缺少配置指纹，不可用于当前版本放行。</p> : null}
      {error ? <ErrorBanner className="mt-2">{error}</ErrorBanner> : null}
      {report.config_fingerprint && !associations && !error ? <Skeleton className="mt-2 h-[150px] rounded-lg" /> : null}
      {report.config_fingerprint && associations ? (
        <div className="mt-3 grid gap-4 text-sm">
          <div>
            <span className="text-ink-faint">来源版本</span>
            {associations.origin_version ? (
              <div className="mt-1 grid gap-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Button variant="link" className="h-auto px-0 py-0" onClick={() => openVersion(associations.origin_version!)}>{versionLabel(associations.origin_version)}</Button>
                  <Badge tone="neutral" shape="status">{associations.origin_version.status}</Badge>
                </div>
                {associations.origin_evaluation_run_id ? <small className="break-all text-ink-faint">运行记录 {associations.origin_evaluation_run_id}</small> : null}
              </div>
            ) : <p className="mt-1 mb-0 text-ink-muted">来源版本不可追溯；该报告可能来自历史离线基线。</p>}
          </div>
          <div>
            <span className="text-ink-faint">配置兼容版本 · {associations.compatible_versions.length}</span>
            {associations.compatible_versions.length ? (
              <div className="mt-1 grid gap-1.5">
                {associations.compatible_versions.slice(0, 5).map((version) => <Button key={version.index_version_id} variant="link" className="h-auto justify-start px-0 py-0 text-left" onClick={() => openVersion(version)}>{versionLabel(version)} · {version.status}</Button>)}
                {associations.compatible_versions.length > 5 ? <span className="text-ink-faint">另有 {associations.compatible_versions.length - 5} 个兼容版本</span> : null}
              </div>
            ) : <p className="mt-1 mb-0 text-ink-muted">当前没有配置指纹匹配的可见索引版本。</p>}
          </div>
          <div>
            <span className="text-ink-faint">三层验证使用记录 · {associations.validation_usages.length}</span>
            {associations.validation_usages.length ? (
              <div className="mt-1 grid gap-1.5">
                {associations.validation_usages.slice(0, 5).map((usage) => (
                  <div key={usage.validation_report_id} className="flex items-center justify-between gap-2">
                    <Button variant="link" className="h-auto min-w-0 justify-start truncate px-0 py-0 text-left" onClick={() => onOpen(`/knowledge-bases/${encodeURIComponent(usage.knowledge_base_id)}/index-versions/${encodeURIComponent(usage.index_version_id)}`)}>{usage.validation_report_id}</Button>
                    <Badge tone={usage.status === "pass" ? "success" : usage.status === "failed" ? "danger" : "neutral"} shape="status">{usage.status}</Badge>
                  </div>
                ))}
              </div>
            ) : <p className="mt-1 mb-0 text-ink-muted">尚未被三层验证引用。</p>}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function versionLabel(version: EvaluationAssociationVersion) {
  return `${version.version_no ? `v${version.version_no}` : "Legacy"} · ${version.index_version_id}`;
}

const RETRIEVAL_METRICS: Array<{ key: keyof EvaluationReport; label: string }> = [
  { key: "recall_at_5", label: "Recall@5" },
  { key: "recall_at_10", label: "Recall@10" },
  { key: "vector_mrr", label: "Vector MRR" },
  { key: "rerank_mrr", label: "Rerank MRR" },
  { key: "rerank_recall_at_5", label: "Rerank Recall@5" },
  { key: "hybrid_mrr", label: "Hybrid MRR" },
  { key: "ndcg_at_5", label: "NDCG@5" },
  { key: "ndcg_at_10", label: "NDCG@10" },
  { key: "metadata_filter_accuracy", label: "元数据过滤准确率" },
  { key: "query_rewrite_success_rate", label: "Query Rewrite 成功率" },
  { key: "query_rewrite_fallback_rate", label: "Query Rewrite 降级率" },
  { key: "no_result_rate", label: "无结果率" },
];

function retrievalMetricRows(report: EvaluationReport) {
  return RETRIEVAL_METRICS.flatMap(({ key, label }) => {
    const value = report[key];
    if (!value || typeof value !== "object" || !("value" in value)) return [];
    return [{ key: String(key), label, metric: value as EvaluationMetric }];
  });
}

const ANSWER_METRIC_LABELS: Record<string, string> = {
  faithfulness: "忠实度",
  answer_correctness: "答案正确性",
  citation_precision: "引用准确率",
  citation_coverage: "引用覆盖率",
  source_conflict_accuracy: "来源冲突识别率",
  unsupported_claim_rate: "无支持声明率",
};

function formatMetric(metric: EvaluationMetric) {
  const percent = Math.abs(metric.value) <= 1 && Math.abs(metric.threshold) <= 1;
  const value = percent ? `${(metric.value * 100).toFixed(1)}%` : metric.value.toFixed(3);
  const threshold = percent ? `${(metric.threshold * 100).toFixed(1)}%` : metric.threshold.toFixed(3);
  return `${value} / ${threshold}`;
}

function RuntimeObservationsWorkspace() {
  const [tab, setTab] = useState("sync");
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
    <div className="grid gap-4">
      <header>
        <h2 className="m-0 text-lg font-bold text-ink">运行观测</h2>
        <p className="mt-1 mb-0 text-sm text-ink-muted">用于定位性能、稳定性和成本问题，不参与正式质量门结论。</p>
      </header>
      {error ? <ErrorBanner>{error}</ErrorBanner> : null}
      <Tabs items={[{ value: "sync", label: "Data Sync" }, { value: "rag", label: "RAG Runtime" }]} value={tab} onChange={setTab} label="运行观测范围">
        {!summary && !error ? <Skeleton className="h-[240px] rounded-lg" /> : null}
        {summary && tab === "sync" ? <DataSyncObservations summary={summary} /> : null}
        {summary && tab === "rag" ? <RagRuntimeObservations summary={summary} /> : null}
      </Tabs>
    </div>
  );
}

type SyncMetricRow = { metric: string; count: number };

function DataSyncObservations({ summary }: { summary: PipelineEvaluation }) {
  const rows: SyncMetricRow[] = [
    { metric: "新增", count: summary.added_count },
    { metric: "更新", count: summary.updated_count },
    { metric: "删除", count: summary.deleted_count },
    { metric: "跳过", count: summary.skipped_count },
    { metric: "失败", count: summary.failed_count },
    { metric: "重试", count: summary.retry_count },
  ];
  const columns: Column<SyncMetricRow>[] = [
    { key: "metric", header: "同步结果", width: "70%", render: (row) => row.metric },
    { key: "count", header: "数量", width: "30%", numeric: true, render: (row) => row.count },
  ];
  return (
    <div className="grid gap-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <ObservationCard label="同步批次" value={String(summary.run_count)} />
        <ObservationCard label="平均耗时" value={`${(summary.average_duration_ms / 1000).toFixed(1)} 秒`} />
        <ObservationCard label="失败率" value={`${(summary.failure_rate * 100).toFixed(1)}%`} danger={summary.failure_rate > 0} />
      </div>
      <DataTable label="Data Sync 运行观测" rows={rows} rowKey={(row) => row.metric} columns={columns} emptyState={{ kind: "empty", title: "没有同步观测数据。", description: "同步运行后会在这里汇总工程指标。" }} />
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

function ObservationCard({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <span className="text-sm text-ink-faint">{label}</span>
      <strong className={`mt-1 block text-xl tabular-nums ${danger ? "text-danger-text" : "text-ink"}`}>{value}</strong>
    </div>
  );
}
