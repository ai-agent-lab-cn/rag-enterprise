import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";
import { ProgressSteps } from "./ui/ProgressSteps";
import type { ProgressStep, ProgressStepState } from "./ui/ProgressSteps";

afterEach(cleanup);

/**
 * 步骤条视觉 primitive。
 *
 * 它同时服务任务进度与发布流程，两个调用方对「读屏要不要逐格发声」的要求正好相反，
 * 所以这里测的全是**可访问性契约**，不是像素：状态有没有文字、原因看不看得到、
 * 该隐藏的有没有真的隐藏。颜色与圆点样式不在测试范围内——它们只在浏览器里可验证。
 */

const STATE_CASES: Array<{ label: string; state: ProgressStepState; accessibleName: string }> = [
  { label: "解析", state: "done", accessibleName: "解析：已完成" },
  { label: "切片", state: "current", accessibleName: "切片：进行中" },
  { label: "建索引", state: "todo", accessibleName: "建索引：未开始" },
  { label: "验证", state: "blocked", accessibleName: "验证：需要处理" },
  { label: "构建", state: "failed", accessibleName: "构建：未通过" },
  { label: "同步", state: "retrying", accessibleName: "同步：等待重试" },
];

const THREE_STEPS: ProgressStep[] = [
  { key: "definition", label: "索引定义", state: "done" },
  { key: "build", label: "索引构建", state: "current" },
  { key: "live", label: "当前生效", state: "todo" },
];

test("六种状态各有文字可访问名，不靠颜色区分", () => {
  // 这是这个组件存在的硬要求：`todo` 的圆点里根本没有符号（MARK.todo 是空串），
  // 只有蓝/灰的差别。没有 aria-label 的话，读屏用户听到的六个格子完全一样。
  render(
    <ProgressSteps
      label="任务进度"
      steps={STATE_CASES.map((item) => ({ key: item.state, label: item.label, state: item.state }))}
    />,
  );

  for (const item of STATE_CASES) {
    expect(screen.getByRole("img", { name: item.accessibleName })).toBeInTheDocument();
  }
  // 整条步骤条自己也要有名字，否则读屏里是一堆无归属的图片。
  expect(screen.getByRole("list", { name: "任务进度" })).toBeInTheDocument();
});

test("有原因的格子把原因写进可访问名", () => {
  render(
    <ProgressSteps
      label="发布流程"
      steps={[
        { key: "build", label: "索引构建", state: "done" },
        { key: "evaluation", label: "正式评测", state: "blocked", note: "缺少可用于发布的正式质量报告" },
      ]}
    />,
  );

  expect(
    screen.getByRole("img", { name: "正式评测：需要处理，缺少可用于发布的正式质量报告" }),
  ).toBeInTheDocument();
  // 没有原因的格子只念「标签：状态」，不带多余的标点。
  expect(screen.getByRole("img", { name: "索引构建：已完成" })).toBeInTheDocument();
});

test("原因能被键盘聚焦触发，不是只能悬停", async () => {
  // CLAUDE.md 第一条：原因只挂 title 等于没有——悬停约一秒才出现，触屏看不到，
  // 键盘用户永远看不到。所以带 note 的圆点自己必须可聚焦，且 Tooltip 是 delay={0}。
  render(
    <ProgressSteps
      label="发布流程"
      steps={[
        { key: "build", label: "索引构建", state: "done" },
        { key: "evaluation", label: "正式评测", state: "blocked", note: "缺少可用于发布的正式质量报告" },
      ]}
    />,
  );

  // 没有 note 的格子不该抢 Tab 焦点：它没有原因可给，停在上面只是噪音。
  expect(screen.getByRole("img", { name: "索引构建：已完成" })).not.toHaveAttribute("tabindex");

  const dot = screen.getByRole("img", { name: "正式评测：需要处理，缺少可用于发布的正式质量报告" });
  expect(dot).toHaveAttribute("tabindex", "0");

  // 全条只有这一个可聚焦元素，所以第一次 Tab 必定落在它身上。
  await userEvent.tab();
  expect(dot).toHaveFocus();

  // Radix 把内容渲染进 portal，且同时存在一份 aria-live 副本，所以用 findAllByText。
  const shown = await screen.findAllByText("缺少可用于发布的正式质量报告");
  expect(shown.length).toBeGreaterThan(0);
});

test("describeSteps 为 false 时整条步骤条对读屏隐藏", () => {
  // 任务进度是表格行内的一格，一行念七个格子读屏用户听不完——语义由外层那一句
  // 完整可访问名承担（见 PipelineStepper），这里必须彻底闭嘴。
  const { container } = render(
    <ProgressSteps
      label="上传进度"
      describeSteps={false}
      steps={[
        { key: "parse", label: "解析", state: "done" },
        { key: "chunk", label: "切片", state: "blocked", note: "等待前置任务" },
      ]}
    />,
  );

  expect(screen.queryByRole("img")).toBeNull();
  // aria-hidden 的 ol 不再暴露 list role（byRole 默认跳过无障碍树之外的节点）。
  expect(screen.queryByRole("list")).toBeNull();
  expect(container.querySelector("ol")).toHaveAttribute("aria-hidden", "true");

  // 隐藏的是语义不是像素：标签仍然画在页面上。
  expect(screen.getByText("解析")).toBeInTheDocument();

  // 读屏进不去的子树里放可聚焦元素会制造「Tab 停在一个念不出名字的东西上」，
  // 所以这一支连 Tooltip 都不包。
  expect(container.querySelector("[tabindex]")).toBeNull();
});

test("showPercent 为 null 或 undefined 时不渲染百分比", () => {
  const { rerender } = render(<ProgressSteps label="任务进度" steps={THREE_STEPS} />);
  expect(screen.queryByText(/%$/)).toBeNull();

  rerender(<ProgressSteps label="任务进度" steps={THREE_STEPS} showPercent={null} />);
  expect(screen.queryByText(/%$/)).toBeNull();
});

test("showPercent 给了数字就四舍五入显示，0 也要显示", () => {
  const { rerender } = render(
    <ProgressSteps label="任务进度" steps={THREE_STEPS} showPercent={66.6} />,
  );
  expect(screen.getByText("67%")).toBeInTheDocument();

  // 0 必须落地：写成 `showPercent ? … : null` 会让刚入队的任务整块胶囊消失，
  // 用户看到的是「这一列有时有有时没有」。
  rerender(<ProgressSteps label="任务进度" steps={THREE_STEPS} showPercent={0} />);
  expect(screen.getByText("0%")).toBeInTheDocument();
});

test("格子之间的连线不进可访问性树", () => {
  // 「→」是纯装饰。进了无障碍树就会在每两个格子之间插一句无意义的朗读。
  render(<ProgressSteps label="发布流程" steps={THREE_STEPS} />);

  const arrows = screen.getAllByText("→");
  expect(arrows).toHaveLength(THREE_STEPS.length - 1); // 最后一格后面没有连线
  for (const arrow of arrows) {
    expect(arrow).toHaveAttribute("aria-hidden", "true");
  }
});
