import { useEffect, useState } from "react";
import { api } from "../api";
import type { AcceptanceRun } from "../types";
import { Button } from "./ui/Button";

/**
 * 端到端链路验收。
 *
 * 它承担版本放行职责，结论是 PASS 或 BLOCKED，与「看指标」不是一件事，所以独立成菜单。
 */
export function AcceptancePage({ isAdmin }: { isAdmin: boolean }) {
  const [runs, setRuns] = useState<AcceptanceRun[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.listAcceptanceRuns("kb_default").then(setRuns, (reason: unknown) =>
      setError(reason instanceof Error ? reason.message : "无法读取链路验收记录。"));
  }, []);

  return <section className="evaluation-center" aria-label="链路验收">
    {error ? <div className="error-banner" role="alert">{error}</div> : null}
    {runs ? <AcceptancePanel runs={runs} isAdmin={isAdmin} busy={busy} onStarted={(run) => { setRuns((current) => [run, ...(current ?? [])]); setBusy(false); }} onError={(message) => { setError(message); setBusy(false); }} /> : <div className="evaluation-panel" aria-busy="true" />}
  </section>;
}

function AcceptancePanel({ runs, isAdmin, busy, onStarted, onError }: { runs: AcceptanceRun[]; isAdmin: boolean; busy: boolean; onStarted: (run: AcceptanceRun) => void; onError: (message: string) => void }) {
  const latest = runs[0] ?? null;
  const start = async () => { try { onStarted(await api.startAcceptanceRun("kb_default")); } catch (reason) { onError(reason instanceof Error ? reason.message : "链路验收启动失败。"); } };
  return <div className="evaluation-panel"><header className="compact-section-heading"><div><h2>真实链路总验收</h2><p>S3 → Sync → Parse → Index → Retrieval → ACL → Citation → Evaluation</p></div>{isAdmin ? <Button size="sm" loading={busy} onClick={() => void start()}>运行默认知识库验收</Button> : null}</header>{latest ? <><div className={`acceptance-summary is-${latest.status}`}><strong>{latest.status === "passed" ? "通过" : latest.status === "failed" ? "失败" : "阻塞"}</strong><span>Schema V{latest.schema_version} · {latest.commit_sha.slice(0, 12)} · {new Date(latest.created_at).toLocaleString("zh-CN")}</span></div><ol className="acceptance-steps">{latest.steps.map((step) => <li key={step.step_key} className={`is-${step.status}`}><span>{step.status === "passed" ? "✓" : step.status === "failed" ? "×" : "!"}</span><div><strong>{step.title}</strong><p>{step.summary}</p>{Object.keys(step.evidence).length ? <small>{JSON.stringify(step.evidence)}</small> : null}</div></li>)}</ol><div className="acceptance-history overflow-x-auto"><table className="governance-table"><thead><tr><th>验收记录</th><th>结论</th><th>Schema</th><th>Commit</th><th>运行时间</th></tr></thead><tbody>{runs.map((run) => <tr key={run.acceptance_run_id}><td>{run.acceptance_run_id}</td><td>{run.status === "passed" ? "通过" : run.status === "failed" ? "失败" : "阻塞"}</td><td>V{run.schema_version}</td><td>{run.commit_sha.slice(0, 12)}</td><td>{new Date(run.created_at).toLocaleString("zh-CN")}</td></tr>)}</tbody></table></div></> : <div className="evaluation-state">还没有链路验收记录。</div>}</div>;
}
