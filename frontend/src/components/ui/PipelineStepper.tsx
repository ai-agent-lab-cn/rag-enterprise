import { cn } from "./cn";

type PipelineKind = "file_upload" | "file_update" | "sync_run" | "index_build" | string;

const PIPELINES: Record<string, Array<{ key: string; label: string; aliases?: string[] }>> = {
  file_upload: [
    { key: "upload", label: "上传", aliases: ["queued", "preparing"] },
    { key: "parse", label: "解析", aliases: ["parse", "parsing"] },
    { key: "chunk", label: "切片", aliases: ["chunk", "chunking"] },
    { key: "classify", label: "分类", aliases: ["classify", "classifying"] },
    { key: "vector", label: "向量" },
    { key: "keyword", label: "关键词" },
    { key: "metadata", label: "元数据", aliases: ["enrich", "enriching"] },
    { key: "validate", label: "验证", aliases: ["validate", "validating"] },
    { key: "complete", label: "完成", aliases: ["complete", "completed", "succeeded"] },
  ],
  file_update: [
    { key: "upload", label: "新版本", aliases: ["queued", "preparing"] },
    { key: "parse", label: "解析", aliases: ["parse", "parsing"] },
    { key: "chunk", label: "切片", aliases: ["chunk", "chunking"] },
    { key: "classify", label: "分类", aliases: ["classify", "classifying"] },
    { key: "vector", label: "向量" },
    { key: "keyword", label: "关键词" },
    { key: "metadata", label: "元数据", aliases: ["enrich", "enriching"] },
    { key: "validate", label: "验证", aliases: ["validate", "validating"] },
    { key: "activate", label: "切换版本", aliases: ["activate", "activating", "complete", "completed", "succeeded"] },
  ],
  document_reprocess: [
    { key: "parse", label: "解析", aliases: ["queued", "preparing", "parse", "parsing"] },
    { key: "chunk", label: "切片", aliases: ["chunk", "chunking"] },
    { key: "classify", label: "分类", aliases: ["classify", "classifying"] },
    { key: "vector", label: "向量" },
    { key: "keyword", label: "关键词" },
    { key: "metadata", label: "元数据", aliases: ["enrich", "enriching"] },
    { key: "validate", label: "验证", aliases: ["validate", "validating"] },
    { key: "complete", label: "完成", aliases: ["activate", "activating", "complete", "completed", "succeeded"] },
  ],
  sync_run: [
    { key: "discover", label: "发现", aliases: ["queued", "discover", "discovering", "diff"] },
    { key: "fetch", label: "获取", aliases: ["fetch", "fetching", "syncing"] },
    { key: "normalize", label: "规范化", aliases: ["normalize", "normalizing"] },
    { key: "parse", label: "解析", aliases: ["parse", "parsing"] },
    { key: "chunk", label: "切片", aliases: ["chunk", "chunking"] },
    { key: "enrich", label: "治理", aliases: ["enrich", "enriching"] },
    { key: "build", label: "构建", aliases: ["build", "building", "indexing"] },
    { key: "validate", label: "验证", aliases: ["validate", "validating"] },
    { key: "activate", label: "激活", aliases: ["activate", "activated", "activating", "complete", "completed", "succeeded"] },
  ],
  index_build: [
    { key: "prepare", label: "准备", aliases: ["queued", "preparing"] },
    { key: "vector", label: "向量" },
    { key: "keyword", label: "关键词" },
    { key: "metadata", label: "元数据" },
    { key: "validate", label: "验证", aliases: ["validate", "validating"] },
    { key: "complete", label: "完成", aliases: ["activate", "activating", "complete", "completed", "succeeded"] },
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

  return (
    <div className="min-w-0" aria-label={`${label}：${statusText}，${Math.round(progressPercent ?? 0)}%`}>
      <div className="flex min-w-0 items-center gap-2 overflow-hidden py-0.5">
        <div className="min-w-0 flex-1 overflow-x-auto">
          <ol className="flex min-w-max items-start" aria-hidden="true">
            {steps.map((step, index) => {
              const completed = succeeded || index < currentIndex;
              const current = index === currentIndex && !succeeded;
              const failureHere = current && failed;
              const retryHere = current && retrying;
              return (
                <li key={step.key} className="flex items-start">
                  <div className="grid w-11 justify-items-center gap-1">
                    <span className={cn(
                      "grid h-4 w-4 place-items-center rounded-full border text-[9px] font-bold",
                      completed && "border-success bg-success text-white",
                      current && !failureHere && !retryHere && !waiting && !cancelled && "border-brand bg-brand text-white",
                      failureHere && "border-danger bg-danger text-white",
                      retryHere && "border-warning bg-warning text-white",
                      (waiting || cancelled) && current && "border-line-firm bg-surface text-ink-faint",
                      !completed && !current && "border-line-firm bg-surface text-ink-faint",
                    )}>{completed ? "✓" : failureHere ? "!" : retryHere ? "↻" : current ? "●" : ""}</span>
                    <span className={cn("text-[10px] leading-3", current ? "font-medium text-ink" : "text-ink-faint")}>{step.label}</span>
                  </div>
                  {index < steps.length - 1 ? <span className={cn("mt-[1px] grid h-4 w-2 place-items-center text-[11px]", completed ? "text-success" : "text-ink-faint")}>→</span> : null}
                </li>
              );
            })}
          </ol>
        </div>
        {!failed ? <span className={cn(
          "shrink-0 whitespace-nowrap rounded-full px-1.5 py-0.5 text-[11px] font-medium",
          retrying ? "bg-warning/10 text-warning" : succeeded ? "bg-success-subtle text-success" : waiting || cancelled ? "bg-canvas text-ink-faint" : "bg-brand-subtle text-brand",
        )}>{statusText}</span> : null}
        <span className={cn(
          "w-11 shrink-0 rounded-full px-1.5 py-0.5 text-center text-[12px] font-semibold tabular-nums",
          failed ? "bg-danger-subtle text-danger-text" : retrying ? "bg-warning/10 text-warning" : succeeded ? "bg-success-subtle text-success" : waiting || cancelled ? "bg-canvas text-ink-faint" : "bg-brand-subtle text-brand",
        )}>{Math.round(progressPercent ?? 0)}%</span>
      </div>
      {failed ? <div className="mt-1 flex min-w-0 items-center gap-1.5">
        <span className="shrink-0 whitespace-nowrap rounded-full bg-danger-subtle px-1.5 py-0.5 text-[11px] font-medium text-danger-text">{statusText}</span>
        {failureReason ? <span className="min-w-0 truncate text-[11px] text-danger-text" title={failureReason}>{failureReason}</span> : null}
      </div> : null}
    </div>
  );
}
