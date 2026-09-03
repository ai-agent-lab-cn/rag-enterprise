import { useEffect, useState } from "react";
import { api } from "../api";
import type { GovernedBadCase } from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { type Column, DataTable } from "./ui/DataTable";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input } from "./ui/Input";
import { Select } from "./ui/Select";

const STATUS_TONE: Record<GovernedBadCase["status"], "neutral" | "success" | "warning" | "danger" | "brand"> = {
  new: "neutral",
  confirmed: "brand",
  fixing: "warning",
  resolved: "success",
  regression_added: "success",
  ignored: "neutral",
};
const SEVERITY_TONE: Record<GovernedBadCase["severity"], "neutral" | "success" | "warning" | "danger" | "brand"> = {
  critical: "danger",
  high: "warning",
  medium: "neutral",
  low: "neutral",
};

// 加载态占位列：表头文案与 BadCasePanel 的真实列一一对应，render 不会被调用
// （DataTable 在 rows===null 时只画 SkeletonRows），这里只是为了让表头在加载
// 完成前后保持一致，不产生跳动。
const LOADING_COLUMNS: Column<GovernedBadCase>[] = [
  { key: "question", header: "问题", truncate: false, render: () => null },
  { key: "failure_stage", header: "阶段", render: () => null },
  { key: "category", header: "分类", render: () => null },
  { key: "severity", header: "严重级别", truncate: false, render: () => null },
  { key: "status", header: "状态", truncate: false, render: () => null },
  { key: "governance", header: "治理", truncate: false, render: () => null },
];

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

  return (
    <section className="px-6 pt-5 pb-8" aria-label="Bad Case">
      {error ? (
        <ErrorBanner>{error}</ErrorBanner>
      ) : null}
      {busy ? (
        <div className="py-2 text-[12.8px] text-ink-muted" aria-live="polite">
          正在保存治理结果…
        </div>
      ) : null}
      {items ? (
        <BadCasePanel items={items} isAdmin={isAdmin} onUpdate={updateCase} />
      ) : (
        // 筛选条依赖已加载数据算出「失败阶段」选项，加载完成前不能先渲染一份空选项的
        // 筛选条——复用 DataTable 本身的加载态（rows=null 时它会画 SkeletonRows，且
        // 表头是真实的），不用再手写一份 <table>。DataTable 在 rows=null 时渲染的
        // <table> 本身不带 aria-hidden（只有骨架格子内部隐藏），这里外包一层
        // aria-hidden 保持与原实现一致：加载中的占位表格不应出现在无障碍树里，
        // 状态改由下面独立的 role="status" 承担。
        <>
          <div aria-hidden="true">
            <DataTable
              rows={null}
              columns={LOADING_COLUMNS}
              rowKey={(item) => item.case_id}
              label="Bad Case"
              emptyState={{
                kind: "empty",
                title: "还没有 Bad Case。",
                description: "Bad Case 由评测和线上问答的失败样本自动归集，暂时没有需要治理的记录。",
              }}
            />
          </div>
          <span role="status" className="sr-only">
            正在读取 Bad Case
          </span>
        </>
      )}
    </section>
  );
}

