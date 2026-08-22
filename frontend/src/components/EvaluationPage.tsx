import { useEffect, useState } from "react";
import { api } from "../api";
import type { EvaluationMetric, EvaluationReport, EvaluationReportSummary } from "../types";
import { TopbarPortal } from "./TopbarPortal";

const METRIC_ROWS: Array<{
  key: "recall_at_5" | "vector_mrr" | "rerank_mrr";
  label: string;
}> = [
  { key: "recall_at_5", label: "Recall@5" },
  { key: "vector_mrr", label: "向量 MRR" },
  { key: "rerank_mrr", label: "最终排序 MRR" },
];

function MetricCard({ label, metric }: { label: string; metric: EvaluationMetric }) {
  const change = metric.baseline === null ? null : metric.value - metric.baseline;
  return (
    <article className="quality-card">
      <div className="quality-heading">
        <span>{label}</span>
        <strong>{metric.value.toFixed(4)}</strong>
      </div>
      <div className="quality-bar" aria-label={`${label} ${metric.value.toFixed(4)}`}>
        <span style={{ width: `${metric.value * 100}%` }} />
        <i style={{ left: `${metric.threshold * 100}%` }} title={`冻结阈值 ${metric.threshold}`} />
      </div>
      <div className="quality-meta">
        <span>阈值 {metric.threshold.toFixed(2)}</span>
        <span>{change === null ? "首次基线" : `较基线 ${change >= 0 ? "+" : ""}${change.toFixed(4)}`}</span>
        <b className={metric.passed ? "status-pass" : "status-fail"}>{metric.regressed ? "发生回退" : metric.passed ? "通过" : "未通过"}</b>
      </div>
    </article>
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
    <section className="evaluation-page" aria-label="检索评测">
      <TopbarPortal>{report ? <span className={report.passed ? "release-badge" : "release-badge is-failed"}>{report.passed ? "质量门已通过" : "质量门未通过"}</span> : null}</TopbarPortal>

      {error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}
      {reports === null && !error ? <div className="evaluation-state pulse">正在读取正式评测报告…</div> : null}
      {reports?.length === 0 ? (
        <div className="evaluation-state">
          <h2>还没有正式评测报告</h2>
          <p>报告需要由离线真实模型评测生成，页面不会启动重量评测任务。</p>
        </div>
      ) : null}

      {reports && reports.length > 0 ? (
        <>
          <label className="report-picker">
            评测运行
            <select
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
            </select>
          </label>
          {!report && !error ? <div className="evaluation-state pulse">正在读取指标详情…</div> : null}
        </>
      ) : null}

      {report ? (
        <div className="evaluation-content">
          <div className="report-context">
            <div>
              <span>数据版本</span>
              <strong>{report.dataset_version}</strong>
            </div>
            <div>
              <span>标注问题</span>
              <strong>{report.query_count} 条</strong>
            </div>
            <div>
              <span>运行时间</span>
              <strong>{new Date(report.run_at).toLocaleString("zh-CN")}</strong>
            </div>
            <div>
              <span>代码提交</span>
              <a href={`https://github.com/ai-agent-lab-cn/rag-enterprise/commit/${report.commit}`} target="_blank" rel="noreferrer">
                {report.commit.slice(0, 8)}
              </a>
            </div>
          </div>
          <div className="quality-grid">
            {METRIC_ROWS.map(({ key, label }) => (
              <MetricCard key={key} label={label} metric={report[key]} />
            ))}
          </div>
          <div className="report-details">
            <section>
              <span className="section-kicker">模型标识</span>
              {Object.entries(report.models).map(([key, value]) => (
                <p key={key}>
                  <b>{key}</b>
                  <code title={value}>{value}</code>
                </p>
              ))}
            </section>
            <section>
              <span className="section-kicker">运行参数</span>
              <dl>
                {Object.entries(report.parameters).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          </div>
          <p className="readonly-note">此页面只读取已生成的正式报告，不会从浏览器启动模型评测。</p>
        </div>
      ) : null}
    </section>
  );
}
