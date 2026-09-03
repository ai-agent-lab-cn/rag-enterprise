import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { DataTable, type Column } from "./DataTable";

afterEach(cleanup);

type Row = { id: string; name: string; count: number };

const ROWS: Row[] = [
  { id: "a", name: "企业知识库", count: 5 },
  { id: "b", name: "默认知识库", count: 0 },
];

const COLUMNS: Column<Row>[] = [
  { key: "name", header: "知识库名称", render: (row) => row.name },
  { key: "count", header: "文档数量", numeric: true, render: (row) => row.count },
];

const EMPTY = { kind: "empty", title: "还没有知识库", description: "创建一个后即可上传资料。" } as const;

test("渲染表头与数据行", () => {
  render(
    <DataTable rows={ROWS} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  const table = screen.getByRole("table", { name: "知识库列表" });
  expect(within(table).getByRole("columnheader", { name: "知识库名称" })).toBeInTheDocument();
  expect(within(table).getByText("企业知识库")).toBeInTheDocument();
  expect(within(table).getAllByRole("row")).toHaveLength(3); // 表头 + 2 行
});

test("每一行都有下边框，最后一行没有", () => {
  render(
    <DataTable rows={ROWS} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  // CLAUDE.md 记过这个真实缺陷：--border 等 5 个变量从未定义，
  // 相关 CSS 声明全部失效，三张表都没有分隔线，靠 hover 底色区分行。
  const bodyRows = screen.getAllByRole("row").slice(1);
  expect(bodyRows[0].className).toContain("border-b");
  expect(bodyRows[bodyRows.length - 1].className).toContain("border-b-0");
});

test("行高统一，compact 只用于超长列表", () => {
  const { rerender } = render(
    <DataTable rows={ROWS} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );
  expect(screen.getAllByRole("row")[1].className).toContain("h-14");

  rerender(
    <DataTable rows={ROWS} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" density="compact" />,
  );
  expect(screen.getAllByRole("row")[1].className).toContain("h-11");
});

test("numeric 列等宽右对齐", () => {
  render(
    <DataTable rows={ROWS} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  // 文档数、切片数逐行宽度不同，整列看着是歪的。tabular-nums 让每个数字等宽。
  const cell = screen.getByText("5").closest("td")!;
  expect(cell.className).toContain("tabular-nums");
  expect(cell.className).toContain("text-right");
});

test("truncate 默认开启", () => {
  render(
    <DataTable rows={ROWS} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  const cell = screen.getByText("企业知识库").closest("td")!;
  expect(cell.className.split(/\s+/)).toContain("truncate");
});

test("truncate: false 时关闭截断", () => {
  const columns: Column<Row>[] = [
    { key: "name", header: "知识库名称", truncate: false, render: (row) => row.name },
    { key: "count", header: "文档数量", numeric: true, render: (row) => row.count },
  ];
  render(
    <DataTable rows={ROWS} columns={columns} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  const cell = screen.getByText("企业知识库").closest("td")!;
  expect(cell.className.split(/\s+/)).not.toContain("truncate");
});

test("width 落到 col 元素上，列宽不随内容变化", () => {
  const columns: Column<Row>[] = [
    { key: "name", header: "知识库名称", width: "200px", render: (row) => row.name },
    { key: "count", header: "文档数量", render: (row) => row.count },
  ];
  render(
    <DataTable rows={ROWS} columns={columns} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  // 用 <col> 而不是给 th 加 className：th 上的宽度会被内容撑开，
  // <col> 配 table-fixed 才是硬约束。列宽漂移就是这么来的。
  const col = document.querySelector("col");
  expect(col).toHaveStyle({ width: "200px" });
  expect(screen.getByRole("table").className).toContain("table-fixed");
});

test("rows 为 null 时显示骨架而不是文案", () => {
  render(
    <DataTable rows={null} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  expect(screen.queryByText("还没有知识库")).toBeNull();
  expect(screen.getByRole("status")).toBeInTheDocument();
  // 骨架行数固定为 3，行高与真实行一致，数据到达时布局不跳。
  expect(screen.getAllByRole("row").slice(1)).toHaveLength(3);
});

test("空数组时渲染 emptyState 而不是空表格", () => {
  render(
    <DataTable rows={[]} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  expect(screen.getByRole("heading", { name: "还没有知识库" })).toBeInTheDocument();
  expect(screen.queryByRole("table")).toBeNull();
});

test("空态与加载态、数据态共用同一个带边框的卡片容器", () => {
  // 同一张列表页，加载态/数据态有 rounded-lg border 的卡片外观，空态若丢掉这层
  // 容器，视觉上会像「空态时组件坏了」。
  const { container } = render(
    <DataTable rows={[]} columns={COLUMNS} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  const wrapper = container.firstElementChild as HTMLElement;
  const wrapperClasses = wrapper.className.split(/\s+/);
  expect(wrapperClasses).toContain("rounded-lg");
  expect(wrapperClasses).toContain("border");
  expect(wrapperClasses).toContain("border-line");
});

test("选择列独立成列，不与首列内容挤在一起", async () => {
  const onChange = vi.fn();
  render(
    <DataTable
      rows={ROWS}
      columns={COLUMNS}
      rowKey={(row) => row.id}
      emptyState={EMPTY}
      label="知识库列表"
      selection={{ selected: [], onChange, rowLabel: (row) => row.name }}
    />,
  );

  // DocumentPanel.tsx:120 把 checkbox 和文件名塞进同一个 <td>，
  // 截图上 checkbox 浮在文件名上方。这里它必须是自己的一列。
  const firstBodyRow = screen.getAllByRole("row")[1];
  const cells = within(firstBodyRow).getAllByRole("cell");
  expect(within(cells[0]).getByRole("checkbox")).toBeInTheDocument();
  expect(cells[0].textContent).toBe("");

  await userEvent.click(screen.getByRole("checkbox", { name: "选择 企业知识库" }));
  expect(onChange).toHaveBeenCalledWith(["a"]);
});

test("部分选中时表头是 indeterminate，点击后补齐为全选", async () => {
  const onChange = vi.fn();
  render(
    <DataTable
      rows={ROWS}
      columns={COLUMNS}
      rowKey={(row) => row.id}
      emptyState={EMPTY}
      label="知识库列表"
      selection={{ selected: ["a"], onChange, rowLabel: (row) => row.name }}
    />,
  );

  const all = screen.getByRole("checkbox", { name: "选择全部" });
  expect(all).toHaveAttribute("data-state", "indeterminate");

  await userEvent.click(all);
  expect(onChange).toHaveBeenCalledWith(["a", "b"]);
});

test("全选状态下再点击表头即清空", async () => {
  const onChange = vi.fn();
  render(
    <DataTable
      rows={ROWS}
      columns={COLUMNS}
      rowKey={(row) => row.id}
      emptyState={EMPTY}
      label="知识库列表"
      selection={{ selected: ["a", "b"], onChange, rowLabel: (row) => row.name }}
    />,
  );

  await userEvent.click(screen.getByRole("checkbox", { name: "选择全部" }));
  expect(onChange).toHaveBeenCalledWith([]);
});
