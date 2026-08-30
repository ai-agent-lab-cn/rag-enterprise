import { useEffect, useState, type ReactNode } from "react";
import { BarChart3, BookOpen, ChevronRight, Database, FileText, Link2, MessageCircle, SearchCheck, ShieldCheck } from "lucide-react";
import { api } from "../api";
import type { AnswerEvaluationSummary, ConversationSummary, KnowledgeBase, User } from "../types";
import { Button } from "./ui/Button";

function MetricCard({ icon, tone, label, value, note, valueClass = "" }: { icon: ReactNode; tone: string; label: string; value: ReactNode; note: string; valueClass?: string }) {
  return <article className="overview-metric"><div className={`overview-icon ${tone}`}>{icon}</div><span>{label}</span><strong className={valueClass}>{value}</strong><small>{note}</small></article>;
}

const ACTIONS = [
  { label: "问答工作台", note: "开始智能问答", path: "/chat", icon: <MessageCircle/>, tone: "is-purple" },
  { label: "管理知识库", note: "创建与管理知识库", path: "/knowledge-bases", icon: <Database/>, tone: "is-green" },
  { label: "上传资料", note: "导入文档到知识库", path: "/data-sources", icon: <Link2/>, tone: "is-blue" },
  { label: "回答评测", note: "评估回答质量", path: "/evaluation#answer", icon: <BarChart3/>, tone: "is-amber" },
  { label: "检索评测", note: "评估检索效果", path: "/evaluation#retrieval", icon: <SearchCheck/>, tone: "is-slate" },
  { label: "系统状态", note: "查看系统运行状态", path: "/system", icon: <BarChart3/>, tone: "is-gray" },
];

export function OverviewPage({ onOpen, onLogout, user }: { onOpen: (path: string) => void; onLogout: () => void; user: User }) {
  const [bases, setBases] = useState<KnowledgeBase[] | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [quality, setQuality] = useState<AnswerEvaluationSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([api.listKnowledgeBases(), api.listAnswerEvaluations()]).then(
      async ([items, reports]) => {
        if (!active) return;
        setBases(items);
        setQuality(reports[0] ?? null);
        const histories = await Promise.all(items.map((item) => api.listConversations(item.knowledge_base_id)));
        if (active) setConversations(histories.flat());
      },
      (reason: unknown) => active && setError(reason instanceof Error ? reason.message : "无法读取概览。"),
    );
    return () => { active = false; };
  }, []);

  const documentCount = bases?.reduce((total, item) => total + item.document_count, 0) ?? 0;
  const chunkCount = bases?.reduce((total, item) => total + item.chunk_count, 0) ?? 0;
  const latestBase = bases?.slice().sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
  const displayName = user.display_name || user.username;
  return (
    <section className="product-page overview-page" aria-labelledby="overview-title">
      <header className="overview-header"><div><h1 id="overview-title">项目概览</h1><p>查看知识库、已索引资料、历史会话和回答质量监控状态。</p></div><button className="overview-user" type="button" onClick={onLogout} aria-label="退出登录" title="退出登录"><span>{displayName.slice(0, 1).toUpperCase()}</span><b>{displayName}</b></button></header>
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {bases === null && !error ? <div className="evaluation-state pulse">正在汇总项目数据…</div> : null}
      {bases ? <>
        <div className="overview-metrics">
          <MetricCard icon={<BookOpen/>} tone="is-purple" label="知识库" value={bases.length} note="规范隔离的独立空间"/>
          <MetricCard icon={<FileText/>} tone="is-blue" label="已索引资料" value={documentCount} note={`${chunkCount} 个可检索片段`}/>
          <MetricCard icon={<MessageCircle/>} tone="is-green" label="历史会话" value={conversations.length} note="回答记录可追溯"/>
          <MetricCard icon={<ShieldCheck/>} tone="is-amber" label="回答质量门" value={quality ? quality.passed ? "通过" : "未通过" : "暂无"} valueClass={quality?.passed ? "status-pass" : quality ? "status-fail" : ""} note={quality?.prompt_version ?? "等待正式报告"}/>
        </div>

        <div className="overview-panels">
          <section className="overview-panel knowledge-overview"><header><h2>知识库</h2><Button variant="link" onClick={() => onOpen("/knowledge-bases")}>查看全部 <ChevronRight size={13}/></Button></header>
            {latestBase ? <button className="latest-base" onClick={() => onOpen(`/knowledge-bases/${latestBase.knowledge_base_id}`)}><span className="latest-base-icon"><FileText/></span><span className="latest-base-copy"><span><b>{latestBase.name}</b><em>{latestBase.is_default ? "默认" : "独立"}</em></span><small>{latestBase.description || "暂无说明"}</small></span><span className="latest-base-meta"><small>更新时间：{new Date(latestBase.updated_at).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })}</small><b>{latestBase.document_count} 份资料</b></span></button> : <p className="empty-copy">还没有知识库。</p>}
          </section>
          <section className="overview-panel quality-overview"><header><h2>质量监控</h2>{quality ? <span className={quality.passed ? "quality-status" : "quality-status is-failed"}>{quality.passed ? "主指标通过" : "存在未通过指标"}</span> : null}</header>{quality ? <div className="quality-overview-body"><div className="quality-details"><div><strong>{quality.dataset_version}</strong><p>Prompt {quality.prompt_version}<br/>+ {quality.models.generation}</p></div></div><Button variant="link" onClick={() => onOpen("/evaluation#answer")}>查看回答评测详情 <ChevronRight size={13}/></Button></div> : <p className="empty-copy">还没有正式回答评测报告。</p>}</section>
        </div>

        <section className="quick-actions"><h2>快捷操作</h2><div>{ACTIONS.filter((action) => user.role === "admin" || action.path !== "/system").map((action) => <button key={action.path} onClick={() => onOpen(action.path)}><span className={`action-icon ${action.tone}`}>{action.icon}</span><b>{action.label}</b><small>{action.note}</small></button>)}</div></section>
      </> : null}
    </section>
  );
}
