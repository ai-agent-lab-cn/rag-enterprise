import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["line"], ["html", { outputFolder: "playwright-report", open: "never" }]] : "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    // 本地手动工具，不进 CI、也没有视觉基线快照：`npm run test:e2e` 显式锁定
    // desktop-chromium（见 package.json），因为 visual-baseline.spec.ts 只在
    // desktop-chromium 下生成过快照——之前 `npm run test:e2e` 不指定 project，
    // CI 会把这个 project 也跑进去，而它一张快照可比，导致「配置说要跑、实际
    // 无快照可比」的中间态。需要看移动端响应式行为时手动跑：
    // `npx playwright test visual-baseline --project=mobile-chromium --update-snapshots`。
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
  ],
});
