import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import type {
  IndexEvaluationRun,
  IndexVersion,
  LifecycleEvent,
  ValidationReport,
} from "../types";
import { IndexVersionDetailDialog } from "./IndexVersionDetailDialog";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const KB = "kb_default";
const VERSION_ID = "iv_1";

/**
 * 完整的 IndexVersion。**逐字段齐全**是必需的，不是洁癖：弹框直接读
 * `version.config_snapshot`、`version.component_manifest` 的 `Object.keys()`，
 * 少一个字段就崩在那一行，症状是「整个弹框空白」而不是「某个断言失败」。
 */
const VERSION: IndexVersion = {
  index_version_id: VERSION_ID,
  status: "active",
  chunking_version: "v1-700-100",
  parser_version: "structured-v2",
  embedding_model: "test/embedding",
  embedding_dimension: 1024,
  processing_options: { chunk_size: 700, chunk_overlap: 100 },
  config_fingerprint: "a".repeat(64),
  evaluation_report_id: "rep_official_1",
  validation_report_id: "vr_1",
  document_snapshot_id: "snap_1",
  rebuild_batch_id: null,
  version_no: 3,
  creation_reason: "config_changed",
  force_reason: null,
  requested_by: "admin",
  config_snapshot: { chunking: { version: "v1-700-100" } },
  component_manifest: {
    embedding_model: "test/embedding",
    vector_index_schema_version: "hnsw-v2",
  },
  release_fingerprint: "c".repeat(64),
  config_completeness: "complete",
  legacy_migrated: false,
  excluded_documents_acknowledged: false,
  created_at: "2026-09-01T00:00:00Z",
  activated_at: "2026-09-01T01:00:00Z",
  retired_at: null,
  cleaned_at: null,
};

/**
 * 治理机制启用之前留下的版本：没有配置指纹、没有组件清单、没有配置快照。
 * 它存在的意义是被追溯，不是被当成「通过了验证」。
 */
const LEGACY_VERSION: IndexVersion = {
  ...VERSION,
  status: "retired",
  version_no: 1,
  creation_reason: "legacy",
  config_completeness: "unknown",
  config_fingerprint: "",
  release_fingerprint: null,
  evaluation_report_id: null,
  validation_report_id: null,
  config_snapshot: {},
  component_manifest: {},
  activated_at: null,
};

const REPORT: ValidationReport = {
  validation_report_id: "vr_1",
  index_version_id: VERSION_ID,
  index_build_id: "ib_1",
  status: "pass",
  policy_version: "v3",
  evaluation_set_version: "2.0.0",
  baseline_version_id: null,
  integrity_result: { layer: "integrity", status: "pass", checks: [] },
  technical_result: { layer: "technical", status: "pass", checks: [] },
  retrieval_result: { layer: "retrieval", status: "pass", checks: [] },
  summary: null,
  failure_items: [],
  report_source: "standard",
  started_at: "2026-09-01T00:10:00Z",
  finished_at: "2026-09-01T00:20:00Z",
  created_at: "2026-09-01T00:20:00Z",
};

const EVENT: LifecycleEvent = {
  event_id: "ev_1",
  index_version_id: VERSION_ID,
  event_type: "activated",
  from_status: "ready",
  to_status: "active",
  actor_id: "admin",
  actor_role: "admin",
  reason: null,
  validation_report_id: "vr_1",
  created_at: "2026-09-01T01:00:00Z",
};

/** 跑完了、但指标没到冻结阈值。`status: "succeeded"` 与 `passed: false` 是两件事。 */
const EVALUATION: IndexEvaluationRun = {
  evaluation_run_id: "er_1",
  knowledge_base_id: KB,
  index_version_id: VERSION_ID,
  operation_id: "op_1",
  dataset_id: "retrieval",
  dataset_version: "2.0.0",
  dataset_slug: "retrieval",
  status: "succeeded",
  config_fingerprint: "a".repeat(64),
  baseline_report_id: null,
  report_id: "rep_official_1",
  official: true,
  passed: false,
  attempt_count: 1,
  max_attempts: 3,
  requested_by: "admin",
  failure_code: null,
  failure_reason: null,
  available_at: "2026-09-01T00:00:00Z",
  started_at: "2026-09-01T00:01:00Z",
  finished_at: "2026-09-01T00:05:00Z",
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:05:00Z",
};

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * 只覆盖弹框自己会调的四个接口。
 *
 * 不需要 `setAccessToken`：`api.ts` 的 `request()` 只在有 token 时补一个
 * Authorization 头（api.ts:83-87），而这里的假实现按 URL 分派，不看请求头。
 *
 * 兜底一律 404 而不是空 200：漏 mock 的接口若返回 `[]`，组件会安静地走「没有数据」
 * 分支，断言看起来全绿实际什么都没测到。
 */
