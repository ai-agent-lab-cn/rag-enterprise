import { useEffect, useState } from "react";
import { api } from "../api";
import type { AcceptanceRun } from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Column, DataTable } from "./ui/DataTable";
import { EmptyState } from "./ui/EmptyState";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Skeleton } from "./ui/Skeleton";

const STATUS_LABEL: Record<AcceptanceRun["status"], string> = { passed: "通过", failed: "失败", blocked: "阻塞" };
const STATUS_TONE: Record<AcceptanceRun["status"], "success" | "danger" | "warning"> = {
  passed: "success",
  failed: "danger",
  blocked: "warning",
};
/** 三态各自的强调色，用于结论条的左边框——沿用迁移前的三种取值，颜色本身不是设计变更。 */
const STATUS_BORDER: Record<AcceptanceRun["status"], string> = {
  passed: "#36ad68",
  failed: "#cc3d3d",
  blocked: "#d99024",
};

/* DataTable 是 table-fixed，列宽必须显式给，否则 5 列均分会把运行时间截断成
   「2026/9/3 10…」。结论列关 truncate：Badge 是 inline-block，截断会把它裁掉一半。 */
const RUN_COLUMNS: Column<AcceptanceRun>[] = [
  { key: "id", header: "验收记录", width: "24%", render: (run) => run.acceptance_run_id },
  {
    key: "status",
    header: "结论",
    width: "12%",
    truncate: false,
    render: (run) => (
      <Badge tone={STATUS_TONE[run.status]} shape="status">
        {STATUS_LABEL[run.status]}
      </Badge>
    ),
  },
  { key: "schema", header: "Schema", width: "10%", render: (run) => `V${run.schema_version}` },
  { key: "commit", header: "Commit", width: "18%", render: (run) => run.commit_sha.slice(0, 12) },
  { key: "created", header: "运行时间", width: "36%", render: (run) => new Date(run.created_at).toLocaleString("zh-CN") },
];

/**
 * 端到端链路验收。
 *
 * 它承担版本放行职责，结论是 PASS 或 BLOCKED，与「看指标」不是一件事，所以独立成菜单。
 *
 * `blocked` 是有意的第三态：缺少真实 S3、增量删除或 ACL 证据时必须显示 blocked，
 * 不能塌缩成 passed/failed 两态——见结论条与步骤列表里各自独立的 `blocked` 分支。
 */
export function AcceptancePage({ isAdmin }: { isAdmin: boolean }) {
  const [runs, setRuns] = useState<AcceptanceRun[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.listAcceptanceRuns("kb_default").then(setRuns, (reason: unknown) =>
      setError(reason instanceof Error ? reason.message : "无法读取链路验收记录。"));
  }, []);

  return (
    <section className="px-6 pt-5 pb-8" aria-label="链路验收">
      {error ? (
        <ErrorBanner>{error}</ErrorBanner>
      ) : null}
      {runs ? (
        <AcceptancePanel
          runs={runs}
          isAdmin={isAdmin}
          busy={busy}
          onStarted={(run) => {
            setRuns((current) => [run, ...(current ?? [])]);
            setBusy(false);
          }}
          onError={(message) => {
            setError(message);
            setBusy(false);
          }}
        />
      ) : (
        <div className="grid gap-3">
          <span role="status" className="sr-only">
            正在读取链路验收记录
          </span>
          <Skeleton className="h-6 w-72" />
          <Skeleton className="h-24 rounded-lg" />
          <Skeleton className="h-64 rounded-lg" />
        </div>
      )}
    </section>
  );
}

function AcceptancePanel({
  runs,
  isAdmin,
  busy,
  onStarted,
  onError,
}: {
  runs: AcceptanceRun[];
  isAdmin: boolean;
  busy: boolean;
  onStarted: (run: AcceptanceRun) => void;
  onError: (message: string) => void;
}) {
  const latest = runs[0] ?? null;
  const start = async () => {
    try {
      onStarted(await api.startAcceptanceRun("kb_default"));
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "链路验收启动失败。");
    }
  };
  return (
    <div>
      <header className="mb-3.5 flex items-start justify-between gap-2">
        <div>
          <h2 className="m-0 text-[21px] font-bold">真实链路总验收</h2>
          <p className="mt-1 mb-0 text-[13.12px] text-ink-muted">S3 → Sync → Parse → Index → Retrieval → ACL → Citation → Evaluation</p>
        </div>
        {isAdmin ? (
          <Button size="sm" loading={busy} onClick={() => void start()}>
            运行默认知识库验收
          </Button>
        ) : null}
      </header>
      {latest ? (
        <>
          <div
            className="my-3 flex items-center gap-3 rounded-none bg-[#f8f9fc] py-2.5 px-3"
            style={{ borderLeft: `3px solid ${STATUS_BORDER[latest.status]}` }}
          >
            <Badge tone={STATUS_TONE[latest.status]} shape="status">
              {STATUS_LABEL[latest.status]}
            </Badge>
            <span>
              Schema V{latest.schema_version} · {latest.commit_sha.slice(0, 12)} · {new Date(latest.created_at).toLocaleString("zh-CN")}
            </span>
          </div>
          <ol className="m-0 grid list-none gap-2 p-0">
            {latest.steps.map((step) => (
              <li key={step.step_key} className="grid grid-cols-[24px_1fr] gap-2 border-b border-line py-2.5">
                <span>{step.status === "passed" ? "✓" : step.status === "failed" ? "×" : "!"}</span>
                <div>
                  <strong>{step.title}</strong>
                  <p className="mt-0.5 mb-0 text-[#7b8395]">{step.summary}</p>
                  {Object.keys(step.evidence).length ? <small className="mt-0.5 block text-[#7b8395]">{JSON.stringify(step.evidence)}</small> : null}
                </div>
              </li>
            ))}
          </ol>
          <div className="mt-4">
            <DataTable
              label="链路验收记录"
              rows={runs}
              rowKey={(run) => run.acceptance_run_id}
              columns={RUN_COLUMNS}
              emptyState={{ kind: "empty", title: "还没有链路验收记录。", description: "该知识库还没有运行过端到端链路验收。" }}
            />
          </div>
        </>
      ) : (
        <div className="rounded-lg border border-line bg-surface">
          <EmptyState kind="empty" title="还没有链路验收记录。" description="该知识库还没有运行过端到端链路验收。" />
        </div>
      )}
    </div>
  );
}
