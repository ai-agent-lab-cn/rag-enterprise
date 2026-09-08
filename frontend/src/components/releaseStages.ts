import type { EvaluationReportSummary, IndexVersion, KnowledgeBase } from "../types";
import type { ReleaseStage } from "./ui/ReleaseFlow";

/** 候选版本：还在走发布流程的那一个。active/previous/retired/cleaned 都已离开流程。 */
export const CANDIDATE_STATUSES = new Set(["building", "validating", "ready", "build_failed", "validation_failed"]);

/**
 * 把「索引版本状态 + 有没有可用质量报告」推导成发布流程的 7 个阶段。
 *
 * 阶段划分来自实施计划第 5 节，其中**正式评测与发布验证必须是两个阶段**——正式检索评测
 * 产出质量报告，发布验证再拿这份报告做三层门禁。合成一个格子就没法表达
 * 「验证卡住是因为缺报告」这个最常见的原因。
 *
 * 只读 `index_versions.status`（迁移 0031 的 CHECK 约束里那 9 个取值）与
 * `evaluationReports`，不猜进度：拿不到判断依据的阶段一律给 `todo`，不给「大概在这里」。
 */
export function releaseStages(
  versions: IndexVersion[],
  reports: EvaluationReportSummary[],
  drift: KnowledgeBase["index_config_drift"],
): ReleaseStage[] {
  const active = versions.find((item) => item.status === "active") ?? null;
  const candidate = versions.find((item) => CANDIDATE_STATUSES.has(item.status)) ?? null;
  const status = candidate?.status;
  const hasReport = Boolean(
    candidate && reports.some((report) => report.passed && report.config_fingerprint === candidate.config_fingerprint),
  );

  const definition: ReleaseStage = {
    key: "definition",
    label: "索引定义",
    state: "done",
    note: drift.length ? `配置已更新 ${drift.length} 项，新建索引版本后生效` : undefined,
  };
  const version: ReleaseStage = candidate
    ? { key: "version", label: "版本", state: "done" }
    : { key: "version", label: "版本", state: "todo", note: "没有正在发布的候选版本" };

  const build: ReleaseStage = { key: "build", label: "构建", state: "todo" };
  if (status === "building") build.state = "current";
  else if (status === "build_failed") { build.state = "failed"; build.note = "构建失败，可重新构建"; }
  else if (candidate) build.state = "done";

  const evaluation: ReleaseStage = { key: "evaluation", label: "正式评测", state: "todo" };
  if (hasReport) evaluation.state = "done";
  else if (candidate && status !== "building" && status !== "build_failed") {
    evaluation.state = "blocked";
    evaluation.note = "缺少可用于发布的正式质量报告";
  }

  const validation: ReleaseStage = { key: "validation", label: "发布验证", state: "todo" };
  if (status === "validating") validation.state = "current";
  else if (status === "validation_failed") { validation.state = "failed"; validation.note = "部分发布条件未满足，可重新验证"; }
  else if (status === "ready") validation.state = "done";
  // 只有构建已经过去、单纯缺报告时才标「需要处理」。构建中或构建失败时这里必须是「未开始」——
  // 否则页面同时喊「构建失败」和「缺少质量报告」，用户不知道先处理哪个。实测过这个组合：
  // 真实数据下曾出现「构建 × → 正式评测 ○ → 发布验证 !」，三个格子互相矛盾。
  else if (candidate && !hasReport && status !== "building" && status !== "build_failed") {
    validation.state = "blocked";
    validation.note = "等待前置条件：正式质量报告";
  }

  const pending: ReleaseStage = status === "ready"
    ? { key: "pending", label: "待激活", state: "current", note: "验证已通过，激活需要手动执行" }
    : { key: "pending", label: "待激活", state: "todo" };

  const live: ReleaseStage = active
    ? { key: "live", label: "生效", state: "done", note: `当前线上版本 ${active.version_no ? `v${active.version_no}` : active.index_version_id}` }
    : { key: "live", label: "生效", state: "todo", note: "还没有线上生效的索引版本" };

  return [definition, version, build, evaluation, validation, pending, live];
}
