import { useEffect, useState } from "react";
import { api } from "../api";
import type { AcceptanceRun, EvaluationCenterOverview, GovernedBadCase, PipelineEvaluation } from "../types";
import { AnswerEvaluationPage } from "./AnswerEvaluationPage";
import { EvaluationPage } from "./EvaluationPage";
import { TopbarPortal } from "./TopbarPortal";

type EvaluationTab = "overview" | "retrieval" | "answer" | "pipeline" | "bad-cases" | "acceptance";

const TABS: Array<{ id: EvaluationTab; label: string }> = [
  { id: "overview", label: "总览" },
  { id: "retrieval", label: "检索质量" },
  { id: "answer", label: "回答质量" },
  { id: "pipeline", label: "工程指标" },
  { id: "bad-cases", label: "Bad Case" },
  { id: "acceptance", label: "链路验收" },
];

export function EvaluationCenterPage({ isAdmin, initialTab = "overview" }: { isAdmin: boolean; initialTab?: EvaluationTab }) {
  const [tab, setTab] = useState<EvaluationTab>(initialTab);
  const [overview, setOverview] = useState<EvaluationCenterOverview | null>(null);
  const [pipeline, setPipeline] = useState<PipelineEvaluation | null>(null);
  const [badCases, setBadCases] = useState<GovernedBadCase[] | null>(null);
  const [acceptanceRuns, setAcceptanceRuns] = useState<AcceptanceRun[] | null>(null);
  const [error, setError] = useState("");
  const [localLoading, setLocalLoading] = useState(false);

  useEffect(() => {
    api.getEvaluationCenterOverview().then(setOverview, (reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取评测总览。"));
  }, []);

  const selectTab = async (next: EvaluationTab) => {
    setTab(next);
    setError("");
    if (next === "pipeline" && pipeline === null) {
      setLocalLoading(true);
      try {
        setPipeline(await api.getPipelineEvaluation());
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "无法读取工程指标。");
      } finally {
        setLocalLoading(false);
      }
    }
    if (next === "bad-cases" && badCases === null) {
      setLocalLoading(true);
      try {
        setBadCases(await api.listGovernedBadCases());
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "无法读取 Bad Case。");
      } finally {
        setLocalLoading(false);
      }
    }
    if (next === "acceptance" && acceptanceRuns === null) {
      setLocalLoading(true);
      try {
        setAcceptanceRuns(await api.listAcceptanceRuns("kb_default"));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "无法读取链路验收记录。");
      } finally {
        setLocalLoading(false);
      }
    }
  };

  const updateCase = async (item: GovernedBadCase, update: Parameters<typeof api.updateGovernedBadCase>[1]) => {
    setLocalLoading(true);
    setError("");
    try {
      const updated = await api.updateGovernedBadCase(item.case_id, update);
      setBadCases((current) => current?.map((candidate) => candidate.case_id === item.case_id ? updated : candidate) ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Bad Case 更新失败。");
    } finally {
      setLocalLoading(false);
    }
  };

  return <section className="evaluation-center" aria-label="评测中心">
    <TopbarPortal>{overview ? <span className={overview.passed ? "release-badge" : "release-badge is-failed"}>{overview.passed ? "统一质量门已通过" : "统一质量门未通过"}</span> : null}</TopbarPortal>
    <div className="detail-tabs evaluation-tabs" role="tablist" aria-label="评测治理范围">
      {TABS.map((item) => <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? "is-active" : ""} onClick={() => void selectTab(item.id)}>{item.label}</button>)}
    </div>
    {error ? <div className="error-banner" role="alert">{error}</div> : null}
    {localLoading ? <div className="inline-governance-loading" aria-live="polite">正在读取当前 Tab…</div> : null}
    {tab === "overview" ? <OverviewPanel overview={overview} /> : null}
    {tab === "retrieval" ? <EvaluationPage /> : null}
    {tab === "answer" ? <AnswerEvaluationPage /> : null}
    {tab === "pipeline" && pipeline ? <PipelinePanel summary={pipeline} /> : null}
    {tab === "bad-cases" && badCases ? <BadCasePanel items={badCases} isAdmin={isAdmin} onUpdate={updateCase} /> : null}
    {tab === "acceptance" && acceptanceRuns ? <AcceptancePanel runs={acceptanceRuns} isAdmin={isAdmin} busy={localLoading} onStarted={(run) => setAcceptanceRuns((current) => [run, ...(current ?? [])])} onError={setError}/> : null}
  </section>;
}

