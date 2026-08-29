import { useEffect, useState } from "react";
import { api } from "../api";
import type { AnswerEvaluationMetric, AnswerEvaluationReport, AnswerEvaluationSummary } from "../types";
import { TopbarPortal } from "./TopbarPortal";

const LABELS: Record<string, string> = {
  answer_correctness: "回答正确性",
  completeness: "要点完整性",
  faithfulness: "忠实度",
  citation_validity: "引用有效率",
  citation_support: "引用支持率",
  claim_citation_coverage: "声明引用覆盖率",
  unsupported_claim_rate: "无支持声明率",
  contradiction_rate: "矛盾声明率",
  refusal_accuracy: "拒答正确率",
  source_conflict_accuracy: "来源冲突识别率",
  failure_strategy_stability: "失败策略稳定性",
};
const METRIC_GROUPS = [
  {
    title: "回答质量",
    description: "衡量答案是否正确并覆盖必要信息。",
    metrics: ["answer_correctness", "completeness"],
  },
  {
    title: "证据质量",
    description: "检查答案是否忠于资料，且引用完整、有效并真正支持结论。",
    metrics: ["faithfulness", "citation_validity", "citation_support", "claim_citation_coverage"],
  },
  {
    title: "幻觉风险",
    description: "数值越低越好，用于识别无来源或与资料矛盾的声明。",
    metrics: ["unsupported_claim_rate", "contradiction_rate"],
  },
  {
    title: "失败控制",
    description: "验证资料不足或服务异常时能否稳定拒答和降级。",
    metrics: ["refusal_accuracy", "source_conflict_accuracy", "failure_strategy_stability"],
  },
] as const;
function Metric({ name, metric }: { name: string; metric: AnswerEvaluationMetric }) {
  return (
    <article className="quality-card">
      <div className="quality-heading">
        <span>{LABELS[name] ?? name}</span>
        <strong>{(metric.value * 100).toFixed(0)}%</strong>
      </div>
      <div className="quality-bar">
        <span
          style={{
            width: `${metric.direction === "maximum" ? (1 - metric.value) * 100 : metric.value * 100}%`,
          }}
        />
        <i style={{ left: `${metric.threshold * 100}%` }} />
      </div>
      <div className="quality-meta">
        <span>
          {metric.direction === "maximum" ? "上限" : "下限"} {(metric.threshold * 100).toFixed(0)}%
        </span>
        <b className={metric.passed ? "status-pass" : "status-fail"}>{metric.passed ? "通过" : "未通过"}</b>
      </div>
    </article>
  );
}

export function AnswerEvaluationPage() {
  const [reports, setReports] = useState<AnswerEvaluationSummary[] | null>(null);
  const [selected, setSelected] = useState("");
  const [report, setReport] = useState<AnswerEvaluationReport | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.listAnswerEvaluations().then(
      (items) => {
        setReports(items);
        setSelected(items[0]?.report_id ?? "");
      },
      (reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取回答评测。"),
    );
  }, []);
  useEffect(() => {
    if (!selected) return;
    api.getAnswerEvaluation(selected).then(setReport, (reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取回答评测详情。"));
  }, [selected]);
  return (
    <section className="evaluation-page answer-evaluation-page" aria-label="回答评测">
      <TopbarPortal>{report ? <span className={report.passed ? "release-badge" : "release-badge is-failed"}>{report.passed ? "回答质量门已通过" : "回答质量门未通过"}</span> : null}</TopbarPortal>
      {error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}
      {reports === null && !error ? <div className="evaluation-state pulse">正在读取正式回答评测…</div> : null}
      {reports?.length === 0 ? (
        <div className="evaluation-state">
          <h2>还没有正式回答评测报告</h2>
          <p>页面只读取人工复核后放行的正式报告。</p>
        </div>
      ) : null}
      {reports?.length ? (
        <label className="report-picker">
          评测运行
          <select
            value={selected}
            onChange={(event) => {
              setReport(null);
              setSelected(event.target.value);
            }}
          >
            {reports.map((item) => (
              <option key={item.report_id} value={item.report_id}>
                {new Date(item.run_at).toLocaleString("zh-CN")} · {item.dataset_version}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {report ? (
        <div className="evaluation-content">
          <div className="report-context">
            <div>
              <span>数据版本</span>
              <strong>{report.dataset_version}</strong>
            </div>
            <div>
              <span>评测样本</span>
              <strong>{report.case_count} 条</strong>
            </div>
            <div>
              <span>Prompt</span>
              <strong>{report.prompt_version}</strong>
            </div>
            <div>
              <span>代码提交</span>
              <a href={`https://github.com/ai-agent-lab-cn/rag-enterprise/commit/${report.commit}`} target="_blank" rel="noreferrer">
                {report.commit.slice(0, 8)}
              </a>
            </div>
          </div>
          <div className="metric-groups">
            {METRIC_GROUPS.map((group) => (
              <section className="metric-group" key={group.title}>
                <header>
                  <div>
                    <h2>{group.title}</h2>
                    <p>{group.description}</p>
                  </div>
                  <span>{group.metrics.length} 项指标</span>
                </header>
                <div className="quality-grid answer-quality-grid">
                  {group.metrics.map((name) => {
                    const metric = report.metrics[name];
                    return metric ? <Metric key={name} name={name} metric={metric} /> : null;
                  })}
                </div>
              </section>
            ))}
          </div>
          <div className="report-details">
            <section>
              <span className="section-kicker">模型标识</span>
              {Object.entries(report.models).map(([key, value]) => (
                <p key={key}>
                  <b>{key}</b>
                  <code>{value}</code>
                </p>
              ))}
            </section>
            <section>
              <span className="section-kicker">可复现上下文</span>
              <dl>
                <div>
                  <dt>Prompt Hash</dt>
                  <dd title={report.prompt_hash}>{report.prompt_hash.slice(0, 12)}</dd>
                </div>
                {Object.entries(report.parameters).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          </div>
          <p className="readonly-note">此页面不会启动模型评测；展示的是与数据版本和 commit 绑定的正式报告。</p>
        </div>
      ) : null}
    </section>
  );
}
