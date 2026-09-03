import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { Badge } from "./Badge";

afterEach(cleanup);

test("状态与类型用不同形状，从轮廓就能区分语义", () => {
  render(
    <div>
      <Badge shape="status" tone="success">可用</Badge>
      <Badge shape="type">独立知识库</Badge>
    </div>,
  );

  // 状态会变（可用→处理中→失败），画成胶囊；类型是固有属性，画成方角标签。
  // 这是这个组件唯一的结构性决定：形状携带信息，不只是好看。
  expect(screen.getByText("可用").className).toContain("rounded-full");
  expect(screen.getByText("独立知识库").className.split(/\s+/)).toContain("rounded-sm");
});

test("五个 tone 各有底色，neutral 不带任何语义色", () => {
  render(
    <div>
      <Badge tone="neutral">中性</Badge>
      <Badge tone="success">成功</Badge>
      <Badge tone="warning">警告</Badge>
      <Badge tone="danger">危险</Badge>
      <Badge tone="brand">品牌</Badge>
    </div>,
  );

  const classOf = (text: string) => screen.getByText(text).className.split(/\s+/);
  expect(classOf("成功")).toContain("bg-success-subtle");
  expect(classOf("危险")).toContain("bg-danger-subtle");
  expect(classOf("品牌")).toContain("bg-brand-subtle");
  // 中性不得带语义色——大多数徽章都是中性的，让它去抢语义色会稀释真正的告警。
  for (const cls of classOf("中性")) {
    expect(cls).not.toMatch(/^bg-(success|danger|brand|warning)/);
  }
});

test("默认是 neutral 状态胶囊", () => {
  render(<Badge>默认</Badge>);

  const cls = screen.getByText("默认").className.split(/\s+/);
  expect(cls).toContain("rounded-full");
  expect(cls).toContain("bg-canvas");
});

test("外部 className 能覆盖内部同类样式", () => {
  render(<Badge className="bg-transparent">自定义</Badge>);

  const cls = screen.getByText("自定义").className.split(/\s+/);
  expect(cls).toContain("bg-transparent");
  expect(cls).not.toContain("bg-canvas");
});
