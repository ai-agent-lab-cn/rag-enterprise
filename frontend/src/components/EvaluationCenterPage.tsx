import { useEffect, useState } from "react";
import { api } from "../api";
import type { EvaluationCenterOverview, EvaluationReportSummary, PipelineEvaluation } from "../types";
import { AnswerEvaluationPage } from "./AnswerEvaluationPage";
import { EvaluationPage } from "./EvaluationPage";
import { TopbarPortal } from "./TopbarPortal";

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

  return <section className="evaluation-center" aria-label="评测中心">
    <TopbarPortal>{overview ? <span className={overview.passed ? "release-badge" : "release-badge is-failed"}>{overview.passed ? "统一质量门已通过" : "统一质量门未通过"}</span> : null}</TopbarPortal>
    <nav className="section-anchors" aria-label="评测中心小节">
      {SECTIONS.map((item) => <a key={item.id} href={`#${item.id}`}>{item.label}</a>)}
    </nav>
    {error ? <div className="error-banner" role="alert">{error}</div> : null}

    <section id="summary" className="evaluation-section">
      <h2>质量总览</h2>
      <OverviewPanel overview={overview} />
    </section>

    <section id="retrieval" className="evaluation-section">
      <h2>检索质量</h2>
      <p className="section-question">正确证据有没有被召回，以及是否排在足够靠前的位置？</p>
      <EvaluationPage />
    </section>

    <section id="answer" className="evaluation-section">
      <h2>回答质量</h2>
      <p className="section-question">模型是否基于检索证据生成了正确、可信、可引用的答案？</p>
      <AnswerEvaluationPage />
    </section>

    <section id="pipeline" className="evaluation-section">
      <h2>工程指标</h2>
      <p className="section-question">RAG 链路的性能、稳定性和成本是否符合要求？</p>
      {pipeline ? <PipelinePanel summary={pipeline} /> : <div className="evaluation-panel" aria-busy="true" />}
    </section>

    <section id="recent" className="evaluation-section">
      <h2>最近评测</h2>
      <RecentRuns reports={reports} />
    </section>
  </section>;
}

function RecentRuns({ reports }: { reports: EvaluationReportSummary[] | null }) {
  if (!reports) return <div className="evaluation-panel" aria-busy="true" />;
  if (!reports.length) return <div className="evaluation-panel"><div className="evaluation-state">还没有正式评测记录。</div></div>;
  return <div className="evaluation-panel"><div className="overflow-x-auto"><table className="governance-table">
    <thead><tr><th>评测</th><th>数据集版本</th><th>运行时间</th><th>结论</th></tr></thead>
    <tbody>{reports.map((item) => <tr key={item.report_id}>
      <td><strong>{item.report_id}</strong><small>{item.dataset_id}</small></td>
      <td>{item.dataset_version}</td>
      <td>{new Date(item.run_at).toLocaleString("zh-CN")}</td>
      <td><span className={item.passed ? "status-pass" : "status-fail"}>{item.passed ? "PASS" : "FAIL"}</span></td>
    </tr>)}</tbody>
  </table></div></div>;
}

function OverviewPanel({ overview }: { overview: EvaluationCenterOverview | null }) {
  if (!overview) return <div className="evaluation-panel" aria-busy="true" />;
  const rows = [
    ["检索质量", overview.retrieval_report?.dataset_version ?? "无正式报告", overview.retrieval_report?.passed],
    ["回答质量", overview.answer_report?.dataset_version ?? "无正式报告", overview.answer_report?.passed],
    ["工程指标", "读取同步运行记录", null],
    ["安全边界", "ACL 泄漏必须为 0", overview.passed],
  ] as const;
  return <div className="evaluation-panel">
    <header className="compact-section-heading"><div><h3>{overview.passed ? "统一质量门已通过" : "统一质量门未通过"}</h3><p>{overview.report_count} 份正式报告已纳入治理结论。</p></div></header>
    <table className="governance-table"><thead><tr><th>范围</th><th>版本/规则</th><th>结论</th></tr></thead><tbody>{rows.map(([name, version, passed]) => <tr key={name}><td>{name}</td><td>{version}</td><td><span className={passed === false ? "status-fail" : passed === true ? "status-pass" : "status-muted"}>{passed === false ? "未通过" : passed === true ? "通过" : "待核对"}</span></td></tr>)}</tbody></table>
  </div>;
}

function PipelinePanel({ summary }: { summary: PipelineEvaluation }) {
  const rows = [
    ["新增", summary.added_count], ["更新", summary.updated_count], ["删除", summary.deleted_count],
    ["跳过", summary.skipped_count], ["失败", summary.failed_count], ["重试", summary.retry_count],
  ];
  return <div className="evaluation-panel"><header className="compact-section-heading"><div><h3>{summary.run_count} 个同步批次</h3><p>平均耗时 {(summary.average_duration_ms / 1000).toFixed(1)} 秒 · 失败率 {(summary.failure_rate * 100).toFixed(1)}%</p></div></header><table className="governance-table"><thead><tr><th>工程指标</th><th>数量</th></tr></thead><tbody>{rows.map(([label, value]) => <tr key={label}><td>{label}</td><td>{value}</td></tr>)}</tbody></table></div>;
}
