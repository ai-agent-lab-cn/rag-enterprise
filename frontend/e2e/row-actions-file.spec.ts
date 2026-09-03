import { expect, test } from "@playwright/test";

/**
 * 守护「⋯」菜单里的文件类行操作（`RowAction.file`，见 ui/RowActions.tsx）。
 *
 * **它验证的是一条 vitest 验证不了的链路**：Radix `DropdownMenu.Item` 的 `onSelect`
 * 经 `flushSync` 同步派发，使隐藏 `<input type="file">` 的 `.click()` 与用户的原生点击
 * 处于同一调用栈，从而不触发浏览器的「用户激活」限制。jsdom 不模拟这条限制——
 * `input.click()` 在 jsdom 里无论从哪调用都会成功——所以现有 vitest 用例（App.test.tsx）
 * 只能证明「input 没被卸载」，证明不了「用户手势没有丢」。若某次 Radix 升级把
 * `onSelect` 派发改成微任务/宏任务，vitest 会继续全绿，而真实浏览器里点「更新文件」
 * 会静默无反应（CLAUDE.md 第九条记录的那种「点了按钮什么也不会发生」）。
 *
 * DataSourcesPage 是 `RowAction.file` 的第一个真实消费者，其数据源行在 allowed_actions
 * ≥3 时会折叠进「⋯」菜单，走的正是这条链路（≤2 时是平铺的 FileButton，走普通 onClick，
 * 不经过 Radix onSelect，不需要本测试覆盖）。
 *
 * **用 page.route() 构造场景，不真实上传文件**：这个 demo 环境没有跑 index worker，
 * 真的上传会创建一个永远 queued 的索引作业，知识库随即进入「索引作业活跃」状态，
 * 上传的数据源会因为后端 409 INDEX_JOB_ACTIVE 而删不掉。这里拦截 GET /api/data-sources
 * 注入一条固定的、allowed_actions 命中折叠阈值的假数据，只验证「点击『更新文件』菜单项
 * 后浏览器原生 filechooser 事件真的触发了」，不点选文件、不真正发起上传请求。
 *
 * **没有凭据时用 test.skip 优雅跳过**，与 visual-baseline.spec.ts 同一套开关
 * （SMOKE_ADMIN_USERNAME / SMOKE_ADMIN_PASSWORD）。跳过在 CI 日志里长得和通过一模一样
 * （见 CLAUDE.md 第五条）——这意味着本测试只有在 CI 真的配置了这两个环境变量时才提供
 * 保护；没配置时它不是「通过」，是「完全没跑」，需要有人对着 CI 配置确认这一点。
 */

const username = process.env.SMOKE_ADMIN_USERNAME;
const password = process.env.SMOKE_ADMIN_PASSWORD;

const probeDataSource = {
  data_source_id: "src_e2e_probe",
  name: "e2e-probe.md",
  source_type: "file",
  knowledge_base_id: "kb_default",
  knowledge_base_name: "默认知识库",
  enabled: true,
  upload_status: "succeeded",
  index_status: "succeeded",
  sync_status: "succeeded",
  document_count: 1,
  source_file_bytes: 2048,
  last_indexed_at: "2026-08-01T00:00:00Z",
  last_synced_at: "2026-08-01T00:00:00Z",
  failure_reason: null,
  updated_at: "2026-08-01T00:00:00Z",
  acl_version: 1,
  allow_user_ids: [],
  deny_user_ids: [],
  // update_file + disable + delete = 3 个动作，命中 RowActions 的折叠阈值（>2 进「⋯」菜单），
  // 这是本测试要覆盖的形态；detail/edit 不被 DataSourcesPage 的 rowActions() 消费，只是陪衬。
  allowed_actions: ["detail", "edit", "update_file", "disable", "delete"],
};

test("数据源「⋯」菜单里的更新文件会真的打开文件选择器", async ({ page }) => {
  test.skip(!username || !password, "需要管理员凭据；跳过不代表通过，见文件头注释");

  await page.route("**/api/data-sources**", (route) => route.fulfill({ json: [probeDataSource] }));

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "登录 RAG 工作台" })).toBeVisible();
  await page.getByLabel("用户名").fill(username!);
  await page.getByLabel("密码", { exact: true }).fill(password!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "项目概览" })).toBeVisible();

  // 页面间跳转走内部导航（点菜单），不能用 page.goto()——令牌只存在页面内存，整页刷新会丢。
  await page.getByRole("button", { name: "数据源管理", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "数据源管理" })).toBeVisible();
  await expect(page.getByText("e2e-probe.md")).toBeVisible();

  await page.getByRole("button", { name: "e2e-probe.md 的更多操作" }).click();
  const menuItem = page.getByRole("menuitem", { name: "更新文件" });
  await expect(menuItem).toBeVisible();

  // 断言核心：菜单项点击后，浏览器的原生文件选择器真的被唤起——证明手势没有在
  // Radix 的 onSelect 派发过程中丢失。不 setFiles，不发起真实上传。
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser", { timeout: 3_000 }),
    menuItem.click(),
  ]);
  expect(chooser.isMultiple()).toBe(false);
});
