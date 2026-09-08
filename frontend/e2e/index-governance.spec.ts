import { expect, test, type Page } from "@playwright/test";

/**
 * 索引治理页的状态回归。
 *
 * 覆盖实施计划第 48 节 Step 10 列的那些场景。**用 `page.route()` 构造状态**，
 * 不动真实后端——这些状态里有 build_failed / validation_failed / cleaned，
 * 真跑一遍要破坏演示库的索引数据。
 *
 * 与 `visual-baseline.spec.ts` 的分工：那个拍像素、绑定数据集、不进 CI；
 * 这个断言行为与文案，不依赖具体数据，可以进 CI。
 *
 * 需要凭据：
 *   SMOKE_ADMIN_USERNAME=demo SMOKE_ADMIN_PASSWORD=... \
 *     npx playwright test index-governance --project=desktop-chromium
 *
 * **没有凭据时整个 describe 被 skip，而 skip 在日志里长得和通过一模一样**
 * （CLAUDE.md 第五条）。所以别把「`npm run test:e2e` 绿了」当成这些场景被验证过——
 * 要么带凭据跑，要么明确知道它被跳过了。
 */

const username = process.env.SMOKE_ADMIN_USERNAME;
const password = process.env.SMOKE_ADMIN_PASSWORD;

const FP = "a".repeat(64);
const OTHER_FP = "b".repeat(64);

/** 一个配置完整的候选版本骨架。各场景只改 status 与指纹。 */
function version(overrides: Record<string, unknown>) {
  return {
    index_version_id: "iv_case",
    version_no: 9,
    status: "building",
    creation_reason: "config_changed",
    config_completeness: "complete",
    chunking_version: "v1-700-100",
    parser_version: "2.0",
    embedding_model: "text2vec-base-chinese",
    embedding_dimension: 768,
    processing_options: {},
    config_fingerprint: FP,
    config_snapshot: {},
    component_manifest: {},
    validation_report_id: null,
    evaluation_report_id: null,
    release_fingerprint: null,
    document_snapshot_id: "ds_case",
    requested_by: "demo",
    force_reason: null,
    created_at: "2026-09-08T00:00:00Z",
    activated_at: null,
    retired_at: null,
    ...overrides,
  };
}

const ACTIVE = version({
  index_version_id: "iv_live", version_no: 1, status: "active",
  activated_at: "2026-09-01T00:00:00Z", release_fingerprint: "c".repeat(64),
});

function passedReport(fingerprint: string | null) {
  return {
    report_id: "qr_case", dataset_id: "rag-corpus", dataset_version: "2.0",
    commit: "abc1234", run_at: "2026-09-07T00:00:00Z", models: {},
    passed: true, config_fingerprint: fingerprint,
  };
}

/** 拦掉索引版本与质量报告，构造出目标场景。 */
async function stub(page: Page, versions: unknown[], reports: unknown[]) {
  await page.route("**/api/knowledge-bases/*/index-versions", (route) =>
    route.request().method() === "GET" ? route.fulfill({ json: versions }) : route.fallback());
  await page.route("**/api/evaluations", (route) => route.fulfill({ json: reports }));
}

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("用户名").fill(username!);
  await page.getByLabel("密码", { exact: true }).fill(password!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "项目概览" })).toBeVisible();
}

/**
 * 进入索引治理 Tab。
 *
 * **每个用例都要重新走「知识库管理 → 企业知识库」这条路径**，不能只切 Tab：
 * KnowledgeBaseDetailPage 的 load() 挂在 useCallback([id]) 上，切 Tab 不会重新请求，
 * 于是这个用例注册的 route 拦不到任何东西，拿到的还是上一个用例的数据。
 */
async function openGovernance(page: Page) {
  await page.getByRole("button", { name: "知识库管理", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "知识库管理" })).toBeVisible();
  await page.getByText("企业知识库").first().click();
  await expect(page.getByRole("tab", { name: /资料/ })).toBeVisible();
  await page.getByRole("tab", { name: /索引治理/ }).click();
  await page.waitForLoadState("networkidle");
}

/** 读某个发布流程格子的状态（aria-label 形如「正式评测：需要处理，原因」）。 */
async function stage(page: Page, label: string) {
  return page.getByRole("list", { name: "发布流程" }).getByRole("img", { name: new RegExp(`^${label}`) }).getAttribute("aria-label");
}

// serial + 共享 page：登录限流默认 10 次/窗口（LOGIN_RATE_LIMIT），
// 这里有十几个用例，一个一登会从第 11 个开始撞 429（CLAUDE.md 第七条）。
test.describe.configure({ mode: "serial" });

