import { expect, test } from "@playwright/test";

const username = process.env.SMOKE_ADMIN_USERNAME;
const password = process.env.SMOKE_ADMIN_PASSWORD;

test("管理员重新登录并访问生产管理页面", async ({ page }) => {
  test.skip(!username || !password, "需要受控试运行管理员凭据");

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "登录 RAG 工作台" })).toBeVisible();
  await page.getByLabel("用户名").fill(username!);
  await page.getByLabel("密码", { exact: true }).fill(password!);
  await page.getByRole("button", { name: "登录", exact: true }).click();

  await expect(page.getByRole("heading", { name: "项目概览" })).toBeVisible();
  await page.getByRole("button", { name: "数据源管理" }).click();
  await expect(page.getByRole("heading", { name: "数据源管理" })).toBeVisible();
  await expect(page.getByText("服务暂时不可用，请稍后重试。")).toHaveCount(0);

  await page.getByRole("button", { name: "系统状态" }).click();
  await expect(page.getByRole("heading", { name: "系统状态" })).toBeVisible();
  await expect(page.getByText("服务已就绪")).toBeVisible();
});
