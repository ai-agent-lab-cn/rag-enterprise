import { expect, test, type Page } from "@playwright/test";

/**
 * 视觉基线。
 *
 * 用途是**迁移期的变化确认**，不是 CI 门禁：UI Foundation 迁移会让几百处 className
 * 改写，届时每个页面都会有像素变化，而人眼逐页看 12 个页面根本看不过来。有了基线，
 * 每一处变化都必须被显式接受（`--update-snapshots`），而不是悄悄溜过去。
 *
 * **已知失真**：`fullPage` 截图会把页面展开到完整高度，而 `position: sticky` 的元素
 * （左侧导航，`height: 100vh`）不会跟着拉长，于是长页面的截图里侧边栏下方是白的。
 * 实测滚动到底部时侧边栏顶部仍在 0、高度仍等于视口高——真实浏览没有问题，不要去「修」它。
 *
 * 它**不进 CI**，因为截图绑定当前数据集：文档数、评测记录、时间戳一变，布局高度就变，
 * 放进 CI 只会天天误报。需要凭据才运行，与 controlled-pilot 同一套开关。
 *
 * **它会写数据。** 脚本要点开弹层才能拍到弹层，而点击落在真实环境上：2026-08-30 有一次
 * 跑动把 reader 的角色改成了管理员（见文末成员弹层那段的说明）。不要在任何你不愿意被
 * 改动的环境上跑，也不要因为「只是截图」就放松警惕。
 *
 * 首次生成：
 *   SMOKE_ADMIN_USERNAME=demo SMOKE_ADMIN_PASSWORD=... \
 *     npx playwright test visual-baseline --project=desktop-chromium --update-snapshots
 * 迁移后对比：去掉 --update-snapshots 重跑，差异会输出到 playwright-report。
 */

const username = process.env.SMOKE_ADMIN_USERNAME;
const password = process.env.SMOKE_ADMIN_PASSWORD;

/** 每个页面：菜单名 → 落地后用于确认渲染完成的标题。 */
const PAGES: Array<{ menu: string; heading: string; name: string }> = [
  { menu: "概览", heading: "项目概览", name: "overview" },
  { menu: "问答工作台", heading: "对话助手", name: "chat" },
  { menu: "知识库管理", heading: "知识库管理", name: "knowledge-bases" },
  { menu: "数据源管理", heading: "数据源管理", name: "data-sources" },
  { menu: "评测中心", heading: "评测中心", name: "evaluation-center" },
  { menu: "Bad Case", heading: "Bad Case", name: "bad-cases" },
  { menu: "链路验收", heading: "链路验收", name: "acceptance" },
  { menu: "成员与权限", heading: "成员与权限", name: "members" },
];

/**
 * 有意不做基线的两个页面：
 *
 * - 系统状态：显示进程内指标（请求数、RAG 查询数），**访问这一页本身就会让计数变化**。
 * - 审计记录：审计事件只增不减，页面高度随使用不断增长（实测已到 11866px）。
 *
 * 它们变的是数据不是样式，纳入基线只会每次都红。这两页的布局来自共享的
 * `.management-table` 等规则，已由其他页面覆盖；迁移时若要确认，需人工过目。
 */

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("用户名").fill(username!);
  await page.getByLabel("密码", { exact: true }).fill(password!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "项目概览" })).toBeVisible();
}

/**
 * 等到页面不再显示加载文案。
 *
 * `networkidle` 只保证请求停了，不保证 React 已经用数据重渲染——实测拍到过
 * 「正在读取知识库…」的中间态。
 *
 * 只匹配「正在…」这类瞬时文案。别把「处理中」也算进来：那是知识库的常驻状态标签
 * （`STATUS.processing`），一旦有知识库处于该状态，这里就会每页等满超时。
 */
async function settled(page: Page) {
  await page
    .waitForFunction(() => !/正在(读取|汇总|加载|上传|建立)/.test(document.body.innerText), { timeout: 5000 })
    .catch(() => {});
}

/**
 * 关掉动画与光标闪烁，并等字体真正就绪，否则同一状态两次截图也会有像素差。
 *
 * **字体这一步是 `--update-snapshots` 必需的。** 普通模式下 `toHaveScreenshot` 会反复
 * 截图直到两帧一致，字体加载完成前的画面自然被丢弃；而 update 模式不重试，直接把第一帧
 * 写成基线。结果是拍完立刻复跑就红：fallback 字体的字宽不同，工具栏元素整体错位
 * （实测资料 Tab 差 990 像素），连「←」的抗锯齿都差 2 像素。
 */
