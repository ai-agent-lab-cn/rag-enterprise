import { useEffect, useState } from "react";
import { FileClock, Search } from "lucide-react";
import { api } from "../api";
import type { AuditEvent } from "../types";

const ACTION_LABELS: Record<string, string> = { "auth.login": "登录", "auth.logout": "退出", "member.create": "创建成员", "member.update": "更新成员", "knowledge_base.member_grant": "授予知识库权限", "knowledge_base.member_revoke": "撤销知识库权限", "document.upload": "上传资料", "document.delete": "删除资料" };

export function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [result, setResult] = useState("");
  const [action, setAction] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { let active = true; api.listAuditEvents(result, action.trim()).then((value) => { if (active) setEvents(value); }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "审计记录读取失败"); }); return () => { active = false; }; }, [result, action]);
  const changeAction = (value: string) => { setEvents(null); setError(""); setAction(value); };
  const changeResult = (value: string) => { setEvents(null); setError(""); setResult(value); };
  return <section className="admin-page" aria-labelledby="audit-title"><header className="page-heading"><div><span className="eyebrow">管理员 · 只读证据</span><h1 id="audit-title">审计记录</h1><p>敏感操作的追加式哈希链记录；不展示业务正文、令牌或密钥。</p></div><span className="status-pill is-success">哈希链由服务端校验</span></header><div className="audit-filters"><label><Search size={15}/><span className="sr-only">操作名称</span><input value={action} onChange={(event) => changeAction(event.target.value)} placeholder="筛选操作，例如 member.update" pattern="[a-z][a-z0-9_.-]+"/></label><select aria-label="结果筛选" value={result} onChange={(event) => changeResult(event.target.value)}><option value="">全部结果</option><option value="success">成功</option><option value="denied">已拒绝</option><option value="failed">失败</option></select></div>{error ? <div className="error-banner" role="alert">{error}</div> : null}{events === null && !error ? <div className="admin-loading" role="status">正在读取审计记录…</div> : null}{events?.length === 0 ? <div className="admin-state"><FileClock/><h2>没有匹配的审计记录</h2><p>调整筛选条件，或等待敏感操作产生新的记录。</p></div> : null}{events?.length ? <div className="audit-list">{events.map((event) => <article key={event.event_id}><header><div><strong>{ACTION_LABELS[event.action] ?? event.action}</strong><code>{event.action}</code></div><span className={`status-pill ${event.result === "success" ? "is-success" : event.result === "denied" ? "is-warning" : "is-danger"}`}>{event.result === "success" ? "成功" : event.result === "denied" ? "已拒绝" : "失败"}</span></header><dl><div><dt>发生时间</dt><dd>{new Date(event.occurred_at).toLocaleString("zh-CN")}</dd></div><div><dt>操作者</dt><dd>{event.actor_role ?? "匿名"} · {event.actor_hash?.slice(0, 10) ?? "—"}</dd></div><div><dt>资源</dt><dd>{event.resource_type} · {event.resource_id ?? "—"}</dd></div><div><dt>请求 ID</dt><dd title={event.request_id}>{event.request_id}</dd></div><div><dt>事件哈希</dt><dd title={event.event_hash}>{event.event_hash.slice(0, 16)}</dd></div></dl></article>)}</div> : null}</section>;
}
