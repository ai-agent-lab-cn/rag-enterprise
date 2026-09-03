import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { api } from "../api";
import type { AuditEvent } from "../types";
import { TopbarPortal } from "./TopbarPortal";
import { Badge } from "./ui/Badge";
import { Column, DataTable } from "./ui/DataTable";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input } from "./ui/Input";
import { Pagination } from "./ui/Pagination";
import { Select } from "./ui/Select";
import { Toolbar } from "./ui/Toolbar";

const ACTION_LABELS: Record<string, string> = {
  "auth.login": "登录",
  "auth.logout": "退出",
  "member.create": "创建成员",
  "member.update": "更新成员",
  "knowledge_base.member_grant": "授予知识库权限",
  "knowledge_base.member_revoke": "撤销知识库权限",
  "document.upload": "上传资料",
  "document.delete": "删除资料",
};

const RESULT_LABEL: Record<AuditEvent["result"], string> = { success: "成功", denied: "已拒绝", failed: "失败" };
const RESULT_TONE: Record<AuditEvent["result"], "success" | "warning" | "danger"> = { success: "success", denied: "warning", failed: "danger" };
const PAGE_SIZE = 50;

export function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [hasNext, setHasNext] = useState(false);
  const [result, setResult] = useState("");
  const [action, setAction] = useState("");
  const [page, setPage] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .listAuditEvents({ result, action: action.trim(), offset: page * PAGE_SIZE, limit: PAGE_SIZE + 1 })
      .then((value) => {
        if (!active) return;
        setHasNext(value.length > PAGE_SIZE);
        setEvents(value.slice(0, PAGE_SIZE));
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "审计记录读取失败");
      });
    return () => {
      active = false;
    };
  }, [result, action, page]);
  const changeAction = (value: string) => {
    setEvents(null);
    setError("");
    setAction(value);
    setPage(0);
  };
  const changeResult = (value: string) => {
    setEvents(null);
    setError("");
    setResult(value);
    setPage(0);
  };
  const changePage = (value: number) => {
    setEvents(null);
    setError("");
    setPage(value);
  };
  const filtered = Boolean(action.trim() || result);
  const columns: Column<AuditEvent>[] = [
    {
      key: "action", header: "操作", width: "210px", truncate: false,
      render: (event) => (
        <span className="flex min-w-0 flex-col">
          <strong className="truncate font-medium text-ink">{ACTION_LABELS[event.action] ?? event.action}</strong>
          <code className="truncate text-sm text-ink-faint">{event.action}</code>
        </span>
      ),
    },
    {
      key: "result", header: "结果", width: "90px",
      render: (event) => <Badge shape="status" tone={RESULT_TONE[event.result]}>{RESULT_LABEL[event.result]}</Badge>,
    },
    { key: "occurred_at", header: "发生时间", width: "160px", render: (event) => new Date(event.occurred_at).toLocaleString("zh-CN") },
    { key: "actor", header: "操作者", width: "150px", render: (event) => `${event.actor_role ?? "匿名"} · ${event.actor_hash?.slice(0, 10) ?? "—"}` },
    { key: "resource", header: "资源", width: "170px", render: (event) => `${event.resource_type} · ${event.resource_id ?? "—"}` },
    {
      key: "request_id", header: "请求 ID", width: "130px",
      render: (event) => <span className="font-mono text-sm" title={event.request_id}>{event.request_id.slice(0, 10)}</span>,
    },
    {
      key: "event_hash", header: "事件哈希", width: "150px",
      render: (event) => <span className="font-mono text-sm" title={event.event_hash}>{event.event_hash.slice(0, 16)}</span>,
    },
  ];
  return (
    <section className="mx-auto max-w-[1440px] p-[26px_24px_52px] max-[768px]:p-[20px_14px_36px] min-[1025px]:p-[20px_20px_40px]" aria-label="审计记录">
      <TopbarPortal>
        <Badge shape="status" tone="success">哈希链由服务端校验</Badge>
      </TopbarPortal>
      <Toolbar
        filters={<>
          {/* 放大镜以前靠 `.audit-filters label` 那个 40px 高的边框容器托着，input 自身
              border:0。改用 Input 组件后边框回到输入框上，图标改为绝对定位——和 Select
              的下拉箭头同一个做法。 */}
          <label className="relative flex items-center">
            <span className="sr-only">操作名称</span>
            <Search size={14} aria-hidden className="pointer-events-none absolute left-2 text-ink-faint" />
            <Input
              size="sm"
              className="w-72 pl-7"
              value={action}
              onChange={(event) => changeAction(event.target.value)}
              placeholder="筛选操作，例如 member.update"
              pattern="[a-z][a-z0-9_.\-]+"
            />
          </label>
          <Select size="sm" className="w-28" aria-label="结果筛选" value={result} onChange={(event) => changeResult(event.target.value)}>
            <option value="">全部结果</option>
            <option value="success">成功</option>
            <option value="denied">已拒绝</option>
            <option value="failed">失败</option>
          </Select>
        </>}
      />
      {error ? (
        <ErrorBanner>{error}</ErrorBanner>
      ) : (
        <>
          <DataTable
            rows={events}
            columns={columns}
            rowKey={(event) => event.event_id}
            label="审计记录"
            density="compact"
            emptyState={filtered
              ? { kind: "filtered", title: "没有匹配的审计记录", description: "调整筛选条件后重试。" }
              : { kind: "empty", title: "还没有审计记录", description: "等待敏感操作产生新的记录。" }}
          />
          {events !== null ? <Pagination page={page} hasNext={hasNext} onChange={changePage} label="审计记录分页" /> : null}
        </>
      )}
    </section>
  );
}