const EVIDENCE_CHAIN = {
  knowledge_base_id: KB,
  index_version_id: VERSION_ID,
  version: {
    index_version_id: VERSION_ID,
    version_no: 3,
    status: "active",
    config_fingerprint: "a".repeat(64),
  },
  evaluation_run: {
    evaluation_run_id: "er_1",
    status: "succeeded",
    official: true,
    passed: true,
    config_fingerprint: "a".repeat(64),
    created_at: "2026-09-01T00:00:00Z",
  },
  formal_report: {
    report_id: "rep_official_1",
    official: true,
    passed: true,
    config_fingerprint: "a".repeat(64),
    run_at: "2026-09-01T00:05:00Z",
  },
  validation_report: {
    validation_report_id: "vr_1",
    status: "pass",
    report_source: "standard",
    evaluation_report_id: "rep_official_1",
    created_at: "2026-09-01T00:20:00Z",
  },
  activation: {
    event_id: "ev_1",
    event_type: "activated",
    actor_id: "admin",
    validation_report_id: "vr_1",
    created_at: "2026-09-01T01:00:00Z",
  },
};

function stubFetch(
  routes: Partial<Record<"versions" | "validations" | "events" | "evaluations" | "evidence", () => Response>> = {},
) {
  const list = `/api/knowledge-bases/${KB}/index-versions`;
  const detail = `${list}/${VERSION_ID}`;
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url === list) return Promise.resolve((routes.versions ?? (() => json([VERSION])))());
    if (url === `${detail}/validations`) return Promise.resolve((routes.validations ?? (() => json([REPORT])))());
    if (url === `${detail}/events`) return Promise.resolve((routes.events ?? (() => json([EVENT])))());
    if (url === `${detail}/evaluation-runs`) return Promise.resolve((routes.evaluations ?? (() => json([EVALUATION])))());
    if (url === `${detail}/evidence-chain`) return Promise.resolve((routes.evidence ?? (() => json(EVIDENCE_CHAIN)))());
    return Promise.resolve(json({ error: { message: `未 mock 的接口 ${url}` } }, 404));
  });
}

function renderDialog(
  overrides: { versionId?: string; onClose?: () => void; onActionComplete?: () => void } = {},
) {
  return render(
    <IndexVersionDetailDialog
      open
      knowledgeBaseId={KB}
      versionId={overrides.versionId ?? VERSION_ID}
      onClose={overrides.onClose ?? (() => undefined)}
      onActionComplete={overrides.onActionComplete}
    />,
  );
}

/**
 * 骨架本身对读屏不可见（`Skeleton` 是 `aria-hidden`），所以加载状态必须由一条
 * `role="status"` 单独承担——否则读屏用户听到的是一个只有标题的空弹框。
 */
