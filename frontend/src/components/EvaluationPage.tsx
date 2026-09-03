import { useEffect, useState } from "react";
import { api } from "../api";
import type { EvaluationMetric, EvaluationReport, EvaluationReportSummary } from "../types";
import { Badge } from "./ui/Badge";
import { EmptyState } from "./ui/EmptyState";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Select } from "./ui/Select";
import { Skeleton } from "./ui/Skeleton";

const METRIC_ROWS: Array<{
  key: "recall_at_5" | "vector_mrr" | "hybrid_mrr" | "rerank_mrr";
  label: string;
}> = [
  { key: "recall_at_5", label: "Recall@5" },
  { key: "vector_mrr", label: "向量 MRR" },
  { key: "hybrid_mrr", label: "混合召回 MRR" },
  { key: "rerank_mrr", label: "最终排序 MRR" },
];

/**
 * 指标卡：数值 + 阈值进度条 + 通过态。
 *
 * 没有直接用 `ui/MetricCard`——那个组件只有 icon/label/value/note 四个槽位，容不下这里
 * 必须有的阈值进度条、基线对比和通过/回退徽章。数值字阶（`text-xl` + `tabular-nums`）
 * 沿用它的规范，但卡片结构是本页专属的。
 */
function MetricCard({ label, metric }: { label: string; metric: EvaluationMetric }) {
  const change = metric.baseline === null ? null : metric.value - metric.baseline;
  return (
    <article className="rounded-[9px] border border-line bg-surface p-4 min-[1025px]:p-[15px]">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[14px] font-semibold text-[#6e768a]">{label}</span>
        <strong className="text-xl tabular-nums text-[#252d43]">{metric.value.toFixed(4)}</strong>
      </div>
      <div
        className="relative mt-[15px] mb-[9px] h-[5px] rounded-full bg-[#eceef4]"
        aria-label={`${label} ${metric.value.toFixed(4)}`}
      >
        <span className="block h-full rounded-[inherit] bg-[#6558d8]" style={{ width: `${metric.value * 100}%` }} />
        <span
          className="absolute -top-[3px] h-[11px] w-[2px] bg-[#f19a36]"
          style={{ left: `${metric.threshold * 100}%` }}
          title={`冻结阈值 ${metric.threshold}`}
        />
      </div>
      <div className="flex items-center gap-1.5 text-[11px] text-[#8c93a5]">
        <span>阈值 {metric.threshold.toFixed(2)}</span>
        <span>{change === null ? "首次基线" : `较基线 ${change >= 0 ? "+" : ""}${change.toFixed(4)}`}</span>
        <Badge className="ml-auto" tone={metric.passed ? "success" : "danger"} shape="status">
          {metric.regressed ? "发生回退" : metric.passed ? "通过" : "未通过"}
        </Badge>
      </div>
    </article>
  );
}

function MetricSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 min-[561px]:grid-cols-2 min-[821px]:grid-cols-3">
      <Skeleton className="h-[124px] rounded-[9px]" />
      <Skeleton className="h-[124px] rounded-[9px]" />
      <Skeleton className="h-[124px] rounded-[9px]" />
    </div>
  );
}

