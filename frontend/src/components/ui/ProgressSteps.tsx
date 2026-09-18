import type { ReactNode } from "react";
import { Tooltip } from "./Tooltip";
import { cn } from "./cn";

/**
 * 步骤条的视觉 primitive。
 *
 * 任务进度（`PipelineStepper`）与发布流程（`ReleaseFlow`）此前是两套画法：前者是
 * 16px 圆点 + 连线 + 紧凑标签，后者是一排裸符号。同一个页面上「构建到第几步」和
 * 「发布走到哪一格」长得完全不同，用户得分别学两次。
 *
 * **这个组件只渲染传进来的状态，不推导任何业务状态。** 阶段从哪来、为什么是这个状态，
 * 仍然分别由两个调用方决定——那正是它们必须分开的原因（任务阶段来自后端写入的
 * `current_stage`，发布阶段来自 `index_versions.status` 加跨实体判断）。
 */
export type ProgressStepState = "done" | "current" | "todo" | "blocked" | "failed" | "retrying";

const MARK: Record<ProgressStepState, string> = {
  done: "✓",
  current: "●",
  todo: "",
  blocked: "!",
  failed: "×",
  retrying: "↻",
};

/** 状态必须有文字，不能只靠颜色（UI 基线：状态不能只靠颜色）。 */
export const PROGRESS_STATE_LABEL: Record<ProgressStepState, string> = {
  done: "已完成",
  current: "进行中",
  todo: "未开始",
  blocked: "需要处理",
  failed: "未通过",
  retrying: "等待重试",
};

const DOT_TONE: Record<ProgressStepState, string> = {
  done: "border-success bg-success text-white",
  current: "border-brand bg-brand text-white",
  todo: "border-line-firm bg-surface text-ink-faint",
  blocked: "border-warning bg-warning text-white",
  failed: "border-danger bg-danger text-white",
  retrying: "border-warning bg-warning text-white",
};

export interface ProgressStep {
  key: string;
  label: string;
  state: ProgressStepState;
  /** 该阶段为什么停在这里。会进可访问名，并通过 Tooltip 在悬停与键盘聚焦时显示。 */
  note?: string;
}

export function ProgressSteps({
  steps,
  label,
  compact = false,
  showPercent,
  describeSteps = true,
  trailing,
  className,
}: {
  steps: ProgressStep[];
  /** 整条步骤条的可访问名。 */
  label: string;
  /** 紧凑模式：格子定宽 44px、标签 10px，用于表格行内的任务进度。 */
  compact?: boolean;
  /** 传入时在右侧显示百分比胶囊；null 与 undefined 都不显示。 */
  showPercent?: number | null;
  /**
   * 每个格子是否对读屏单独发声。
   *
   * `false` 用于调用方已经在外层给出一句完整可访问名的场景（任务进度就是这样：
   * 一行表格里念七个格子只会让读屏用户听不完），此时整条 `ol` 对读屏隐藏。
   */
  describeSteps?: boolean;
  /** 右侧附加内容（状态胶囊等），由调用方决定。 */
  trailing?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex min-w-0 items-center gap-2 py-0.5", className)}>
      <div className="min-w-0 flex-1 overflow-x-auto">
        <ol
          // 横向可滚动而不是换行：窄屏下换行会让「→」指向错位，读起来像两条独立流程。
          className="flex min-w-max list-none items-start p-0"
          aria-label={describeSteps ? label : undefined}
          aria-hidden={describeSteps ? undefined : true}
        >
          {steps.map((step, index) => {
            const accessibleName = `${step.label}：${PROGRESS_STATE_LABEL[step.state]}${
              step.note ? `，${step.note}` : ""
            }`;
            // Tooltip 的 trigger 必须自己可聚焦：note 此前只挂在 title 上，
            // 悬停约一秒才出现，触屏上完全看不到，键盘用户永远看不到（CLAUDE.md 第一条）。
            const dot = (
              <span
                className={cn(
                  "grid h-4 w-4 place-items-center rounded-full border text-[9px] font-bold",
                  "focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand/20",
                  DOT_TONE[step.state],
                )}
                {...(describeSteps
                  ? { role: "img", "aria-label": accessibleName, tabIndex: step.note ? 0 : undefined }
                  : { "aria-hidden": true })}
              >
                {MARK[step.state]}
              </span>
            );
            return (
              <li key={step.key} className="flex shrink-0 items-start">
                <div
                  className={cn(
                    "grid justify-items-center gap-1",
                    compact ? "w-11" : "min-w-11 px-1",
                  )}
                >
                  {step.note && describeSteps ? (
                    <Tooltip content={step.note} delay={0}>
                      {dot}
                    </Tooltip>
                  ) : (
                    dot
                  )}
                  <span
                    className={cn(
                      "whitespace-nowrap leading-3",
                      compact ? "text-[10px]" : "text-[11px]",
                      step.state === "current" ? "font-medium text-ink" : "text-ink-faint",
                    )}
                  >
                    {step.label}
                  </span>
                </div>
                {index < steps.length - 1 ? (
                  <span
                    aria-hidden
                    className={cn(
                      "mt-[1px] grid h-4 w-2 place-items-center text-[11px]",
                      step.state === "done" ? "text-success" : "text-ink-faint",
                    )}
                  >
                    →
                  </span>
                ) : null}
              </li>
            );
          })}
        </ol>
      </div>
      {trailing}
      {showPercent === undefined || showPercent === null ? null : (
        <span
          className={cn(
            "w-11 shrink-0 rounded-full px-1.5 py-0.5 text-center text-[12px] font-semibold tabular-nums",
            percentTone(steps),
          )}
        >
          {Math.round(showPercent)}%
        </span>
      )}
    </div>
  );
}

function percentTone(steps: ProgressStep[]): string {
  if (steps.some((step) => step.state === "failed")) return "bg-danger-subtle text-danger-text";
  if (steps.some((step) => step.state === "retrying")) return "bg-warning/10 text-warning";
  if (steps.length > 0 && steps.every((step) => step.state === "done")) {
    return "bg-success-subtle text-success";
  }
  if (steps.some((step) => step.state === "current")) return "bg-brand-subtle text-brand";
  return "bg-canvas text-ink-faint";
}
