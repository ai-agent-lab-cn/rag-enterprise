import { useEffect, useState } from "react";
import { api } from "../api";
import type { AnswerEvaluationMetric, AnswerEvaluationReport, AnswerEvaluationSummary } from "../types";
import { Badge } from "./ui/Badge";
import { EmptyState } from "./ui/EmptyState";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Select } from "./ui/Select";
import { Skeleton } from "./ui/Skeleton";

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

/**
 * 指标卡：数值 + 阈值进度条 + 通过态。
 *
 * 没有直接用 `ui/MetricCard`——它只有 icon/label/value/note 四个槽位，容不下阈值进度条
 * 和上限/下限判定。数值字阶沿用它的规范（`tabular-nums`），卡片结构是本页专属的，
 * 和 `EvaluationPage.tsx` 里的同名组件是两份独立实现（字段语义不同：这里按方向
 * minimum/maximum 判定进度条填充方式，那边是固定的召回率填充）。
 */
function Metric({ name, metric }: { name: string; metric: AnswerEvaluationMetric }) {
  return (
    <article className="rounded-[7px] border border-line bg-[#fafbfe] p-[9px_10px]">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] leading-4 text-[#6e768a]">{LABELS[name] ?? name}</span>
        <strong className="text-[16px] leading-5 tabular-nums text-[#252d43]">{(metric.value * 100).toFixed(0)}%</strong>
      </div>
      <div className="relative mt-[7px] mb-[5px] h-1 rounded-full bg-[#eceef4]">
        <span
          className="block h-full rounded-[inherit] bg-[#6558d8]"
          style={{
            width: `${metric.direction === "maximum" ? (1 - metric.value) * 100 : metric.value * 100}%`,
          }}
        />
        <span className="absolute -top-[3px] h-[11px] w-[2px] bg-[#f19a36]" style={{ left: `${metric.threshold * 100}%` }} />
      </div>
      <div className="flex items-center gap-1.5 text-[9px] leading-[14px] text-[#8c93a5]">
        <span>
          {metric.direction === "maximum" ? "上限" : "下限"} {(metric.threshold * 100).toFixed(0)}%
        </span>
        <Badge className="ml-auto" tone={metric.passed ? "success" : "danger"} shape="status">
          {metric.passed ? "通过" : "未通过"}
        </Badge>
      </div>
    </article>
  );
}

function MetricSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-[10px] min-[901px]:grid-cols-2">
      <Skeleton className="h-[150px] rounded-[9px]" />
      <Skeleton className="h-[150px] rounded-[9px]" />
    </div>
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
    <section className="px-3 pt-3.5 pb-7 min-[561px]:px-5 min-[561px]:pt-4 min-[561px]:pb-6 min-[1025px]:px-5 min-[1025px]:pt-5 min-[1025px]:pb-10" aria-label="回答评测">
      {/* 曾经是页面级徽章放顶栏；改成 Section 后，顶栏只留统一质量门，分项结论回到各自小节。 */}
      {report ? (
        <Badge tone={report.passed ? "success" : "danger"} shape="status">
          {report.passed ? "回答质量门已通过" : "回答质量门未通过"}
        </Badge>
      ) : null}
      {error ? (
        <ErrorBanner>{error}</ErrorBanner>
      ) : null}
      {reports === null && !error ? (
        <div className="grid gap-3">
          <span role="status" className="sr-only">
            正在读取正式回答评测
          </span>
          <Skeleton className="h-7 w-64" />
          <MetricSkeleton />
        </div>
      ) : null}
      {reports?.length === 0 ? (
        <div className="rounded-lg border border-line bg-surface">
          <EmptyState kind="empty" title="还没有正式回答评测报告" description="页面只读取人工复核后放行的正式报告。" />
        </div>
      ) : null}
      {reports?.length ? (
        <label className="mb-[10px] flex flex-col items-stretch gap-2 text-[11px] text-[#737b8e] min-[561px]:flex-row min-[561px]:items-center">
          评测运行
          <Select
            size="sm"
            className="w-90"
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
          </Select>
        </label>
      ) : null}
      {report ? (
        <div className="grid">
          <div className="grid grid-cols-1 overflow-hidden rounded-[10px] border border-line bg-surface min-[561px]:grid-cols-4">
            <div className="flex min-w-0 flex-col gap-[3px] border-r border-line p-[9px_12px] last:border-r-0">
              <span className="text-[9px] text-[#9299ab]">数据版本</span>
              <strong className="truncate text-[11px] text-[#263047]">{report.dataset_version}</strong>
            </div>
            <div className="flex min-w-0 flex-col gap-[3px] border-r border-line p-[9px_12px] last:border-r-0">
              <span className="text-[9px] text-[#9299ab]">评测样本</span>
              <strong className="truncate text-[11px] text-[#263047]">{report.case_count} 条</strong>
            </div>
            <div className="flex min-w-0 flex-col gap-[3px] border-r border-line p-[9px_12px] last:border-r-0">
              <span className="text-[9px] text-[#9299ab]">Prompt</span>
              <strong className="truncate text-[11px] text-[#263047]">{report.prompt_version}</strong>
            </div>
            <div className="flex min-w-0 flex-col gap-[3px] border-r border-line p-[9px_12px] last:border-r-0">
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
          <div className="my-[10px] grid grid-cols-1 gap-[10px] min-[901px]:grid-cols-2">
            {METRIC_GROUPS.map((group) => (
              <section className="min-w-0 rounded-[9px] border border-line bg-surface p-[11px_12px]" key={group.title}>
                <header className="mb-2 flex items-start justify-between gap-2.5 min-[561px]:items-center">
                  <div className="min-w-0">
                    {/* h3 目前没有匹配的 CSS 规则（历史遗留选择器写的是 h2），实际渲染的一直是
                        浏览器默认 h3 样式（16.38px / 700 / 上下各 16.38px margin）。这里显式
                        写出这份「本来就是默认值」的样式，只是为了在未来打开 preflight 时
                        不出现视觉回归，不是新增设计。 */}
                    <h3 className="my-[16.38px] text-[16.38px] font-bold">{group.title}</h3>
                    <p className="mt-[1px] mb-0 overflow-hidden whitespace-normal text-ellipsis text-[10px] leading-[15px] text-[#788195] min-[561px]:whitespace-nowrap">
                      {group.description}
                    </p>
                  </div>
                  <span className="shrink-0 rounded-full bg-[#f0eeff] px-[7px] py-[3px] text-[9px] text-[#6659cf]">
                    {group.metrics.length} 项指标
                  </span>
                </header>
                <div className="grid grid-cols-1 gap-[7px] min-[561px]:grid-cols-2">
                  {group.metrics.map((name) => {
                    const metric = report.metrics[name];
                    return metric ? <Metric key={name} name={name} metric={metric} /> : null;
                  })}
                </div>
              </section>
            ))}
          </div>
          <div className="grid grid-cols-1 gap-[10px] min-[561px]:grid-cols-[1.2fr_0.8fr]">
            <section className="min-w-0 rounded-[9px] border border-line bg-surface p-[10px_12px]">
              <span className="text-[12px] text-[#7165d8] tracking-[0.04em] font-semibold">模型标识</span>
              {Object.entries(report.models).map(([key, value]) => (
                <p key={key} className="mt-[5px] mb-0 flex items-center gap-2">
                  <b className="w-[72px] shrink-0 text-[9px] text-[#848b9e]">{key}</b>
                  <code className="truncate text-[9px] text-[#3c4458]">{value}</code>
                </p>
              ))}
            </section>
            <section className="min-w-0 rounded-[9px] border border-line bg-surface p-[10px_12px]">
              <span className="text-[12px] text-[#7165d8] tracking-[0.04em] font-semibold">可复现上下文</span>
              <dl className="mt-1 mb-0">
                <div className="flex justify-between gap-2.5 py-0.5">
                  <dt className="m-0 text-[9px] text-[#747c90]">Prompt Hash</dt>
                  <dd className="m-0 text-[9px] text-[#747c90]" title={report.prompt_hash}>
                    {report.prompt_hash.slice(0, 12)}
                  </dd>
                </div>
                {Object.entries(report.parameters).map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-2.5 py-0.5">
                    <dt className="m-0 text-[9px] text-[#747c90]">{key}</dt>
                    <dd className="m-0 text-[9px] text-[#747c90]">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          </div>
          <p className="mt-2 mb-0 text-center text-[9px] text-[#7e879a]">
            此页面不会启动模型评测；展示的是与数据版本和 commit 绑定的正式报告。
          </p>
        </div>
      ) : null}
    </section>
  );
}
