import { ProgressSteps } from "./ProgressSteps";
import type { ProgressStep, ProgressStepState } from "./ProgressSteps";
import { cn } from "./cn";

type PipelineKind =
  | "file_upload"
  | "file_update"
  | "sync_run"
  | "index_build"
  | "index_evaluation"
  | string;

const PIPELINES: Record<string, Array<{ key: string; label: string; aliases?: string[] }>> = {
  // 每条流水线的格子必须对应后端真实写入的 current_stage。多出来的格子不会显示成
  // 「未开始」——PipelineStepper 匹配不到 stage 时会退回按 progressPercent 猜位置
  // （见下面 currentIndex 的 inferredIndex 分支），于是进度条看着精确，其实是在猜。
  // V25 之前 sync_run 有五个格子（parse/chunk/enrich/validate/activate）后端从来不写，
  // 而后端写的 fetch_or_normalize / size_limit / retry_unavailable 前端一个都接不住。
  // `backend/tests/test_module_boundaries.py` 里有一条守卫比对两边，加阶段不加别名会红。
  file_upload: [
    { key: "upload", label: "上传", aliases: ["queued", "preparing"] },
    { key: "parse", label: "解析", aliases: ["parse", "parsing"] },
    { key: "chunk", label: "切片", aliases: ["chunk", "chunking"] },
    { key: "index", label: "建索引", aliases: ["vector", "keyword", "metadata", "build", "building"] },
    { key: "validate", label: "验证", aliases: ["validate", "validating"] },
    { key: "complete", label: "完成", aliases: ["complete", "completed", "succeeded"] },
  ],
  file_update: [
    { key: "upload", label: "新版本", aliases: ["queued", "preparing"] },
    { key: "parse", label: "解析", aliases: ["parse", "parsing"] },
    { key: "chunk", label: "切片", aliases: ["chunk", "chunking"] },
    { key: "index", label: "建索引", aliases: ["vector", "keyword", "metadata", "build", "building"] },
    { key: "validate", label: "验证", aliases: ["validate", "validating"] },
    { key: "complete", label: "完成", aliases: ["complete", "completed", "succeeded", "activate", "activating"] },
  ],
  document_reprocess: [
    { key: "parse", label: "解析", aliases: ["queued", "preparing", "parse", "parsing"] },
    { key: "chunk", label: "切片", aliases: ["chunk", "chunking"] },
    { key: "index", label: "建索引", aliases: ["vector", "keyword", "metadata", "build", "building"] },
    { key: "validate", label: "验证", aliases: ["validate", "validating"] },
    { key: "complete", label: "完成", aliases: ["complete", "completed", "succeeded", "activate", "activating"] },
  ],
  // 同步只负责「发现差异并把变化对象交给索引链路」，本身不解析、不切片、不激活——
  // 那些阶段发生在各自独立的 index 任务里，不会写进 sync_run 的 operations 行。
  sync_run: [
    { key: "discover", label: "发现", aliases: ["queued", "discover", "discovering", "diff"] },
    { key: "fetch", label: "获取", aliases: ["fetch", "fetching", "syncing", "fetch_or_normalize", "retry_wait", "size_limit"] },
    { key: "normalize", label: "规范化", aliases: ["normalize", "normalizing"] },
    { key: "build", label: "交付索引", aliases: ["build", "building", "indexing", "retry_unavailable"] },
    { key: "complete", label: "完成", aliases: ["complete", "completed", "succeeded", "complete_with_failures", "deleted", "unchanged", "skipped"] },
  ],
  index_build: [
    { key: "prepare", label: "准备", aliases: ["queued", "preparing"] },
    { key: "parse", label: "解析", aliases: ["parse", "parsing"] },
    { key: "chunk", label: "切片", aliases: ["chunk", "chunking"] },
    { key: "index", label: "建索引", aliases: ["vector", "keyword", "metadata", "build", "building"] },
    { key: "validate", label: "验证", aliases: ["validate", "validating"] },
    { key: "activate", label: "激活", aliases: ["active", "activate", "activating", "complete", "completed", "succeeded"] },
  ],
  // 正式检索评测。格子与 backend/app/index_evaluation_runs.py 的 EVALUATION_STAGES
  // 逐字对应——Evaluation Worker 按序写入这些 current_stage。
  index_evaluation: [
    { key: "prepare_dataset", label: "备数据集", aliases: ["queued", "prepare_dataset"] },
    { key: "build_corpus", label: "建语料", aliases: ["build_corpus"] },
    { key: "retrieve", label: "召回", aliases: ["retrieve"] },
    { key: "rerank", label: "精排", aliases: ["rerank"] },
    { key: "calculate_metrics", label: "算指标", aliases: ["calculate_metrics"] },
    { key: "persist_report", label: "出报告", aliases: ["persist_report"] },
    { key: "complete", label: "完成", aliases: ["complete", "completed", "succeeded"] },
  ],
};