export function EvaluationPage() {
  const [reports, setReports] = useState<EvaluationReportSummary[] | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [report, setReport] = useState<EvaluationReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    api.listEvaluations().then(
      (items) => {
        if (!active) return;
        setReports(items);
        setSelectedId(items[0]?.report_id ?? "");
      },
      (reason: unknown) => active && setError(reason instanceof Error ? reason.message : "无法读取评测报告。"),
    );
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    let active = true;
    api.getEvaluation(selectedId).then(
      (detail) => active && setReport(detail),
      (reason: unknown) => active && setError(reason instanceof Error ? reason.message : "无法读取报告详情。"),
    );
    return () => {
      active = false;
    };
  }, [selectedId]);

  return (
    <section
      className="mx-auto max-w-[1440px] px-6 pt-[26px] pb-[52px] min-[1025px]:px-5 min-[1025px]:pt-5 min-[1025px]:pb-10"
      aria-label="检索评测"
    >
      {/* 曾经是页面级徽章放顶栏；改成 Section 后，顶栏只留统一质量门，分项结论回到各自小节。 */}
      {report ? (
        <Badge tone={report.passed ? "success" : "danger"} shape="status">
          {report.passed ? "质量门已通过" : "质量门未通过"}
        </Badge>
      ) : null}

      {error ? (
        <ErrorBanner>{error}</ErrorBanner>
      ) : null}
      {reports === null && !error ? (
        <div className="grid gap-3">
          <span role="status" className="sr-only">
            正在读取正式评测报告
          </span>
          <Skeleton className="h-7 w-64" />
          <MetricSkeleton />
        </div>
      ) : null}
      {reports?.length === 0 ? (
        <div className="rounded-lg border border-line bg-surface">
          <EmptyState
            kind="empty"
            title="还没有正式评测报告"
            description="报告需要由离线真实模型评测生成，页面不会启动重量评测任务。"
          />
        </div>
      ) : null}

      {reports && reports.length > 0 ? (
        <>
          <label className="mb-4 flex flex-col items-stretch gap-3 text-[11px] text-[#737b8e] min-[561px]:flex-row min-[561px]:items-center">
            评测运行
            <Select
              size="sm"
              className="w-90"
              value={selectedId}
              onChange={(event) => {
                setReport(null);
                setError("");
                setSelectedId(event.target.value);
              }}
            >
              {reports.map((item) => (
                <option key={item.report_id} value={item.report_id}>
                  {new Date(item.run_at).toLocaleString("zh-CN")} · {item.dataset_version}
                </option>
              ))}
            </Select>
          </label>
          {!report && !error ? (
            <div className="grid gap-3">
              <span role="status" className="sr-only">
                正在读取指标详情
              </span>
              <MetricSkeleton />
            </div>
          ) : null}
        </>
      ) : null}

      {report ? (
        <div className="grid">
          <div className="grid grid-cols-1 overflow-hidden rounded-[10px] border border-line bg-surface min-[561px]:grid-cols-4">
            <div className="flex min-w-0 flex-col gap-1.5 border-r border-line p-4 last:border-r-0">
              <span className="text-[9px] text-[#9299ab]">数据版本</span>
              <strong className="truncate text-[11px] text-[#263047]">{report.dataset_version}</strong>
            </div>
            <div className="flex min-w-0 flex-col gap-1.5 border-r border-line p-4 last:border-r-0">
              <span className="text-[9px] text-[#9299ab]">标注问题</span>
              <strong className="truncate text-[11px] text-[#263047]">{report.query_count} 条</strong>
            </div>
            <div className="flex min-w-0 flex-col gap-1.5 border-r border-line p-4 last:border-r-0">
              <span className="text-[9px] text-[#9299ab]">运行时间</span>
              <strong className="truncate text-[11px] text-[#263047]">
                {new Date(report.run_at).toLocaleString("zh-CN")}
              </strong>
            </div>
            <div className="flex min-w-0 flex-col gap-1.5 border-r border-line p-4 last:border-r-0">
              <span className="text-[9px] text-[#9299ab]">代码提交</span>
              <a
                className="truncate text-[11px] text-[#5548cc]"
                href={`https://github.com/ai-agent-lab-cn/rag-enterprise/commit/${report.commit}`}
                target="_blank"
                rel="noreferrer"
              >
                {report.commit.slice(0, 8)}
              </a>
            </div>
          </div>
          <div className="my-[14px] grid grid-cols-1 gap-3 min-[561px]:grid-cols-2 min-[821px]:grid-cols-3">
            {METRIC_ROWS.map(({ key, label }) => {
              const metric = report[key];
              return metric ? <MetricCard key={key} label={label} metric={metric} /> : null;
            })}
          </div>
          <div className="grid grid-cols-1 gap-3 min-[561px]:grid-cols-[1.2fr_0.8fr]">
            <section className="min-w-0 rounded-[9px] border border-line bg-surface p-[17px]">
              <span className="text-[12px] text-[#7165d8] tracking-[0.04em] font-semibold">模型标识</span>
              {Object.entries(report.models).map(([key, value]) => (
                <p key={key} className="mt-[10px] mb-0 grid gap-1">
                  <b className="text-[9px] text-[#848b9e]">{key}</b>
                  <code className="truncate text-[9px] text-[#3c4458]" title={value}>
                    {value}
                  </code>
                </p>
              ))}
            </section>
            <section className="min-w-0 rounded-[9px] border border-line bg-surface p-[17px]">
              <span className="text-[12px] text-[#7165d8] tracking-[0.04em] font-semibold">运行参数</span>
              <dl className="mt-[7px] mb-0">
                {Object.entries(report.parameters).map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-2.5 py-1">
                    <dt className="m-0 text-[9px] text-[#747c90]">{key}</dt>
                    <dd className="m-0 text-[9px] text-[#747c90]">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          </div>
          <p className="mt-[17px] mb-0 text-center text-[11px] text-[#7e879a]">
            此页面只读取已生成的正式报告，不会从浏览器启动模型评测。
          </p>
        </div>
      ) : null}
    </section>
  );
}
