import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { MetricCard } from "./MetricCard";

afterEach(cleanup);

test("数字与文字数值共用同一字阶和字重", () => {
  render(
    <div>
      <MetricCard icon={<svg />} label="知识库" value={3} note="规范隔离的独立空间" />
      <MetricCard icon={<svg />} label="回答质量门" value="通过" note="v3-grounded-answer-1" />
    </div>,
  );

  // 截图上「3」和「通过」的视觉重量完全不同，四张卡读起来不像一组。
  const numeric = screen.getByText("3").className.split(/\s+/);
  const textual = screen.getByText("通过").className.split(/\s+/);
  expect(numeric).toEqual(textual);
});

test("数值用等宽数字", () => {
  render(<MetricCard icon={<svg />} label="已索引资料" value={128} note="27 个可检索片段" />);

  // 指标卡并排时，非等宽数字会让基线看着参差。
  expect(screen.getByText("128").className).toContain("tabular-nums");
});

test("图标底色恒为中性，tone 只作用于数值", () => {
  render(<MetricCard icon={<svg data-testid="icon" />} label="回答质量门" value="未通过" tone="danger" />);

  // 6 套装饰底色不携带任何信息。颜色只留给数值本身表意。
  const iconBox = screen.getByTestId("icon").parentElement!;
  expect(iconBox.className).toContain("bg-canvas");
  expect(iconBox.className).not.toMatch(/bg-(danger|success|brand|warning)/);
  expect(screen.getByText("未通过").className).toContain("text-danger-text");
});

test("note 可省略", () => {
  render(<MetricCard icon={<svg />} label="历史会话" value={2} />);

  expect(screen.getByText("历史会话")).toBeInTheDocument();
  expect(screen.getByText("2")).toBeInTheDocument();
});