async function freeze(page: Page) {
  await page.addStyleTag({
    content: `*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }`,
  });
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
}

test.describe("视觉基线", () => {
  test.skip(!username || !password, "需要管理员凭据；见文件头的生成命令");

  // 用 expect.soft：一次跑完能看到全部差异，而不是第一处不同就中断——迁移后对比时
  // 需要的是「哪些页面变了」的完整清单。
  // 全部页面共用一次登录：令牌只存在页面内存里，storageState 存不下，而后端登录限流
  // 默认 10 次/窗口（LOGIN_RATE_LIMIT），一页一登会在第 11 页撞上 429。
  test("全站页面视觉", async ({ page }) => {
    await signIn(page);

    for (const item of PAGES) {
      if (item.menu !== "概览") {
        await page.getByRole("button", { name: item.menu, exact: true }).first().click();
      }
      await expect(page.getByRole("heading", { name: item.heading }).first()).toBeVisible();
      await page.waitForLoadState("networkidle");
      await settled(page);
      await freeze(page);
      await expect.soft(page).toHaveScreenshot(`${item.name}.png`, { fullPage: true });
    }

    // 知识库详情的全部 7 个 Tab。这个页面横跨四个组件文件（详情页本体 + DocumentPanel
    // + KnowledgeBaseDataSourcesPanel + ParsingPanel），只拍其中两个 Tab 的话，
    // 另外几个的视觉回归就无人守护——迁移时正是靠这些截图发现操作列换行的。
    await page.getByRole("button", { name: "知识库管理", exact: true }).first().click();
    await page.getByText("企业知识库").first().click();
    await expect(page.getByRole("tab", { name: /资料/ })).toBeVisible();
    await page.waitForLoadState("networkidle");
    await settled(page);

    for (const [key, label] of [
      ["documents", "资料"],
      ["data-sources", "数据源"],
      ["categories", "分类管理"],
      ["parsing", "解析与切片"],
      ["versions", "版本治理"],
      ["members", "权限边界"],
      ["conversations", "会话"],
    ] as const) {
      await page.getByRole("tab", { name: new RegExp(label) }).click();
      await page.waitForTimeout(400);
      await page.waitForLoadState("networkidle");
      await settled(page);
      await freeze(page);
      await expect.soft(page).toHaveScreenshot(`kb-detail-${key}.png`, { fullPage: true });
    }

    // 弹层是迁移风险最高的一类：遮罩、层级、居中、焦点都容易在换实现时出问题。
    await page.getByRole("button", { name: "知识库管理", exact: true }).first().click();
    await page.getByRole("button", { name: "＋ 新建知识库" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    // 「将复制 N 个有效分类」这句要等分类模板拉回来才出现，它撑高弹层 42px。
    // 不等的话 --update-snapshots 会把「加载中」那一帧写成基线，之后每次比对都差 42px。
    await expect(page.getByText(/将复制 \d+ 个有效分类/)).toBeVisible();
    await freeze(page);
    // 只截弹层本身：Radix 的滚动锁定会给 body 补一个滚动条宽度的 padding，
    // 背景内容因此横向位移几像素，整页截图每次都不一样。弹层才是这张图要记录的东西。
    await expect.soft(page.getByRole("dialog")).toHaveScreenshot("dialog-create-knowledge-base.png");

    // 成员弹层是唯一一个四个字段的表单，也是 Modal → Dialog 迁移里字段最多的一处。
    //
    // **点击前必须等页面稳定。** 这个脚本跑在有真实数据的环境上，误点就是真实写操作：
    // 2026-08-30 有一次跑动把 reader 的角色改成了管理员（审计事件 member.update，
    // 23:09:11），事后不能稳定复现，最可能是「新建成员」按钮所在的 topbar-context
    // 在 portal 挂载时重排，Playwright 已经把鼠标移到旧位置、按下时底下换成了别的按钮。
    // 等表格渲染完再点，布局就不会在点击途中变。
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "成员与权限", exact: true }).first().click();
    await expect(page.getByRole("table", { name: "成员列表" })).toBeVisible();
    await settled(page);
    await page.getByRole("button", { name: "新建成员" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await freeze(page);
    await expect.soft(page.getByRole("dialog")).toHaveScreenshot("dialog-create-member.png");
  });
});