test("打开时先显示加载状态，数据到达后才出现正文", async () => {
  stubFetch();

  renderDialog();

  expect(screen.getByRole("status")).toHaveTextContent("正在读取索引版本详情");
  expect(screen.queryByRole("heading", { name: "版本信息" })).not.toBeInTheDocument();

  expect(await screen.findByRole("heading", { name: "版本信息" })).toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

/**
 * 这个弹框取代了原来的独立页面，能取代的前提是六块内容一块不少。
 * 用 heading 定位而不是文本：区块标题就是用户扫读时的锚点，它得真的是标题。
 */
test("加载完成后六个区块全部渲染，并显示各自的关键内容", async () => {
  stubFetch();

  renderDialog();

  expect(await screen.findByRole("heading", { name: "版本信息" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "正式评测" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "证据链" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "三层验证" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "发布记录" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "组件清单" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "生命周期" })).toBeInTheDocument();

  // 版本信息：状态与创建原因都翻译成业务语言，active / config_changed 不出现在页面上。
  expect(screen.getByText("当前生效")).toBeInTheDocument();
  expect(screen.getByText("配置已变更")).toBeInTheDocument();
  // 三层验证：pass 单独走一句完整的话，而不是「已通过」两个字。
  expect(screen.getByText("所有发布检查均已通过")).toBeInTheDocument();
  // 组件清单：key 翻译成中文标签，值原样显示。
  expect(screen.getByText("Vector 索引")).toBeInTheDocument();
  expect(screen.getByText("hnsw-v2")).toBeInTheDocument();
  // 生命周期：事件类型翻译，activated 不出现在页面上。
  expect(screen.getByText("激活")).toBeInTheDocument();
  expect(screen.getByText(/索引版本$/)).toBeInTheDocument();
  expect(screen.getByText(/正式报告$/)).toBeInTheDocument();

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("发布记录中的验证报告 ID 可点击查看完整报告", async () => {
  stubFetch();
  renderDialog();

  await userEvent.click(await screen.findByRole("button", { name: "查看验证报告 vr_1" }));

  const reportDialog = screen.getByRole("dialog", { name: "验证报告" });
  expect(reportDialog).toHaveTextContent("vr_1");
  expect(reportDialog).toHaveTextContent("完整性检查");
  expect(reportDialog).toHaveTextContent("技术检查");
  expect(reportDialog).toHaveTextContent("检索质量检查");
});

/**
 * 版本被清理、或链接指向别的知识库时，用户手里只有那串 ID。
 * 报错必须把 ID 说出来，否则「找不到」这句话没法用来排查。
 */
test("列表里没有该版本时报错，并写出具体的版本 ID", async () => {
  stubFetch();

  renderDialog({ versionId: "iv_missing" });

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("找不到索引版本 iv_missing");
  expect(screen.queryByRole("heading", { name: "版本信息" })).not.toBeInTheDocument();
});

/**
 * 治理数据（验证 / 事件 / 评测）拿不到时，绝不能整块空着不说话——
 * 「拉取失败」和「这个版本没有治理数据」在页面上长得一模一样，含义却相反。
 * 同时版本信息必须照常渲染：配置与指纹跟治理接口无关，不该被一起拖下水。
 */
test("治理数据拉取失败时版本信息照常渲染，并单独报出失败原因", async () => {
  stubFetch({
    validations: () => json({ error: { message: "验证报告服务不可用" } }, 500),
  });

  renderDialog();

  expect(await screen.findByRole("heading", { name: "版本信息" })).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("验证报告服务不可用");
  // 失败之后评测区块落到空态文案——正因为它和「真的没有评测」一字不差，
  // 上面那条 alert 才是用户唯一能分辨两者的凭据。
  expect(screen.getByText(/还没有针对本版本的正式评测/)).toBeInTheDocument();
});

/**
 * 历史版本的风险是被误当成「验证过的版本」。提示必须是可见正文，不是 title
 * （CLAUDE.md 第一条），并且要说清楚它「只能用于追溯」。
 */
test("历史版本显示追溯提示，组件清单为空时给出无法证明同代的警告", async () => {
  stubFetch({ versions: () => json([LEGACY_VERSION]) });

  renderDialog();

  expect(await screen.findByText(/历史版本 · 部分治理信息未记录/)).toBeInTheDocument();
  expect(screen.getByText(/组件版本未记录/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "组件清单" })).toBeInTheDocument();
});

test("点击关闭按钮同时通知父组件刷新并关闭弹框", async () => {
  stubFetch();
  const onClose = vi.fn();
  const onActionComplete = vi.fn();

  renderDialog({ onClose, onActionComplete });
  await screen.findByRole("heading", { name: "版本信息" });

  await userEvent.click(screen.getByRole("button", { name: "关闭弹框" }));

  expect(onActionComplete).toHaveBeenCalledTimes(1);
  expect(onClose).toHaveBeenCalledTimes(1);
});

/**
 * 「跑完了」和「达标了」是两件事。一次 succeeded 但 passed=false 的运行，
 * 若只显示「已完成」，用户会以为这版可以发布。
 */
test("评测跑完但未达标时，除状态外还写出未达冻结阈值", async () => {
  stubFetch();

  renderDialog();

  expect(await screen.findByRole("heading", { name: "正式评测" })).toBeInTheDocument();
  expect(screen.getByText("已完成")).toBeInTheDocument();
  expect(screen.getByText("未达冻结阈值")).toBeInTheDocument();
  expect(screen.queryByText("已达冻结阈值")).not.toBeInTheDocument();
});

/** 没有评测时要说明「三层验证需要一份正式报告」，而不是留一块空白。 */
test("没有评测运行时给出空态并说明它是三层验证的前置条件", async () => {
  stubFetch({ evaluations: () => json([]) });

  renderDialog();

  expect(await screen.findByRole("heading", { name: "正式评测" })).toBeInTheDocument();
  expect(screen.getByText(/还没有针对本版本的正式评测/)).toBeInTheDocument();
  expect(screen.queryByText("未达冻结阈值")).not.toBeInTheDocument();
});

/**
 * 详情从独立页面改回弹层的前提，就是窄屏下 lg 会转成全屏——否则六个区块在手机上
 * 仍然放不下，改回来就是退步。断言 `Dialog` 对 `size="lg"` 落下的那个类
 * （Dialog.tsx:49），是这里唯一能在 jsdom 里核对该前提的手段：jsdom 不跑 CSS，
 * 视觉高度断言不出来。
 */
test("lg 弹层带上窄屏全屏的类，保证六个区块在手机上还放得下", async () => {
  stubFetch();

  renderDialog();
  await screen.findByRole("heading", { name: "版本信息" });

  expect(screen.getByRole("dialog").className).toContain("max-[768px]:h-dvh");
});