function AcceptancePanel({ runs, isAdmin, busy, onStarted, onError }: { runs: AcceptanceRun[]; isAdmin: boolean; busy: boolean; onStarted: (run: AcceptanceRun) => void; onError: (message: string) => void }) {
  const latest = runs[0] ?? null;
  const start = async () => { try { onStarted(await api.startAcceptanceRun("kb_default")); } catch (reason) { onError(reason instanceof Error ? reason.message : "链路验收启动失败。"); } };
  return <div className="evaluation-panel"><header className="compact-section-heading"><div><h2>真实链路总验收</h2><p>S3 → Sync → Parse → Index → Retrieval → ACL → Citation → Evaluation</p></div>{isAdmin ? <button type="button" className="primary-action" disabled={busy} onClick={() => void start()}>运行默认知识库验收</button> : null}</header>{latest ? <><div className={`acceptance-summary is-${latest.status}`}><strong>{latest.status === "passed" ? "通过" : latest.status === "failed" ? "失败" : "阻塞"}</strong><span>Schema V{latest.schema_version} · {latest.commit_sha.slice(0, 12)} · {new Date(latest.created_at).toLocaleString("zh-CN")}</span></div><ol className="acceptance-steps">{latest.steps.map((step) => <li key={step.step_key} className={`is-${step.status}`}><span>{step.status === "passed" ? "✓" : step.status === "failed" ? "×" : "!"}</span><div><strong>{step.title}</strong><p>{step.summary}</p>{Object.keys(step.evidence).length ? <small>{JSON.stringify(step.evidence)}</small> : null}</div></li>)}</ol><div className="table-scroll acceptance-history"><table className="governance-table"><thead><tr><th>验收记录</th><th>结论</th><th>Schema</th><th>Commit</th><th>运行时间</th></tr></thead><tbody>{runs.map((run) => <tr key={run.acceptance_run_id}><td>{run.acceptance_run_id}</td><td>{run.status === "passed" ? "通过" : run.status === "failed" ? "失败" : "阻塞"}</td><td>V{run.schema_version}</td><td>{run.commit_sha.slice(0, 12)}</td><td>{new Date(run.created_at).toLocaleString("zh-CN")}</td></tr>)}</tbody></table></div></> : <div className="evaluation-state">还没有链路验收记录。</div>}</div>;
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
    <header className="compact-section-heading"><div><h2>{overview.passed ? "统一质量门已通过" : "统一质量门未通过"}</h2><p>{overview.report_count} 份正式报告已纳入治理结论。</p></div></header>
    <table className="governance-table"><thead><tr><th>范围</th><th>版本/规则</th><th>结论</th></tr></thead><tbody>{rows.map(([name, version, passed]) => <tr key={name}><td>{name}</td><td>{version}</td><td><span className={passed === false ? "status-fail" : passed === true ? "status-pass" : "status-muted"}>{passed === false ? "未通过" : passed === true ? "通过" : "待核对"}</span></td></tr>)}</tbody></table>
  </div>;
}

function PipelinePanel({ summary }: { summary: PipelineEvaluation }) {
  const rows = [
    ["新增", summary.added_count], ["更新", summary.updated_count], ["删除", summary.deleted_count],
    ["跳过", summary.skipped_count], ["失败", summary.failed_count], ["重试", summary.retry_count],
  ];
  return <div className="evaluation-panel"><header className="compact-section-heading"><div><h2>{summary.run_count} 个同步批次</h2><p>平均耗时 {(summary.average_duration_ms / 1000).toFixed(1)} 秒 · 失败率 {(summary.failure_rate * 100).toFixed(1)}%</p></div></header><table className="governance-table"><thead><tr><th>工程指标</th><th>数量</th></tr></thead><tbody>{rows.map(([label, value]) => <tr key={label}><td>{label}</td><td>{value}</td></tr>)}</tbody></table></div>;
}

