import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type {
  AnswerEvaluationReport,
  EvaluationAssociationVersion,
  EvaluationMetric,
  EvaluationReport,
  EvaluationReportAssociations,
} from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Skeleton } from "./ui/Skeleton";

export type FormalReportKind = "retrieval" | "answer";
export type FormalReportVersionTarget = Pick<EvaluationAssociationVersion, "knowledge_base_id" | "index_version_id">;

const INDEX_VERSION_STATUS: Record<string, string> = {
  building: "构建中",
  validating: "验证中",
  ready: "待激活",
  active: "当前生效",
  previous: "上一版本",
  retired: "已退役",
  cleaned: "已清理",
  build_failed: "构建失败",
  validation_failed: "验证未通过",
};

const VALIDATION_STATUS: Record<string, string> = {
  pending: "未开始",
  running: "检查中",
  pass: "已通过",
  failed: "未通过",
  cancelled: "已取消",
};

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

const ANSWER_METRIC_LABELS: Record<string, string> = {
  faithfulness: "忠实度",
  answer_correctness: "答案正确性",
  citation_precision: "引用准确率",
  citation_coverage: "引用覆盖率",
  source_conflict_accuracy: "来源冲突识别率",
  unsupported_claim_rate: "无支持声明率",
};

function formatTime(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
}

function shortFingerprint(value: string | null | undefined) {
  return value ? `${value.slice(0, 10)}…${value.slice(-6)}` : "—";
}

function formatMetric(metric: EvaluationMetric & { direction?: string }) {
  const percent = Math.abs(metric.value) <= 1 && Math.abs(metric.threshold) <= 1;
  const value = percent ? `${(metric.value * 100).toFixed(1)}%` : metric.value.toFixed(3);
  const threshold = percent ? `${(metric.threshold * 100).toFixed(1)}%` : metric.threshold.toFixed(3);
  return `实际值 ${value} · 门槛 ${metric.direction === "maximum" ? "≤" : "≥"} ${threshold}`;
}

function retrievalMetricRows(report: EvaluationReport) {
  return RETRIEVAL_METRICS.flatMap(({ key, label }) => {
    const value = report[key];
    if (!value || typeof value !== "object" || !("value" in value)) return [];
    return [{ key: String(key), label, metric: value as EvaluationMetric }];
  });
}

function versionLabel(version: EvaluationAssociationVersion) {
  return `${version.version_no ? `v${version.version_no}` : "Legacy"} · ${version.index_version_id}`;
}