const TERMINAL_SUCCESS = new Set(["succeeded", "completed", "complete"]);
const TERMINAL_FAILURE = new Set(["failed", "aborted", "partial_failed"]);
const RETRY = new Set(["retry", "retry_wait", "retrying"]);
const WAITING = new Set(["idle", "queued", "pending"]);
const CANCELLED = new Set(["cancelled", "canceled"]);

export function PipelineStepper({
  kind,
  currentStage,
  status,
  progressPercent = 0,
  label,
  failureReason,
}: {
  kind: PipelineKind;
  currentStage?: string | null;
  status: string;
  progressPercent?: number | null;
  label: string;
  failureReason?: string | null;
}) {
  const steps = PIPELINES[kind] ?? [
    { key: "queued", label: "等待", aliases: ["queued", "preparing"] },
    { key: "running", label: "处理中", aliases: ["running"] },
    { key: "complete", label: "完成", aliases: ["complete", "completed", "succeeded"] },
  ];
  // V25 以前的失败任务会把真实阶段覆盖成 failed；“没有可索引文本”发生在解析阶段，
  // 这里为历史记录恢复明确语义。新任务由后端直接保留真实 current_stage。
  const stage = currentStage === "failed" && failureReason?.includes("没有可索引的文本") ? "parsing" : currentStage ?? "";
  const succeeded = TERMINAL_SUCCESS.has(status) || TERMINAL_SUCCESS.has(stage);
  const failed = TERMINAL_FAILURE.has(status) || TERMINAL_FAILURE.has(stage);
  const retrying = RETRY.has(status) || RETRY.has(stage);
  const waiting = WAITING.has(status) || WAITING.has(stage);
  const cancelled = CANCELLED.has(status) || CANCELLED.has(stage);
  const explicitIndex = steps.findIndex((step) => step.key === stage || step.aliases?.includes(stage));
  const inferredIndex = Math.min(steps.length - 1, Math.max(0, Math.floor(((progressPercent ?? 0) / 100) * steps.length)));
  const currentIndex = succeeded ? steps.length - 1 : explicitIndex >= 0 ? explicitIndex : inferredIndex;
  const failedStageLabel = steps[currentIndex]?.label ?? "处理";
  const statusText = succeeded ? "已完成" : failed ? `${failedStageLabel}失败` : retrying ? "等待重试" : cancelled ? "已取消" : waiting ? (status === "idle" ? "未开始" : "等待处理") : "处理中";

  // 业务状态仍在这里推导，只把最终的步骤数组交给共享的视觉 primitive。
  const progressSteps: ProgressStep[] = steps.map((step, index) => {
    const completed = succeeded || index < currentIndex;
    const current = index === currentIndex && !succeeded;
    let state: ProgressStepState = "todo";
    if (completed) state = "done";
    else if (current && failed) state = "failed";
    else if (current && retrying) state = "retrying";
    else if (current && !waiting && !cancelled) state = "current";
    return { key: step.key, label: step.label, state };
  });

  return (
    <div className="min-w-0" aria-label={`${label}：${statusText}，${Math.round(progressPercent ?? 0)}%`}>
      <ProgressSteps
        steps={progressSteps}
        label={label}
        compact
        // 整条对读屏隐藏：一行表格里念七个格子读屏用户听不完，语义由外层那一句
        // 完整可访问名承担。
        describeSteps={false}
        showPercent={progressPercent ?? 0}
        className="overflow-hidden"
        trailing={!failed ? <span className={cn(
          "shrink-0 whitespace-nowrap rounded-full px-1.5 py-0.5 text-[11px] font-medium",
          retrying ? "bg-warning/10 text-warning" : succeeded ? "bg-success-subtle text-success" : waiting || cancelled ? "bg-canvas text-ink-faint" : "bg-brand-subtle text-brand",
        )}>{statusText}</span> : null}
      />
      {failed ? <div className="mt-1 flex min-w-0 items-center gap-1.5">
        <span className="shrink-0 whitespace-nowrap rounded-full bg-danger-subtle px-1.5 py-0.5 text-[11px] font-medium text-danger-text">{statusText}</span>
        {failureReason ? <span className="min-w-0 truncate text-[11px] text-danger-text" title={failureReason}>{failureReason}</span> : null}
      </div> : null}
    </div>
  );
}
