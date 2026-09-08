import { cn } from "./cn";

/**
 * 索引版本的发布流程摘要。
 *
 * 回答实施计划第 49 节那 4 个问题里的两个——「新版本进行到哪一步」「为什么还不能发布」。
 *
 * **这不是 `ui/PipelineStepper`**：那个画的是单个 operation 的 `current_stage`
 * （后端真实写入的阶段，`test_module_boundaries.py` 有守卫比对两边），阶段来自任务；
 * 这里画的是**索引版本的生命周期**，阶段来自 `index_versions.status` 加上「有没有可用的
 * 正式质量报告」这类跨实体判断。两者的格子含义不同，混用会让其中一个的进度是猜出来的。
 *
 * 状态符号沿用实施计划第 5 节的约定：
 * `✓ 已完成` `● 进行中` `○ 未开始` `! 需要处理` `× 未通过`
 */
export type ReleaseStageState = "done" | "current" | "todo" | "blocked" | "failed";

const MARK: Record<ReleaseStageState, string> = {
  done: "✓",
  current: "●",
  todo: "○",
  blocked: "!",
  failed: "×",
};

const TONE: Record<ReleaseStageState, string> = {
  done: "text-success",
  current: "text-brand",
  todo: "text-ink-faint",
  blocked: "text-warning",
  failed: "text-danger-text",
};

export interface ReleaseStage {
  key: string;
  label: string;
  state: ReleaseStageState;
  /** 该阶段为什么停在这里。挂在标记的 title 上，并作为可访问名的一部分。 */
  note?: string;
}

export function ReleaseFlow({ stages, className }: { stages: ReleaseStage[]; className?: string }) {
  return (
    <ol
      // 横向可滚动而不是换行：窄屏下换行会让「→」指向错位，读起来像两条独立流程。
      className={cn("m-0 flex list-none items-start gap-0 overflow-x-auto p-0", className)}
      aria-label="发布流程"
    >
      {stages.map((stage, index) => (
        <li key={stage.key} className="flex shrink-0 items-start">
          {index > 0 ? <span aria-hidden className="mt-1.5 px-2 text-xs text-ink-faint">→</span> : null}
          <span className="grid justify-items-center gap-0.5 px-1">
            <span className="whitespace-nowrap text-sm text-ink-muted">{stage.label}</span>
            <span
              className={cn("text-base leading-none", TONE[stage.state])}
              title={stage.note || undefined}
              // 符号本身对读屏没有意义，把状态和原因念出来。
              aria-label={`${stage.label}：${STATE_LABEL[stage.state]}${stage.note ? `，${stage.note}` : ""}`}
              role="img"
            >
              {MARK[stage.state]}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}

const STATE_LABEL: Record<ReleaseStageState, string> = {
  done: "已完成",
  current: "进行中",
  todo: "未开始",
  blocked: "需要处理",
  failed: "未通过",
};