function BadCasePanel({ items, isAdmin, onUpdate }: { items: GovernedBadCase[]; isAdmin: boolean; onUpdate: (item: GovernedBadCase, update: Parameters<typeof api.updateGovernedBadCase>[1]) => void }) {
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");
  const [stage, setStage] = useState("");
  const visible = items.filter((item) => (!status || item.status === status) && (!severity || item.severity === severity) && (!stage || item.failure_stage === stage));
  return <div className="evaluation-panel"><div className="bad-case-filters"><div className="bad-case-filter-fields"><label>状态<span className="sr-only">Bad Case 状态筛选</span><select aria-label="Bad Case 状态筛选" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部</option><option value="new">新建</option><option value="confirmed">已确认</option><option value="fixing">修复中</option><option value="resolved">已解决</option><option value="regression_added">已入回归集</option><option value="ignored">已忽略</option></select></label><label>严重级别<select aria-label="Bad Case 严重级别筛选" value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="">全部</option><option value="critical">严重</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></label><label>失败阶段<select aria-label="Bad Case 失败阶段筛选" value={stage} onChange={(event) => setStage(event.target.value)}><option value="">全部</option>{Array.from(new Set(items.map((item) => item.failure_stage))).map((value) => <option value={value} key={value}>{value}</option>)}</select></label></div><span>{visible.length} / {items.length} 个案例 · 管理员治理，成员只读</span></div><div className="table-scroll"><table className="governance-table"><thead><tr><th>问题</th><th>阶段</th><th>分类</th><th>严重级别</th><th>状态</th><th>治理</th></tr></thead><tbody>{visible.map((item) => <tr key={item.case_id}><td><strong>{item.question}</strong><small>{item.case_id}</small></td><td>{item.failure_stage}</td><td>{item.category}</td><td>{item.severity}</td><td>{item.status}</td><td><BadCaseGovernanceDetails item={item} isAdmin={isAdmin} onUpdate={onUpdate} /></td></tr>)}</tbody></table></div>{visible.length === 0 ? <div className="evaluation-state">当前筛选范围没有 Bad Case。</div> : null}</div>;
}

function BadCaseGovernanceDetails({ item, isAdmin, onUpdate }: { item: GovernedBadCase; isAdmin: boolean; onUpdate: (item: GovernedBadCase, update: Parameters<typeof api.updateGovernedBadCase>[1]) => void }) {
  const [rootCause, setRootCause] = useState(item.root_cause ?? "");
  const [fixCommit, setFixCommit] = useState(item.fix_commit ?? "");
  const [assignee, setAssignee] = useState(item.assignee ?? "");
  const next = item.status === "new" ? "confirmed" : item.status === "confirmed" ? "fixing" : item.status === "fixing" ? "resolved" : item.status === "resolved" ? "regression_added" : null;
  const nextLabel = item.status === "new" ? "确认" : item.status === "confirmed" ? "开始修复" : item.status === "fixing" ? "标记已解决" : item.status === "resolved" ? "加入回归集" : "";
  return <details className="bad-case-details"><summary>治理详情</summary><dl><div><dt>期望状态</dt><dd>{item.expected_answer_status ?? "未标注"}</dd></div><div><dt>实际状态</dt><dd>{item.actual_answer_status ?? "无"}</dd></div><div><dt>实际回答</dt><dd>{item.actual_answer ?? "无"}</dd></div></dl>{isAdmin ? <div className="bad-case-editor"><label>根因<input value={rootCause} onChange={(event) => setRootCause(event.target.value)} /></label><label>负责人<input value={assignee} onChange={(event) => setAssignee(event.target.value)} /></label><label>修复 Commit<input value={fixCommit} onChange={(event) => setFixCommit(event.target.value)} /></label>{next ? <button type="button" className="table-action" onClick={() => onUpdate(item, { status: next, severity: item.severity, root_cause: rootCause || undefined, assignee: assignee || undefined, fix_commit: fixCommit || undefined, regression_passed: next === "regression_added" ? true : undefined })}>{nextLabel}</button> : null}{item.status !== "ignored" && item.status !== "regression_added" ? <button type="button" className="table-action is-danger" onClick={() => onUpdate(item, { status: "ignored", severity: item.severity })}>忽略</button> : null}</div> : null}</details>;
}
