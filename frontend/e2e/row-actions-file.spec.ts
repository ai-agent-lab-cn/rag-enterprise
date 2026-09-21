import { expect, test } from "@playwright/test";

/**
 * 守护资料列表里的文件类行操作（`RowAction.file`，见 ui/RowActions.tsx）。
 *
 * **它验证的是一条 vitest 验证不了的链路**：可见的 `FileButton` 触发隐藏
 * `<input type="file">` 的 `.click()`，并且这次调用仍处于用户原生点击的调用栈中，
 * 从而不触发浏览器的「用户激活」限制。jsdom 不模拟这条限制——
 * `input.click()` 在 jsdom 里无论从哪调用都会成功——所以现有 vitest 用例只能证明
 * 「input 没被卸载」，证明不了「用户手势没有丢」。若 `FileButton` 将触发逻辑改成
 * 微任务/宏任务，vitest 会继续全绿，而真实浏览器里点「更新文件」会静默无反应。
 *
 * DocumentPanel 是 `RowAction.file` 的真实消费者。测试使用 CI 冒烟流程已经创建的第一份
 * 资料，只验证「点击更新文件后浏览器原生 filechooser 事件真的触发了」；不选择文件，
 * 因而不会发起上传或改变测试数据。
 *
 * **没有凭据时用 test.skip 优雅跳过**，与 visual-baseline.spec.ts 同一套开关
 * （SMOKE_ADMIN_USERNAME / SMOKE_ADMIN_PASSWORD）。跳过在 CI 日志里长得和通过一模一样
 * （见 CLAUDE.md 第五条）——这意味着本测试只有在 CI 真的配置了这两个环境变量时才提供
 * 保护；没配置时它不是「通过」，是「完全没跑」，需要有人对着 CI 配置确认这一点。
 */

const username = process.env.SMOKE_ADMIN_USERNAME;
const password = process.env.SMOKE_ADMIN_PASSWORD;

test("资料列表里的更新文件会真的打开文件选择器", async ({ page }) => {
  test.skip(!username || !password, "需要管理员凭据；跳过不代表通过，见文件头注释");

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "登录 RAG 工作台" })).toBeVisible();
  await page.getByLabel("用户名").fill(username!);
  await page.getByLabel("密码", { exact: true }).fill(password!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "项目概览" })).toBeVisible();

  // 页面间跳转走内部导航（点菜单），不能用 page.goto()——令牌只存在页面内存，整页刷新会丢。
  await page.getByRole("button", { name: "知识库管理", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "知识库管理" })).toBeVisible();
  await page
    .getByRole("table", { name: "知识库列表" })
    .getByRole("row")
    .nth(1)
    .getByRole("button")
    .first()
    .click();
  await expect(page.getByRole("tab", { name: /资料/ })).toBeVisible();
  const updateButton = page.getByRole("button", { name: "更新文件", exact: true }).first();
  await expect(updateButton).toBeVisible();

  // 断言核心：可见按钮点击后，浏览器的原生文件选择器真的被唤起——证明用户激活
  // 一直保留到隐藏 input 的 click。不 setFiles，不发起真实上传。
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser", { timeout: 3_000 }),
    updateButton.click(),
  ]);
  expect(chooser.isMultiple()).toBe(false);
});