test.describe("索引治理状态回归", () => {
  test.skip(!username || !password, "需要管理员凭据；见文件头注释");

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    await signIn(page);
  });

  test.afterAll(async () => {
    await page.close();
  });

  // 每个用例自己注册 route，跑完清掉，免得泄漏到下一个用例。
  test.afterEach(async () => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });

  test("主 UI 不出现后端枚举原文", async () => {
    await stub(page, [
      version({ index_version_id: "iv_bf", version_no: 3, status: "build_failed" }),
      version({ index_version_id: "iv_vf", version_no: 2, status: "validation_failed" }),
      ACTIVE,
    ], []);
    await openGovernance(page);

    const body = await page.locator("main").innerText();
    for (const raw of ["build_failed", "validation_failed", "Legacy 未生成", "BLOCKED", "PENDING", "Validation Gate"]) {
      expect(body, `主 UI 不应出现 ${raw}`).not.toContain(raw);
    }
    // 对应的中文必须在
    expect(body).toContain("构建失败");
    expect(body).toContain("验证未通过");
    expect(body).toContain("当前生效");
  });

  test("构建失败：只提示重新构建，不同时催质量报告", async () => {
    await stub(page, [version({ status: "build_failed" }), ACTIVE], []);
    await openGovernance(page);

    expect(await stage(page, "构建")).toContain("未通过");
    // 构建都失败了还提示缺报告，用户不知道先处理哪个——这个组合曾真实出现过
    expect(await stage(page, "正式评测")).toContain("未开始");
    expect(await stage(page, "发布验证")).toContain("未开始");
    await expect(page.getByRole("table", { name: "索引版本" }).getByRole("button", { name: "重新构建" })).toBeVisible();
  });

  test("无正式质量报告：正式评测标需要处理并说明原因", async () => {
    await stub(page, [version({ status: "validating" }), ACTIVE], []);
    await openGovernance(page);

    expect(await stage(page, "正式评测")).toContain("缺少可用于发布的正式质量报告");
    await expect(page.getByRole("table", { name: "索引版本" }).locator("tr", { hasText: "iv_case" }).getByText("缺少可用报告")).toBeVisible();
    // active 版本不该被说成「缺少报告」：它的凭据是发布时绑定的验证报告
    await expect(page.getByRole("table", { name: "索引版本" }).locator("tr", { hasText: "iv_live" }).getByText("缺少可用报告")).toHaveCount(0);
  });

  test("质量报告配置不匹配：显示配置已变化且不可用于发布", async () => {
    await stub(page, [version({ status: "validating" }), ACTIVE], [passedReport(OTHER_FP)]);
    await openGovernance(page);

    const row = page.getByRole("table", { name: "正式质量评测" }).locator("tbody tr").first();
    await expect(row.getByText("配置已变化")).toBeVisible();
    await expect(row.getByText("不可用于发布")).toBeVisible();
    expect(await stage(page, "正式评测")).toContain("需要处理");
  });

  test("质量报告匹配：可用于发布，质量状态为已通过", async () => {
    await stub(page, [version({ status: "validating" }), ACTIVE], [passedReport(FP)]);
    await openGovernance(page);

    const row = page.getByRole("table", { name: "正式质量评测" }).locator("tbody tr").first();
    await expect(row.getByText("与当前版本一致")).toBeVisible();
    await expect(row.getByText("可用于发布")).toBeVisible();
    expect(await stage(page, "正式评测")).toContain("已完成");
    await expect(page.getByRole("table", { name: "索引版本" }).locator("tr", { hasText: "iv_case" }).getByText("正式评测已通过")).toBeVisible();
  });

  test("三层验证通过（ready）：待激活进行中，且不会自动激活", async () => {
    await stub(page, [version({ status: "ready", validation_report_id: "vr_case" }), ACTIVE], [passedReport(FP)]);
    await openGovernance(page);

    expect(await stage(page, "发布验证")).toContain("已完成");
    const pending = await stage(page, "待激活");
    expect(pending).toContain("进行中");
    expect(pending).toContain("手动");
    await expect(page.getByRole("table", { name: "索引版本" }).getByRole("button", { name: "激活" })).toBeVisible();
  });

  test("验证未通过：可重新验证，并说明部分条件未满足", async () => {
    await stub(page, [version({ status: "validation_failed" }), ACTIVE], [passedReport(FP)]);
    await openGovernance(page);

    const validation = await stage(page, "发布验证");
    expect(validation).toContain("未通过");
    expect(validation).toContain("重新验证");
    await expect(page.getByRole("table", { name: "索引版本" }).getByRole("button", { name: "重新验证" })).toBeVisible();
  });

  test("历史版本：配置与质量状态都说明不可追溯，而不是显示缺失", async () => {
    await stub(page, [version({
      index_version_id: "iv_legacy", version_no: null, status: "active",
      config_completeness: "unknown", parser_version: "legacy",
      chunking_version: "legacy", embedding_model: "legacy",
      config_fingerprint: "", release_fingerprint: null,
      activated_at: "2026-08-01T00:00:00Z",
    })], []);
    await openGovernance(page);

    const table = page.getByRole("table", { name: "索引版本" });
    await expect(table.getByText("历史索引配置")).toBeVisible();
    await expect(table.getByText("历史版本")).toBeVisible();
    // 当前索引定义也要说明，不能只在表格里说（同一份数据两种说法）
    await expect(page.getByText("历史索引配置不可完整追溯")).toBeVisible();
  });

  test("上一版本可回滚；已退役可清理", async () => {
    await stub(page, [
      version({ index_version_id: "iv_prev", version_no: 2, status: "previous" }),
      version({ index_version_id: "iv_retired", version_no: 1, status: "retired" }),
      ACTIVE,
    ], []);
    await openGovernance(page);

    const table = page.getByRole("table", { name: "索引版本" });
    await expect(table.locator("tr", { hasText: "iv_prev" }).getByRole("button", { name: "回滚" })).toBeVisible();
    await expect(table.locator("tr", { hasText: "iv_retired" }).getByRole("button", { name: "清理" })).toBeVisible();
    await expect(table.getByText("上一版本")).toBeVisible();
    await expect(table.getByText("已退役")).toBeVisible();
  });

  test("已清理版本只剩查看，不能再激活或回滚", async () => {
    await stub(page, [version({ index_version_id: "iv_cleaned", version_no: 2, status: "cleaned" }), ACTIVE], []);
    await openGovernance(page);

    const row = page.getByRole("table", { name: "索引版本" }).locator("tr", { hasText: "iv_cleaned" });
    await expect(row.getByText("已清理")).toBeVisible();
    for (const forbidden of ["激活", "回滚", "重新构建"]) {
      await expect(row.getByRole("button", { name: forbidden })).toHaveCount(0);
    }
    await expect(row.getByRole("button", { name: "详情" })).toBeVisible();
  });

  test("运行记录只列索引治理任务", async () => {
    await stub(page, [ACTIVE], []);
    await page.route("**/api/knowledge-bases/*/operations*", (route) => {
      const base = {
        operation_id: "", knowledge_base_id: "kb", operation_type: "", status: "succeeded",
        current_stage: "completed", progress_mode: "count", progress_percent: 100,
        total_count: 1, completed_count: 1, processing_count: 0, failed_count: 0,
        document_id: null, document_version_id: null, data_source_id: null,
        error_code: null, error_message: null, started_at: null, finished_at: null,
        created_at: "2026-09-08T00:00:00Z", updated_at: "2026-09-08T00:00:00Z",
      };
      return route.fulfill({ json: [
        { ...base, operation_id: "o1", operation_type: "index_build" },
        { ...base, operation_id: "o2", operation_type: "file_upload" },
        { ...base, operation_id: "o4", operation_type: "sync_run" },
        { ...base, operation_id: "o5", operation_type: "file_update" },
        { ...base, operation_id: "o6", operation_type: "document_reprocess" },
      ] });
    });
    await openGovernance(page);

    const types = await page.getByRole("table", { name: "运行记录" }).locator("tbody tr td:first-child").allInnerTexts();
    // 只有 index_build 一种：index_validation/index_activation 被 0036 迁移删掉了
    // （单事务动作，没有进度可跟踪）。别因为「只有一种看着奇怪」就把死值加回来。
    expect(new Set(types.map((t) => t.trim()))).toEqual(new Set(["索引构建"]));
    // 筛选下拉不该出现选了却没结果的类型
    const options = await page.getByLabel("任务类型筛选").locator("option").allInnerTexts();
    for (const absent of ["数据同步", "文件更新", "资料重新处理", "文件上传"]) {
      expect(options).not.toContain(absent);
    }
  });

  test("激活后新版本生效、旧版本变上一版本", async () => {
    await stub(page, [
      version({ index_version_id: "iv_new", version_no: 2, status: "active", activated_at: "2026-09-08T01:00:00Z" }),
      version({ index_version_id: "iv_old", version_no: 1, status: "previous" }),
    ], [passedReport(FP)]);
    await openGovernance(page);

    const table = page.getByRole("table", { name: "索引版本" });
    await expect(table.locator("tr", { hasText: "iv_new" }).getByText("当前生效")).toBeVisible();
    await expect(table.locator("tr", { hasText: "iv_old" }).getByText("上一版本")).toBeVisible();
    const live = await stage(page, "生效");
    expect(live).toContain("已完成");
    expect(live).toContain("v2");
  });
});
