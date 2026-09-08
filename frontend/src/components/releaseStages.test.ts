import { expect, test } from "vitest";
import { releaseStages } from "./releaseStages";
import type { EvaluationReportSummary, IndexVersion, KnowledgeBase } from "../types";

/**
 * 发布流程摘要的阶段推导。
 *
 * 值得单独测是因为它有七个格子、五种状态，且每个格子的判据来自不同实体
 * （`index_versions.status` + 质量报告的配置指纹 + 知识库的配置漂移）。
 * 浏览器实测已经抓到过一次自相矛盾的组合：「构建 × → 正式评测 ○ → 发布验证 !」，
 * 三个格子同时喊话，用户不知道先处理哪个。
 */

const FINGERPRINT = "a".repeat(64);

function version(overrides: Partial<IndexVersion>): IndexVersion {
  return {
    index_version_id: "iv_1",
    version_no: 1,
    status: "active",
    creation_reason: "manual_rebuild",
    config_completeness: "complete",
    chunking_version: "v1-700-100",
    parser_version: "1.0",
    embedding_model: "text2vec",
    embedding_dimension: 768,
    processing_options: {},
    config_fingerprint: FINGERPRINT,
    validation_report_id: null,
    evaluation_report_id: null,
    rebuild_batch_id: null,
    release_fingerprint: null,
    created_at: "2026-09-01T00:00:00Z",
    activated_at: null,
    retired_at: null,
    ...overrides,
  } as IndexVersion;
}

function report(overrides: Partial<EvaluationReportSummary> = {}): EvaluationReportSummary {
  return {
    report_id: "qr_1",
    dataset_id: "ds",
    dataset_version: "1.0",
    commit: "abc",
    run_at: "2026-09-01T00:00:00Z",
    models: {},
    passed: true,
    config_fingerprint: FINGERPRINT,
    ...overrides,
  };
}

const NO_DRIFT: KnowledgeBase["index_config_drift"] = [];

/** 取某个格子的状态，断言时比数组下标可读。 */
const stateOf = (stages: ReturnType<typeof releaseStages>, key: string) =>
  stages.find((item) => item.key === key)?.state;
const noteOf = (stages: ReturnType<typeof releaseStages>, key: string) =>
  stages.find((item) => item.key === key)?.note;

test("没有任何版本时，只有索引定义算完成", () => {
  const stages = releaseStages([], [], NO_DRIFT);
  expect(stateOf(stages, "definition")).toBe("done");
  expect(stateOf(stages, "version")).toBe("todo");
  expect(stateOf(stages, "build")).toBe("todo");
  expect(stateOf(stages, "live")).toBe("todo");
});

test("构建失败时，发布验证必须是未开始而不是「缺报告」", () => {
  // 这正是浏览器实测抓到的矛盾组合：构建都失败了还提示缺质量报告，
  // 用户不知道该先重新构建还是先跑评测。
  const stages = releaseStages([version({ index_version_id: "iv_c", status: "build_failed" })], [], NO_DRIFT);
  expect(stateOf(stages, "build")).toBe("failed");
  expect(stateOf(stages, "evaluation")).toBe("todo");
  expect(stateOf(stages, "validation")).toBe("todo");
});

test("构建中同理：不提前催质量报告", () => {
  const stages = releaseStages([version({ index_version_id: "iv_c", status: "building" })], [], NO_DRIFT);
  expect(stateOf(stages, "build")).toBe("current");
  expect(stateOf(stages, "evaluation")).toBe("todo");
  expect(stateOf(stages, "validation")).toBe("todo");
});

test("构建完成但没有匹配报告时，正式评测标「需要处理」并说明原因", () => {
  const stages = releaseStages([version({ index_version_id: "iv_c", status: "validating" })], [], NO_DRIFT);
  expect(stateOf(stages, "build")).toBe("done");
  expect(stateOf(stages, "evaluation")).toBe("blocked");
  expect(noteOf(stages, "evaluation")).toContain("缺少可用于发布的正式质量报告");
  // 验证本身在跑，所以是进行中，不是 blocked
  expect(stateOf(stages, "validation")).toBe("current");
});

test("报告指纹不匹配不算有报告", () => {
  const stages = releaseStages(
    [version({ index_version_id: "iv_c", status: "validating", config_fingerprint: "b".repeat(64) })],
    [report()],
    NO_DRIFT,
  );
  expect(stateOf(stages, "evaluation")).toBe("blocked");
});

test("未通过的报告不算有报告", () => {
  const stages = releaseStages(
    [version({ index_version_id: "iv_c", status: "validating" })],
    [report({ passed: false })],
    NO_DRIFT,
  );
  expect(stateOf(stages, "evaluation")).toBe("blocked");
});

test("ready 时待激活是进行中，并说明不会自动激活", () => {
  const stages = releaseStages([version({ index_version_id: "iv_c", status: "ready" })], [report()], NO_DRIFT);
  expect(stateOf(stages, "evaluation")).toBe("done");
  expect(stateOf(stages, "validation")).toBe("done");
  expect(stateOf(stages, "pending")).toBe("current");
  expect(noteOf(stages, "pending")).toContain("手动");
});

test("validation_failed 标未通过并给出下一步", () => {
  const stages = releaseStages([version({ index_version_id: "iv_c", status: "validation_failed" })], [report()], NO_DRIFT);
  expect(stateOf(stages, "validation")).toBe("failed");
  expect(noteOf(stages, "validation")).toContain("重新验证");
});

test("有 active 版本时生效已完成，并指出线上是哪一版", () => {
  const stages = releaseStages([version({ version_no: 3, status: "active" })], [], NO_DRIFT);
  expect(stateOf(stages, "live")).toBe("done");
  expect(noteOf(stages, "live")).toContain("v3");
});

test("配置漂移体现在索引定义的说明里", () => {
  const stages = releaseStages([version({ status: "active" })], [], [
    { field: "chunking_version", active: "v1-500-50", current: "v1-700-100" },
  ]);
  expect(stateOf(stages, "definition")).toBe("done");
  expect(noteOf(stages, "definition")).toContain("配置已更新 1 项");
});

test("任何输入下都是七个格子，顺序固定", () => {
  const keys = releaseStages([], [], NO_DRIFT).map((item) => item.key);
  expect(keys).toEqual(["definition", "version", "build", "evaluation", "validation", "pending", "live"]);
});
