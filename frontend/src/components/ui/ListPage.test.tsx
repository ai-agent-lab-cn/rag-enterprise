import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Badge } from "./Badge";
import { DataTable, type Column } from "./DataTable";
import { Pagination } from "./Pagination";
import { RowActions } from "./RowActions";
import { Toolbar } from "./Toolbar";

afterEach(cleanup);

/**
 * 组合测试。
 *
 * 阶段 1 的每个组件都单独测过且全绿，但它们拼起来是否还成立，此前没有任何测试覆盖——
 * 终审因此漏掉了两处错配。这个文件的断言全部指向「A 组件产出喂给 B 组件」的接缝，
 * 不重复单组件已经测过的东西。
 */

type Row = { id: string; name: string; kind: string; count: number; blocked?: string };

const ROWS: Row[] = [
  { id: "a", name: "企业知识库", kind: "独立知识库", count: 5 },
  { id: "b", name: "默认知识库", kind: "默认知识库", count: 0, blocked: "默认知识库不能删除" },
];

function listPageColumns(onOpen: (id: string) => void): Column<Row>[] {
  return [
    {
      key: "name",
      header: "知识库名称",
      width: "240px",
      truncate: false,
      render: (row) => (
        <span className="flex items-center gap-2">
          <button onClick={() => onOpen(row.id)}>{row.name}</button>
          <Badge shape="type">{row.kind}</Badge>
        </span>
      ),
    },
    { key: "count", header: "文档数量", numeric: true, render: (row) => row.count },
    {
      key: "actions",
      header: "操作",
      align: "right",
      width: "160px",
      truncate: false,
      render: (row) => (
        <RowActions
          rowLabel={row.name}
          actions={[
            { label: "详情", onSelect: () => onOpen(row.id) },
            { label: "编辑", onSelect: () => {} },
            { label: "删除", onSelect: () => {}, tone: "destructive", blockedReason: row.blocked },
          ]}
        />
      ),
    },
  ];
}

const EMPTY = { kind: "empty", title: "还没有知识库", description: "创建一个后即可上传资料。" } as const;

test("名称列里的徽章不被 truncate 裁掉", () => {
  // DataTable 给每个 td 无条件加了 truncate（overflow:hidden + nowrap）。
  // 「名称 + 类型徽章」这种组合列因此会被裁——而修复这个截断正是 spec 点名的问题之一。
  // 这条断言锁住：名称列必须能同时容纳两者，不能靠调用方自己想办法。
  render(
    <DataTable rows={ROWS} columns={listPageColumns(() => {})} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  const cell = screen.getByText("企业知识库").closest("td")!;
  expect(within(cell).getByText("独立知识库")).toBeVisible();
  expect(cell.className.split(/\s+/)).not.toContain("truncate");
});

test("操作列不被 truncate 裁掉", () => {
  render(
    <DataTable rows={ROWS} columns={listPageColumns(() => {})} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  const row = screen.getByRole("button", { name: "企业知识库" }).closest("tr") as HTMLElement;
  const action = within(row).getByRole("button", { name: "详情" });
  expect(action.closest("td")!.className.split(/\s+/)).not.toContain("truncate");
});

test("平铺形态的禁用原因不会在行内撑出一行小字", () => {
  // 这条守护的是 Task 2 的改动，现在是红的。
  //
  // 它测的不是 `h-14` 这个 class——DataTable 无条件给每行都加它，检查它等于什么都没测。
  // 真正会撑破行高的是内容溢出：Button 用块级 <small> 渲染 blockedReason 时，
  // 行的实际高度会超过 h-14 而 class 纹丝不动。所以这里断言的是可验证的结构事实：
  // 行内不存在块级说明文字。
  //
  // 所有操作均为平铺形态，禁用原因不能变成撑高表格行的块级文字。
  const columns: Column<Row>[] = [
    { key: "name", header: "知识库名称", render: (row) => row.name },
    {
      key: "actions",
      header: "操作",
      align: "right",
      render: (row) => (
        <RowActions
          rowLabel={row.name}
          actions={[
            { label: "详情", onSelect: () => {} },
            { label: "编辑", onSelect: () => {}, blockedReason: row.blocked },
          ]}
        />
      ),
    },
  ];
  render(
    <DataTable rows={ROWS} columns={columns} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );

  const blockedRow = screen.getByRole("cell", { name: "默认知识库" }).closest("tr")!;
  expect(blockedRow.querySelector("small")).toBeNull();
});

test("工具栏的批量选中数与表格的选择状态是同一个来源", () => {
  const onChange = vi.fn();
  const selected = ["a"];
  render(
    <div>
      <Toolbar batch={{ count: selected.length, children: <button>删除 {selected.length} 项</button> }} />
      <DataTable
        rows={ROWS}
        columns={listPageColumns(() => {})}
        rowKey={(row) => row.id}
        emptyState={EMPTY}
        label="知识库列表"
        selection={{ selected, onChange, rowLabel: (row) => row.name }}
      />
    </div>,
  );

  // 页面把同一个 selected 分别喂给 Toolbar 和 DataTable，两者必须显示一致。
  expect(screen.getByRole("status")).toHaveTextContent("已选 1 项");
  expect(screen.getByRole("checkbox", { name: "选择 企业知识库" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "选择全部" })).toHaveAttribute("data-state", "indeterminate");
});

test("空态时分页不出现", () => {
  // 列表页的惯常写法是三个组件并排渲染。空态下还挂一个「第 1 页」是噪音，
  // Pagination 自己会在只有一页时返回 null——这条锁住那个行为在组合场景下也成立。
  render(
    <div>
      <DataTable rows={[]} columns={listPageColumns(() => {})} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />
      <Pagination page={0} hasNext={false} onChange={() => {}} label="知识库分页" />
    </div>,
  );

  expect(screen.getByRole("heading", { name: "还没有知识库" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation")).toBeNull();
});

test("加载态与空态的外框一致", () => {
  // 阶段 1 修过一次「空态丢容器」。这条防止它复发，并把断言放在组合层面：
  // 用户看到的是同一张卡片在三种状态间切换，边框不该忽有忽无。
  const { rerender, container } = render(
    <DataTable rows={null} columns={listPageColumns(() => {})} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );
  const loadingBox = container.firstElementChild!.className;

  rerender(
    <DataTable rows={[]} columns={listPageColumns(() => {})} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );
  const emptyBox = container.firstElementChild!.className;

  rerender(
    <DataTable rows={ROWS} columns={listPageColumns(() => {})} rowKey={(row) => row.id} emptyState={EMPTY} label="知识库列表" />,
  );
  const dataBox = container.firstElementChild!.className;

  expect(emptyBox).toBe(loadingBox);
  expect(dataBox).toBe(loadingBox);
});
