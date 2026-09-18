import { ProgressSteps } from "./ProgressSteps";
import type { ProgressStep, ProgressStepState } from "./ProgressSteps";

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
 * 两者现在共用 `ui/ProgressSteps` 这一份**视觉**实现（16px 圆点、连线、成功/警告/失败
 * 色、紧凑标签），但**状态推导仍各自独立**——共享的是画法，不是判断。
 */
export type ReleaseStageState = ProgressStepState;

export interface ReleaseStage {
  key: string;
  label: string;
  state: ReleaseStageState;
  /**
   * 该阶段为什么停在这里。
   *
   * 它进可访问名，并由 `ProgressSteps` 用 `delay={0}` 的 Tooltip 呈现——此前只挂在
   * `title` 上，悬停约一秒才出现，触屏看不到、键盘用户永远看不到（CLAUDE.md 第一条）。
   */
  note?: string;
}

export function ReleaseFlow({ stages, className }: { stages: ReleaseStage[]; className?: string }) {
  const steps: ProgressStep[] = stages.map((stage) => ({
    key: stage.key,
    label: stage.label,
    state: stage.state,
    note: stage.note,
  }));
  return <ProgressSteps steps={steps} label="发布流程" className={className} />;
}
