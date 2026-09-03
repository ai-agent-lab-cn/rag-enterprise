import { useEffect, useState } from "react";
import { BarChart3, BookOpen, ChevronRight, Database, FileText, Link2, MessageCircle, SearchCheck, ShieldCheck } from "lucide-react";
import { api } from "../api";
import type { AnswerEvaluationSummary, ConversationSummary, KnowledgeBase, User } from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { ErrorBanner } from "./ui/ErrorBanner";
import { ListItemButton } from "./ui/ListItemButton";
import { MetricCard } from "./ui/MetricCard";

const ACTIONS = [
  { label: "问答工作台", note: "开始智能问答", path: "/chat", icon: <MessageCircle size={22}/> },
  { label: "管理知识库", note: "创建与管理知识库", path: "/knowledge-bases", icon: <Database size={22}/> },
  { label: "上传资料", note: "导入文档到知识库", path: "/data-sources", icon: <Link2 size={22}/> },
  { label: "回答评测", note: "评估回答质量", path: "/evaluation#answer", icon: <BarChart3 size={22}/> },
  { label: "检索评测", note: "评估检索效果", path: "/evaluation#retrieval", icon: <SearchCheck size={22}/> },
  { label: "系统状态", note: "查看系统运行状态", path: "/system", icon: <BarChart3 size={22}/> },
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
  const panelHeading = "m-0 text-[16px] leading-6 text-[#1e293b]";
  return (
    <section className="mx-auto max-w-none min-h-screen bg-[#f8fafc] max-[768px]:p-[20px_14px_36px] min-[1025px]:p-[36px_32px_32px]" aria-labelledby="overview-title">
      <header className="flex items-center justify-between gap-6 max-[768px]:items-start"><div><h1 id="overview-title" className="m-0 text-[22px] leading-[26px] tracking-[-0.02em] text-[#1e293b]">项目概览</h1><p className="mt-[1.5px] mb-0 mx-0 text-[13px] leading-[16px] text-[#475569]">查看知识库、已索引资料、历史会话和回答质量监控状态。</p></div><ListItemButton className="w-auto items-center gap-2 p-0 text-[#475569]" onClick={onLogout} aria-label="退出登录" title="退出登录"><span className="grid h-8 w-8 place-items-center rounded-full bg-[#7c3aed] text-[12px] font-bold text-white">{displayName.slice(0, 1).toUpperCase()}</span><b className="text-[14px] font-medium max-[768px]:hidden">{displayName}</b></ListItemButton></header>
      {error ? <ErrorBanner>{error}</ErrorBanner> : null}
      {bases === null && !error ? <div className="pulse grid min-h-[230px] place-content-center rounded-[10px] border border-dashed border-line-firm bg-surface text-center text-[#8a92a5]">正在汇总项目数据…</div> : null}
      {bases ? <>
        <div className="mt-[18px] grid grid-cols-2 gap-[10px] min-[768px]:mt-6 min-[768px]:grid-cols-4 min-[768px]:gap-4">
          <MetricCard icon={<BookOpen size={16}/>} label="知识库" value={bases.length} note="规范隔离的独立空间"/>
          <MetricCard icon={<FileText size={16}/>} label="已索引资料" value={documentCount} note={`${chunkCount} 个可检索片段`}/>
          <MetricCard icon={<MessageCircle size={16}/>} label="历史会话" value={conversations.length} note="回答记录可追溯"/>
          <MetricCard icon={<ShieldCheck size={16}/>} label="回答质量门" value={quality ? quality.passed ? "通过" : "未通过" : "暂无"} tone={quality ? quality.passed ? "success" : "danger" : "neutral"} note={quality?.prompt_version ?? "等待正式报告"}/>
        </div>

        <div className="mt-6 grid h-min grid-cols-1 items-start gap-4 min-[768px]:grid-cols-[minmax(0,1fr)_330px] min-[768px]:items-stretch min-[1181px]:grid-cols-[minmax(0,1fr)_400px]">
          <section className="min-w-0 h-auto border border-line rounded-[16px] bg-surface p-[18px] min-[768px]:h-full min-[768px]:p-6"><header className="flex items-center justify-between"><h2 className={panelHeading}>知识库</h2><Button variant="link" onClick={() => onOpen("/knowledge-bases")}>查看全部 <ChevronRight size={13}/></Button></header>
            {latestBase ? <ListItemButton className="grid min-w-0 grid-cols-[40px_minmax(0,1fr)] items-center gap-4 rounded-lg bg-[#f8fafc] p-4 text-[#475569] hover:bg-[#f5f7fb] min-[768px]:grid-cols-[40px_minmax(0,1fr)_auto]" onClick={() => onOpen(`/knowledge-bases/${latestBase.knowledge_base_id}`)}><span className="grid h-10 w-10 place-items-center rounded-md bg-[#eef2ff] text-[#7c3aed]"><FileText size={20}/></span><span className="grid min-w-0 gap-1.5"><span className="flex items-center gap-2"><b className="overflow-hidden text-ellipsis whitespace-nowrap text-[15px] text-[#1e293b]">{latestBase.name}</b><Badge shape="type" tone="brand">{latestBase.is_default ? "默认" : "独立"}</Badge></span><small className="overflow-hidden text-ellipsis whitespace-nowrap text-[13px]">{latestBase.description || "暂无说明"}</small></span><span className="grid justify-items-start gap-[7px] col-start-2 min-[768px]:col-auto min-[768px]:justify-items-end"><small className="text-[12px] text-[#94a3b8]">更新时间：{new Date(latestBase.updated_at).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })}</small><b className="text-[12px]">{latestBase.document_count} 份资料</b></span></ListItemButton> : <p className="text-md text-[#737c90] leading-[1.6]">还没有知识库。</p>}
          </section>
          <section className="flex flex-col min-w-0 h-auto border border-line rounded-[16px] bg-surface p-[18px] min-[768px]:h-full min-[768px]:p-6"><header className="flex items-center gap-2"><h2 className={panelHeading}>质量监控</h2>{quality ? <Badge tone={quality.passed ? "success" : "danger"} shape="status">{quality.passed ? "主指标通过" : "存在未通过指标"}</Badge> : null}</header>{quality ? <div className="flex flex-1 flex-col justify-center"><div className="my-[10px] mx-0"><div><strong className="text-[13px] font-normal text-[#475569]">{quality.dataset_version}</strong><p className="mt-[10px] mb-0 mx-0 max-w-[210px] text-[12px] leading-[1.5] text-[#94a3b8]">Prompt {quality.prompt_version}<br/>+ {quality.models.generation}</p></div></div><Button variant="link" onClick={() => onOpen("/evaluation#answer")}>查看回答评测详情 <ChevronRight size={13}/></Button></div> : <p className="text-md text-[#737c90] leading-[1.6]">还没有正式回答评测报告。</p>}</section>
        </div>

        <section className="mt-6"><h2 className={panelHeading}>快捷操作</h2><div className="mt-3 grid grid-cols-2 gap-[10px] min-[768px]:grid-cols-3 min-[768px]:gap-4 min-[1181px]:grid-cols-6">{ACTIONS.filter((action) => user.role === "admin" || action.path !== "/system").map((action) => <ListItemButton key={action.path} className="min-h-[126px] min-w-0 flex-col items-center justify-center rounded-lg border border-line bg-surface p-[14px] text-center text-[#1e293b] hover:border-[#cfc5f4] min-[768px]:h-[140px]" onClick={() => onOpen(action.path)}><span className="grid h-12 w-12 place-items-center rounded-full bg-canvas text-ink-muted">{action.icon}</span><b className="mt-[9px] text-[14px]">{action.label}</b><small className="mt-[3px] overflow-hidden text-ellipsis whitespace-nowrap text-[11px] text-[#94a3b8]">{action.note}</small></ListItemButton>)}</div></section>
      </> : null}
    </section>
  );
}