export function FormalReportDetailContent({
  kind,
  retrieval,
  answer,
  associations,
  loading,
  error,
  associationError,
  onOpenVersion,
  showKindBadge = true,
}: {
  kind: FormalReportKind;
  retrieval: EvaluationReport | null;
  answer: AnswerEvaluationReport | null;
  associations: EvaluationReportAssociations | null;
  loading: boolean;
  error: string;
  associationError: string;
  onOpenVersion?: (version: FormalReportVersionTarget) => void;
  showKindBadge?: boolean;
}) {
  const report = kind === "retrieval" ? retrieval : answer;
  const metricRows = useMemo(() => {
    if (retrieval) return retrievalMetricRows(retrieval);
    if (answer) return Object.entries(answer.metrics)
      .filter((entry): entry is [string, NonNullable<typeof entry[1]>] => entry[1] !== null)
      .map(([key, metric]) => ({ key, label: ANSWER_METRIC_LABELS[key] ?? key, metric }));
    return [];
  }, [answer, retrieval]);

  if (error) return <ErrorBanner>{error}</ErrorBanner>;
  if (loading) return <Skeleton className="h-[320px] rounded-lg" />;
  if (!report) return null;

  const failedMetricCount = metricRows.filter((row) => !row.metric.passed).length;
  const datasetEvidence = retrieval?.dataset_evidence;
  const integrityPassed = datasetEvidence?.integrity_status === "passed";
  const conclusion = retrieval && !integrityPassed
    ? `报告记录了 ${retrieval.query_count} 条问题；未保存或无法复核数据集完整性，不按“数据完整”处理。`
    : report.passed
      ? "全部已记录质量指标达到冻结阈值。"
      : retrieval && integrityPassed
      ? `评测数据完整，但 ${failedMetricCount} 项质量指标未达到冻结阈值；失败不是由缺少数据导致。`
      : `${failedMetricCount} 项回答质量指标未达到冻结阈值。`;

  return (
    <div className="grid gap-5">
      {showKindBadge ? <div><Badge tone="brand" shape="type">{kind === "retrieval" ? "检索报告" : "回答报告"}</Badge></div> : null}
      <section className="grid gap-3 rounded-lg border border-line bg-canvas p-3">
        <div className="flex flex-wrap gap-2">
          <Badge tone="brand" shape="type">正式证据</Badge>
          {retrieval ? <Badge tone={integrityPassed ? "success" : "warning"} shape="status">{integrityPassed ? "数据完整" : "完整性未记录"}</Badge> : null}
          <Badge tone={report.passed ? "success" : "danger"} shape="status">{report.passed ? "质量通过" : "质量未通过"}</Badge>
        </div>
        <p className="m-0 text-sm leading-6 text-ink-muted">{conclusion}</p>
        <span className="text-xs text-ink-faint">证据时间 {formatTime(report.run_at)}</span>
      </section>
      {retrieval ? (
        <section>
          <h3 className="mt-0 mb-2 text-sm font-semibold text-ink">评测数据证据</h3>
          {integrityPassed ? (
            <div className="grid gap-2">
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-md border border-line bg-surface p-3"><strong className="block text-lg text-ink">{datasetEvidence.document_count}/{datasetEvidence.document_count} 份文档</strong></div>
                <div className="rounded-md border border-line bg-surface p-3"><strong className="block text-lg text-ink">{datasetEvidence.query_count}/{datasetEvidence.query_count} 条问题</strong></div>
              </div>
              <p className="m-0 text-xs leading-5 text-ink-muted">文件哈希、解析段落数与标注引用均通过。{datasetEvidence.integrity_basis === "report" ? "报告运行时完整性校验。" : "当前注册数据集校验（非历史运行时快照）。"}</p>
            </div>
          ) : (
            <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">文档数未记录 · {retrieval.query_count} 条问题。历史报告未保存完整性证据。</div>
          )}
        </section>
      ) : null}
      <section>
        <h3 className="mt-0 mb-2 text-sm font-semibold text-ink">指标证据</h3>
        <div className="grid gap-2">
          {metricRows.map((row) => (
            <div key={row.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-md bg-canvas px-3 py-2 text-sm max-sm:grid-cols-1">
              <span className="text-ink-muted">{row.label}</span>
              <span className="text-right tabular-nums text-ink max-sm:text-left">{formatMetric(row.metric)} <Badge tone={row.metric.passed ? "success" : "danger"} shape="status">{row.metric.passed ? "通过" : "未通过"}</Badge></span>
            </div>
          ))}
        </div>
      </section>
      {retrieval ? (
        !retrieval.config_fingerprint ? (
          <p className="m-0 rounded-md bg-warning/10 p-3 text-sm text-warning">历史报告 · 缺少配置指纹，不可用于当前版本放行。</p>
        ) : <AssociationEvidence associations={associations} error={associationError} onOpenVersion={onOpenVersion} />
      ) : (
        <p className="m-0 rounded-md bg-canvas p-3 text-sm text-ink-muted">回答报告是横向质量证据，不参与索引版本放行。</p>
      )}
      <section className="border-t border-divider pt-4">
        <h3 className="mt-0 mb-2 text-sm font-semibold text-ink">报告信息</h3>
        <dl className="m-0 grid grid-cols-[92px_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
          <dt className="text-ink-faint">数据集</dt><dd className="m-0 break-all text-ink">{report.dataset_id} · {report.dataset_version}</dd>
          <dt className="text-ink-faint">运行时间</dt><dd className="m-0 text-ink">{formatTime(report.run_at)}</dd>
          <dt className="text-ink-faint">Commit</dt><dd className="m-0 break-all font-mono text-xs text-ink">{report.commit}</dd>
          {retrieval ? <><dt className="text-ink-faint">配置指纹</dt><dd className="m-0 font-mono text-xs text-ink" title={retrieval.config_fingerprint ?? undefined}>{shortFingerprint(retrieval.config_fingerprint)}</dd></> : null}
          {retrieval ? <><dt className="text-ink-faint">ACL 泄漏</dt><dd className="m-0 text-ink">{retrieval.acl_leak_count === null || retrieval.acl_leak_count === undefined ? "未记录" : <Badge tone={retrieval.acl_leak_count === 0 ? "success" : "danger"} shape="status">{retrieval.acl_leak_count}</Badge>}</dd></> : null}
        </dl>
      </section>
    </div>
  );
}

function AssociationEvidence({
  associations,
  error,
  onOpenVersion,
}: {
  associations: EvaluationReportAssociations | null;
  error: string;
  onOpenVersion?: (version: FormalReportVersionTarget) => void;
}) {
  const versionNode = (version: EvaluationAssociationVersion, includeStatus = false) => {
    const label = `${versionLabel(version)}${includeStatus ? ` · ${INDEX_VERSION_STATUS[version.status] ?? version.status}` : ""}`;
    return onOpenVersion ? (
      <Button variant="link" className="h-auto justify-start px-0 py-0 text-left" onClick={() => onOpenVersion(version)}>{label}</Button>
    ) : <strong className="font-normal text-ink">{label}</strong>;
  };

  return (
    <section className="border-t border-divider pt-4">
      <h3 className="mt-0 mb-1 text-sm font-semibold text-ink">与索引治理的关联</h3>
      {error ? <ErrorBanner className="mt-2">{error}</ErrorBanner> : null}
      {!associations && !error ? <Skeleton className="mt-2 h-[150px] rounded-lg" /> : null}
      {associations ? (
        <div className="mt-3 grid gap-4 text-sm">
          <div>
            <span className="text-ink-faint">来源版本</span>
            {associations.origin_version ? (
              <div className="mt-1 flex flex-wrap items-center gap-2">
                {versionNode(associations.origin_version)}
                <Badge tone="neutral" shape="status">{INDEX_VERSION_STATUS[associations.origin_version.status] ?? associations.origin_version.status}</Badge>
              </div>
            ) : <p className="mt-1 mb-0 text-ink-muted">来源版本不可追溯；该报告可能来自历史离线基线。</p>}
          </div>
          <div>
            <span className="text-ink-faint">同配置版本 · {associations.compatible_versions.length}</span>
            {associations.compatible_versions.length ? <div className="mt-1 grid gap-1.5">{associations.compatible_versions.slice(0, 5).map((version) => <div key={version.index_version_id}>{versionNode(version, true)}</div>)}</div> : <p className="mt-1 mb-0 text-ink-muted">当前没有配置指纹匹配的可见索引版本。</p>}
          </div>
          <div>
            <span className="text-ink-faint">三层验证使用记录 · {associations.validation_usages.length}</span>
            {associations.validation_usages.length ? <div className="mt-1 grid gap-1.5">{associations.validation_usages.slice(0, 5).map((usage) => <div key={usage.validation_report_id} className="flex items-center justify-between gap-2"><span className="truncate font-mono text-xs">{usage.validation_report_id}</span><Badge tone={usage.status === "pass" ? "success" : usage.status === "failed" ? "danger" : "neutral"} shape="status">{VALIDATION_STATUS[usage.status] ?? usage.status}</Badge></div>)}</div> : <p className="mt-1 mb-0 text-ink-muted">尚未被三层验证引用。</p>}
          </div>
        </div>
      ) : null}
    </section>
  );
}

export function FormalReportDetailDialog({
  open,
  kind,
  reportId,
  onClose,
  onOpenVersion,
}: {
  open: boolean;
  kind: FormalReportKind;
  reportId: string;
  onClose: () => void;
  onOpenVersion?: (version: FormalReportVersionTarget) => void;
}) {
  const [retrieval, setRetrieval] = useState<EvaluationReport | null>(null);
  const [answer, setAnswer] = useState<AnswerEvaluationReport | null>(null);
  const [associations, setAssociations] = useState<EvaluationReportAssociations | null>(null);
  const [error, setError] = useState("");
  const [associationError, setAssociationError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!open) return;
    let active = true;
    void Promise.resolve().then(async () => {
      setRetrieval(null);
      setAnswer(null);
      setAssociations(null);
      setError("");
      setAssociationError("");
      setLoading(true);
      if (kind === "answer") {
        try {
          const value = await api.getAnswerEvaluation(reportId);
          if (active) setAnswer(value);
        } catch (reason) {
          if (active) setError(reason instanceof Error ? reason.message : "无法读取回答报告详情。");
        } finally {
          if (active) setLoading(false);
        }
        return;
      }
      const [detailResult, associationResult] = await Promise.allSettled([
        api.getEvaluation(reportId),
        api.getEvaluationAssociations(reportId),
      ]);
      if (!active) return;
      if (detailResult.status === "fulfilled") setRetrieval(detailResult.value);
      else setError(detailResult.reason instanceof Error ? detailResult.reason.message : "无法读取检索报告详情。");
      if (associationResult.status === "fulfilled") setAssociations(associationResult.value);
      else setAssociationError(associationResult.reason instanceof Error ? associationResult.reason.message : "无法读取索引关联证据。");
      setLoading(false);
    });
    return () => { active = false; };
  }, [kind, open, reportId]);

  return (
    <Dialog open={open} size="lg" title="正式报告详情" description={reportId} onClose={onClose}>
      <FormalReportDetailContent
        kind={kind}
        retrieval={retrieval}
        answer={answer}
        associations={associations}
        loading={loading}
        error={error}
        associationError={associationError}
        onOpenVersion={onOpenVersion}
      />
    </Dialog>
  );
}