function BadCasePanel({
  items,
  isAdmin,
  onUpdate,
}: {
  items: GovernedBadCase[];
  isAdmin: boolean;
  onUpdate: (item: GovernedBadCase, update: Parameters<typeof api.updateGovernedBadCase>[1]) => void;
}) {
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");
  const [stage, setStage] = useState("");
  const [expandedCaseId, setExpandedCaseId] = useState<string | null>(null);
  const visible = items.filter((item) => (!status || item.status === status) && (!severity || item.severity === severity) && (!stage || item.failure_stage === stage));

  const columns: Column<GovernedBadCase>[] = [
    {
      key: "question",
      header: "问题",
      truncate: false,
      render: (item) => (
        <>
          <strong>{item.question}</strong>
          <small className="block text-ink-faint">{item.case_id}</small>
        </>
      ),
    },
    { key: "failure_stage", header: "阶段", render: (item) => item.failure_stage },
    { key: "category", header: "分类", render: (item) => item.category },
    {
      key: "severity",
      header: "严重级别",
      truncate: false,
      render: (item) => (
        <Badge tone={SEVERITY_TONE[item.severity]} shape="status">
          {item.severity}
        </Badge>
      ),
    },
    {
      key: "status",
      header: "状态",
      truncate: false,
      render: (item) => (
        <Badge tone={STATUS_TONE[item.status]} shape="status">
          {item.status}
        </Badge>
      ),
    },
    {
      key: "governance",
      header: "治理",
      truncate: false,
      render: (item) => (
        <button
          type="button"
          className="cursor-pointer text-brand"
          aria-expanded={expandedCaseId === item.case_id}
          onClick={() => setExpandedCaseId((current) => (current === item.case_id ? null : item.case_id))}
        >
          治理详情
        </button>
      ),
    },
  ];

  return (
    <div>
      <div className="flex justify-between gap-2 pb-2.5 text-[12.8px] text-ink-muted">
        <div className="flex gap-2">
          <label className="flex items-center gap-[5px]">
            <span className="shrink-0">状态</span>
            <Select size="sm" className="w-28" aria-label="Bad Case 状态筛选" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">全部</option>
              <option value="new">新建</option>
              <option value="confirmed">已确认</option>
              <option value="fixing">修复中</option>
              <option value="resolved">已解决</option>
              <option value="regression_added">已入回归集</option>
              <option value="ignored">已忽略</option>
            </Select>
          </label>
          <label className="flex items-center gap-[5px]">
            <span className="shrink-0">严重级别</span>
            <Select size="sm" className="w-20" aria-label="Bad Case 严重级别筛选" value={severity} onChange={(event) => setSeverity(event.target.value)}>
              <option value="">全部</option>
              <option value="critical">严重</option>
              <option value="high">高</option>
              <option value="medium">中</option>
              <option value="low">低</option>
            </Select>
          </label>
          <label className="flex items-center gap-[5px]">
            <span className="shrink-0">失败阶段</span>
            <Select size="sm" className="w-28" aria-label="Bad Case 失败阶段筛选" value={stage} onChange={(event) => setStage(event.target.value)}>
              <option value="">全部</option>
              {Array.from(new Set(items.map((item) => item.failure_stage))).map((value) => (
                <option value={value} key={value}>
                  {value}
                </option>
              ))}
            </Select>
          </label>
        </div>
        <span>
          {visible.length} / {items.length} 个案例 · 管理员治理，成员只读
        </span>
      </div>
      <DataTable
        rows={visible}
        columns={columns}
        rowKey={(item) => item.case_id}
        label="Bad Case"
        emptyState={
          items.length === 0
            ? {
                kind: "empty",
                title: "还没有 Bad Case。",
                description: "Bad Case 由评测和线上问答的失败样本自动归集，暂时没有需要治理的记录。",
              }
            : {
                kind: "filtered",
                title: "当前筛选范围没有 Bad Case。",
                description: "放宽状态、严重级别或失败阶段的筛选条件后重试。",
              }
        }
        expandedRow={(item) =>
          expandedCaseId === item.case_id ? <BadCaseGovernanceDetails item={item} isAdmin={isAdmin} onUpdate={onUpdate} /> : null
        }
      />
    </div>
  );
}

function BadCaseGovernanceDetails({
  item,
  isAdmin,
  onUpdate,
}: {
  item: GovernedBadCase;
  isAdmin: boolean;
  onUpdate: (item: GovernedBadCase, update: Parameters<typeof api.updateGovernedBadCase>[1]) => void;
}) {
  const [rootCause, setRootCause] = useState(item.root_cause ?? "");
  const [fixCommit, setFixCommit] = useState(item.fix_commit ?? "");
  const [assignee, setAssignee] = useState(item.assignee ?? "");
  const next = item.status === "new" ? "confirmed" : item.status === "confirmed" ? "fixing" : item.status === "fixing" ? "resolved" : item.status === "resolved" ? "regression_added" : null;
  const nextLabel = item.status === "new" ? "确认" : item.status === "confirmed" ? "开始修复" : item.status === "fixing" ? "标记已解决" : item.status === "resolved" ? "加入回归集" : "";
  return (
    <div className="w-full">
      <dl className="my-1.5">
        <div className="flex my-2">
          <dt className="text-[11.52px] text-ink-muted">期望状态：</dt>
          <dd className="ml-1">{item.expected_answer_status ?? "未标注"}</dd>
        </div>
        <div className="flex my-2">
          <dt className="text-[11.52px] text-ink-muted">实际状态：</dt>
          <dd className="ml-1">{item.actual_answer_status ?? "无"}</dd>
        </div>
        <div className="flex my-2">
          <dt className="text-[11.52px] text-ink-muted">实际回答：</dt>
          <dd className="ml-1">{item.actual_answer ?? "无"}</dd>
        </div>
      </dl>
      {isAdmin ? (
        <div className="mt-2 grid grid-cols-4 gap-8">
          <label className="flex items-center text-[11.52px] text-ink-muted" >
            <span className="whitespace-nowrap">根因：</span>
            <Input size="sm" value={rootCause} onChange={(event) => setRootCause(event.target.value)} />
          </label>
          <label className="flex items-center text-[11.52px] text-ink-muted">
            <span className="whitespace-nowrap">负责人：</span>
            <Input size="sm" value={assignee} onChange={(event) => setAssignee(event.target.value)} />
          </label>
          <label className="flex items-center text-[11.52px] text-ink-muted">
            <span className="whitespace-nowrap">修复 Commit：</span>
            <Input size="sm" value={fixCommit} onChange={(event) => setFixCommit(event.target.value)} />
          </label>
          <div className="flex gap-1">
            {next ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  onUpdate(item, {
                    status: next,
                    severity: item.severity,
                    root_cause: rootCause || undefined,
                    assignee: assignee || undefined,
                    fix_commit: fixCommit || undefined,
                    regression_passed: next === "regression_added" ? true : undefined,
                  })
                }
              >
                {nextLabel}
              </Button>
            ) : null}
            {item.status !== "ignored" && item.status !== "regression_added" ? (
              <Button
                variant="ghost"
                size="sm"
                className="text-danger-text hover:bg-danger-subtle"
                onClick={() => onUpdate(item, { status: "ignored", severity: item.severity })}
              >
                忽略
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
