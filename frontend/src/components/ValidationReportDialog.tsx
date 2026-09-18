import type { ValidationCheck, ValidationLayerResult, ValidationReport } from "../types";
import { Badge } from "./ui/Badge";
import { Dialog } from "./ui/Dialog";
import { ErrorBanner } from "./ui/ErrorBanner";

const REPORT_STATUS: Record<string, string> = {
  pending: "未开始",
  running: "检查中",
  pass: "已通过",
  failed: "未通过",
  cancelled: "已取消",
};

const LAYER_STATUS: Record<string, string> = {
  pass: "已通过",
  fail: "未通过",
  unknown: "无法核对",
};

const REPORT_SOURCE: Record<string, string> = {
  standard: "正式验证",
  legacy_backfill: "历史回填",
  bootstrap: "首次索引",
};

const LAYERS = [
  { key: "integrity_result", label: "完整性检查" },
  { key: "technical_result", label: "技术检查" },
  { key: "retrieval_result", label: "检索质量检查" },
] as const;

function valueLabel(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function reportTone(status: ValidationReport["status"]) {
  if (status === "failed") return "danger" as const;
  if (status === "pending" || status === "cancelled") return "neutral" as const;
  if (status === "running") return "brand" as const;
  return "success" as const;
}

function layerTone(status: ValidationLayerResult["status"] | "unknown") {
  if (status === "fail") return "danger" as const;
  if (status === "pass") return "success" as const;
  return "neutral" as const;
}

function layerResult(report: ValidationReport, key: (typeof LAYERS)[number]["key"]) {
  const value = report[key];
  return "status" in value ? value as ValidationLayerResult : null;
}

function CheckList({ checks }: { checks: ValidationCheck[] }) {
  if (!checks.length) return <p className="m-0 text-sm text-ink-faint">没有单项检查明细。</p>;
  return (
    <ul className="m-0 grid gap-2 p-0">
      {checks.map((check, index) => (
        <li key={`${check.layer || "check"}:${check.check_key}:${index}`} className="grid list-none gap-1 rounded-md border border-divider p-2 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <strong className="break-all font-medium text-ink">{check.check_key}</strong>
            <Badge shape="status" tone={check.status === "pass" ? "success" : "danger"}>
              {check.status === "pass" ? "已通过" : "未通过"}
            </Badge>
          </div>
          <span className="break-all text-ink-faint">
            期望 {valueLabel(check.expected)} · 实际 {valueLabel(check.actual)} · 严重级别 {check.severity || "—"}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function ValidationReportDialog({
  open,
  reportId,
  report,
  loading,
  error,
  onClose,
}: {
  open: boolean;
  reportId: string;
  report: ValidationReport | null;
  loading: boolean;
  error: string;
  onClose: () => void;
}) {
  return (
    <Dialog open={open} size="lg" title="验证报告" description={reportId} onClose={onClose}>
      {loading ? (
        <p role="status" className="m-0 text-sm text-ink-faint">正在读取验证报告…</p>
      ) : error ? (
        <ErrorBanner>{error}</ErrorBanner>
      ) : report ? (
        <div className="grid gap-4">
          <section className="grid gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge shape="status" tone={reportTone(report.status)}>
                {REPORT_STATUS[report.status] || report.status}
              </Badge>
              <span className="text-sm text-ink-faint">
                {REPORT_SOURCE[report.report_source] || report.report_source}
              </span>
            </div>
            {report.summary ? <p className="m-0 text-sm leading-6 text-ink-muted">{report.summary}</p> : null}
            <dl className="grid grid-cols-3 gap-3 text-sm max-md:grid-cols-2 max-sm:grid-cols-1">
              <div><dt className="text-ink-faint">报告 ID</dt><dd className="m-0 mt-1 break-all font-mono text-xs">{report.validation_report_id}</dd></div>
              <div><dt className="text-ink-faint">策略版本</dt><dd className="m-0 mt-1">{report.policy_version || "—"}</dd></div>
              <div><dt className="text-ink-faint">评测集版本</dt><dd className="m-0 mt-1">{report.evaluation_set_version || "—"}</dd></div>
              <div><dt className="text-ink-faint">索引构建</dt><dd className="m-0 mt-1 break-all">{report.index_build_id || "—"}</dd></div>
              <div><dt className="text-ink-faint">基线版本</dt><dd className="m-0 mt-1 break-all">{report.baseline_version_id || "—"}</dd></div>
              <div><dt className="text-ink-faint">创建时间</dt><dd className="m-0 mt-1">{new Date(report.created_at).toLocaleString("zh-CN")}</dd></div>
              <div><dt className="text-ink-faint">开始时间</dt><dd className="m-0 mt-1">{report.started_at ? new Date(report.started_at).toLocaleString("zh-CN") : "—"}</dd></div>
              <div><dt className="text-ink-faint">完成时间</dt><dd className="m-0 mt-1">{report.finished_at ? new Date(report.finished_at).toLocaleString("zh-CN") : "—"}</dd></div>
            </dl>
          </section>

          <section className="grid gap-3 border-t border-divider pt-4">
            <h3 className="m-0 text-md font-semibold text-ink">三层检查</h3>
            {LAYERS.map(({ key, label }) => {
              const layer = layerResult(report, key);
              const status = layer?.status || "unknown";
              return (
                <div key={key} className="grid gap-2 rounded-lg border border-divider p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h4 className="m-0 text-sm font-semibold text-ink">{label}</h4>
                    <Badge shape="status" tone={layerTone(status)}>{LAYER_STATUS[status] || status}</Badge>
                  </div>
                  {layer?.note ? <p className="m-0 text-sm text-ink-faint">{layer.note}</p> : null}
                  <CheckList checks={layer?.checks || []} />
                </div>
              );
            })}
          </section>

          <section className="grid gap-2 border-t border-divider pt-4">
            <h3 className="m-0 text-md font-semibold text-ink">失败项</h3>
            {report.failure_items.length ? (
              <CheckList checks={report.failure_items} />
            ) : (
              <p className="m-0 text-sm text-ink-faint">无失败项。</p>
            )}
          </section>
        </div>
      ) : (
        <ErrorBanner>未找到验证报告 {reportId}。</ErrorBanner>
      )}
    </Dialog>
  );
}
