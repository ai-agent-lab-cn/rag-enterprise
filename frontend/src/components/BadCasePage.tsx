import { useEffect, useState } from "react";
import { api } from "../api";
import type { GovernedBadCase } from "../types";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";
import { Select } from "./ui/Select";

/**
 * Bad Case 治理。
 *
 * 它之所以配得上一个独立菜单，不是因为指标多，而是因为它是一条完整的工作流：
 * 发现 → 分类 → 定位根因 → 修复 → 回归 → 关闭。评测中心回答「质量怎么样」，
 * 这里回答「哪里有问题、为什么、修好了没有」。
 */
export function BadCasePage({ isAdmin }: { isAdmin: boolean }) {
  const [items, setItems] = useState<GovernedBadCase[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.listGovernedBadCases().then(setItems, (reason: unknown) =>
      setError(reason instanceof Error ? reason.message : "无法读取 Bad Case。"));
  }, []);

  const updateCase = async (item: GovernedBadCase, update: Parameters<typeof api.updateGovernedBadCase>[1]) => {
    setBusy(true);
    setError("");
    try {
      const updated = await api.updateGovernedBadCase(item.case_id, update);
      setItems((current) => current?.map((candidate) => candidate.case_id === item.case_id ? updated : candidate) ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Bad Case 更新失败。");
    } finally {
      setBusy(false);
    }
  };

  return <section className="evaluation-center" aria-label="Bad Case">
    {error ? <div className="error-banner" role="alert">{error}</div> : null}
    {busy ? <div className="inline-governance-loading" aria-live="polite">正在保存治理结果…</div> : null}
    {items ? <BadCasePanel items={items} isAdmin={isAdmin} onUpdate={updateCase} /> : <div className="evaluation-panel" aria-busy="true" />}
  </section>;
}

function BadCasePanel({ items, isAdmin, onUpdate }: { items: GovernedBadCase[]; isAdmin: boolean; onUpdate: (item: GovernedBadCase, update: Parameters<typeof api.updateGovernedBadCase>[1]) => void }) {
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");
  const [stage, setStage] = useState("");
  const visible = items.filter((item) => (!status || item.status === status) && (!severity || item.severity === severity) && (!stage || item.failure_stage === stage));
  return <div className="evaluation-panel"><div className="bad-case-filters"><div className="bad-case-filter-fields"><label><span className="shrink-0">状态</span><Select size="sm" className="w-28" aria-label="Bad Case 状态筛选" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部</option><option value="new">新建</option><option value="confirmed">已确认</option><option value="fixing">修复中</option><option value="resolved">已解决</option><option value="regression_added">已入回归集</option><option value="ignored">已忽略</option></Select></label><label><span className="shrink-0">严重级别</span><Select size="sm" className="w-20" aria-label="Bad Case 严重级别筛选" value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="">全部</option><option value="critical">严重</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></Select></label><label><span className="shrink-0">失败阶段</span><Select size="sm" className="w-28" aria-label="Bad Case 失败阶段筛选" value={stage} onChange={(event) => setStage(event.target.value)}><option value="">全部</option>{Array.from(new Set(items.map((item) => item.failure_stage))).map((value) => <option value={value} key={value}>{value}</option>)}</Select></label></div><span>{visible.length} / {items.length} 个案例 · 管理员治理，成员只读</span></div><div className="overflow-x-auto"><table className="governance-table"><thead><tr><th>问题</th><th>阶段</th><th>分类</th><th>严重级别</th><th>状态</th><th>治理</th></tr></thead><tbody>{visible.map((item) => <tr key={item.case_id}><td><strong>{item.question}</strong><small>{item.case_id}</small></td><td>{item.failure_stage}</td><td>{item.category}</td><td>{item.severity}</td><td>{item.status}</td><td><BadCaseGovernanceDetails item={item} isAdmin={isAdmin} onUpdate={onUpdate} /></td></tr>)}</tbody></table></div>{visible.length === 0 ? <div className="evaluation-state">当前筛选范围没有 Bad Case。</div> : null}</div>;
}

function BadCaseGovernanceDetails({ item, isAdmin, onUpdate }: { item: GovernedBadCase; isAdmin: boolean; onUpdate: (item: GovernedBadCase, update: Parameters<typeof api.updateGovernedBadCase>[1]) => void }) {
  const [rootCause, setRootCause] = useState(item.root_cause ?? "");
  const [fixCommit, setFixCommit] = useState(item.fix_commit ?? "");
  const [assignee, setAssignee] = useState(item.assignee ?? "");
  const next = item.status === "new" ? "confirmed" : item.status === "confirmed" ? "fixing" : item.status === "fixing" ? "resolved" : item.status === "resolved" ? "regression_added" : null;
  const nextLabel = item.status === "new" ? "确认" : item.status === "confirmed" ? "开始修复" : item.status === "fixing" ? "标记已解决" : item.status === "resolved" ? "加入回归集" : "";
  return <details className="bad-case-details"><summary>治理详情</summary><dl><div><dt>期望状态</dt><dd>{item.expected_answer_status ?? "未标注"}</dd></div><div><dt>实际状态</dt><dd>{item.actual_answer_status ?? "无"}</dd></div><div><dt>实际回答</dt><dd>{item.actual_answer ?? "无"}</dd></div></dl>{isAdmin ? <div className="bad-case-editor"><label>根因<Input size="sm" value={rootCause} onChange={(event) => setRootCause(event.target.value)} /></label><label>负责人<Input size="sm" value={assignee} onChange={(event) => setAssignee(event.target.value)} /></label><label>修复 Commit<Input size="sm" value={fixCommit} onChange={(event) => setFixCommit(event.target.value)} /></label><div className="flex gap-1">{next ? <Button variant="ghost" size="sm" onClick={() => onUpdate(item, { status: next, severity: item.severity, root_cause: rootCause || undefined, assignee: assignee || undefined, fix_commit: fixCommit || undefined, regression_passed: next === "regression_added" ? true : undefined })}>{nextLabel}</Button> : null}{item.status !== "ignored" && item.status !== "regression_added" ? <Button variant="ghost" size="sm" className="text-danger-text hover:bg-danger-subtle" onClick={() => onUpdate(item, { status: "ignored", severity: item.severity })}>忽略</Button> : null}</div></div> : null}</details>;
}
