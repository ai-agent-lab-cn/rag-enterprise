# RAG Enterprise UI 基线

本文件是项目 UI 事实源。设计图与本文件冲突时，先提出差异并确认，不要直接覆盖已冻结规则。

## 视觉方向

- 浅色企业后台，冷白背景、白色面板和低对比边框。
- 紫色只强调主操作、选中导航、分页选中和关键指标。
- 内容密度中高；以留白和边框建立层级，常规卡片不使用重阴影。
- 中文优先，英文仅保留模型名和必要技术术语。

## Tokens

```css
:root {
  --font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, -apple-system, sans-serif;

  --color-primary-50: #f3f1ff;
  --color-primary-100: #ebe8ff;
  --color-primary-500: #6548e8;
  --color-primary-600: #5738dc;
  --color-primary-700: #482bc5;
  --color-page: #f8faff;
  --color-surface: #ffffff;
  --color-surface-soft: #fafbff;
  --color-text-primary: #151a31;
  --color-text-secondary: #4f5873;
  --color-text-tertiary: #8690aa;
  --color-border: #e5e9f2;
  --color-border-strong: #d8deeb;
  --color-success: #20a464;
  --color-success-bg: #eaf8f0;
  --color-warning: #ef8500;
  --color-warning-bg: #fff4e7;
  --color-danger: #dc3030;
  --color-danger-bg: #fff0f0;

  --font-size-xs: 12px;
  --font-size-sm: 13px;
  --font-size-base: 14px;
  --font-size-md: 16px;
  --font-size-lg: 18px;
  --font-size-xl: 24px;
  --font-size-metric: 28px;

  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;

  --radius-sm: 6px;
  --radius-control: 8px;
  --radius-card: 12px;
  --radius-panel: 16px;
  --shadow-panel: 0 8px 24px rgb(29 41 81 / 6%);
  --shadow-popup: 0 16px 40px rgb(29 41 81 / 14%);

  --motion-instant: 80ms;
  --motion-fast: 140ms;
  --motion-base: 180ms;
  --motion-panel: 220ms;
  --ease-standard: cubic-bezier(.2, 0, 0, 1);
}
```

## 字体层级

| 元素 | 字号 / 字重 / 行高 |
|---|---|
| 产品名称 | 18px / 600 / 24px |
| 页面标题 | 24px / 600 / 32px |
| 页面副标题 | 13px / 400 / 20px |
| 面板标题 | 16px / 600 / 24px |
| 卡片标题、导航 | 14px / 600或500 / 22px |
| 正文 | 14px / 400 / 24px；长回答行高 1.8 |
| 表格正文 | 13px / 400 / 20px |
| 辅助信息 | 12px / 400 / 18px |
| 指标数字 | 28px / 600 / 34px |

## 布局

- 顶栏高 60px，一级侧栏宽 220px，页面内边距 24px 28px。
- V2 问答：导航 220px、问答 `minmax(640px, 1fr)`、来源栏 320px。
- 完整问答：导航 220px、会话 280px、问答 `minmax(560px, 1fr)`、来源 320px。
- 评测：导航 220px、主表格 `minmax(720px, 1fr)`、详情栏 360px。
- 普通卡片内边距 16px，大面板 20–24px，区块间距 24px。

## 组件

### 导航与图标

- 菜单项高 40px、水平内边距 12px、圆角 8px、图标 18px、图文间距 10px。
- 统一使用 Lucide；按钮图标 16px，线宽 1.75。
- 选中项使用浅紫背景和紫色文字；Hover 只改颜色，不位移。
- 纯图标按钮必须有 `aria-label` 和 Tooltip。

### 按钮与表单

- 按钮高 36px、水平内边距 14px、圆角 8px、字重 500。
- 搜索框高 40px；普通输入和下拉高 36px；文本域最小高 88px。
- 焦点使用紫色边框和 `0 0 0 3px rgb(101 72 232 / 12%)` 焦点环。
- 覆盖默认、Hover、Active、Focus、Disabled 和 Loading。

### 指标卡片

- 卡片高约 112px，内边距 20px，单项最小宽 160px。
- 展示指标名、当前值、阈值、基线差异和文字结论。
- 评分同时显示数值与星级，不能只显示颜色或星星。
- Recall@5、向量 MRR、精排 MRR 使用同一结构。

### 表格与来源

- 表头高 44px，行最小高 72px，单元格水平内边距 16px。
- 主文字最多两行，辅助信息另起一行，操作列固定右侧。
- 来源卡内边距 16px、间距 12px；显示文件、位置、摘要、相关度和原文。
- 答案引用编号与来源卡一一对应，点击后滚动并展开原文。

### 状态与浮层

- 覆盖初始、加载、正常、空数据、搜索无结果、失败、禁用和过期状态。
- 详情栏 320–360px，小屏改为最大 380px 的右侧抽屉。
- 删除必须二次确认；弹窗支持关闭按钮、遮罩、Escape 和焦点返回。

## 动画

- Hover 140–160ms；按钮按下 80ms `scale(.98)`。
- 下拉 160ms 淡入并上移 4px；弹窗 180ms 淡入轻缩放；抽屉 220ms 横向滑入。
- 禁止卡片上浮、导航位移、整页依次飞入和超过 300ms 的常用动画。
- 必须支持 `prefers-reduced-motion: reduce`。

## 响应式

- ≥1440px：完整侧栏、主区和详情栏。
- 960–1439px：缩窄或抽屉化详情栏。
- 768–959px：侧栏折叠为图标栏。
- <768px：顶部菜单；指标单列或双列；表格转卡片；详情全屏抽屉。
- 点击区域至少 44×44px，移动端输入区域避开安全区。

## 内容与验收

- 数量 `1,024`，比例 `75.0%`，评分 `3.68 / 5.0`，空值使用 `—`。
- 状态不能只靠颜色；正文对比度至少 4.5:1；焦点必须可见。
- 每轮至少检查桌面和移动页面，以及空、加载、正常和失败状态。
- 先修结构和信息层级，再修字体间距，最后修颜色、动画和图标。

## 变更规则

- 基线修改必须说明原因、影响页面和验证截图。
- 已冻结 token 优先通过统一变量修改，禁止逐页复制新数值。
- 新设计图只作为候选输入；确认前不得直接替换本基线。
