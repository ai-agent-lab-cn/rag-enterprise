import { useEffect, useState } from "react";
import { api } from "../api";
import type { AnswerEvaluationSummary, ConversationSummary, KnowledgeBase } from "../types";

export function OverviewPage({ onOpen }: { onOpen: (path: string) => void }) {
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
  return (
    <section className="product-page" aria-labelledby="overview-title">
      <header className="page-heading"><div><h1 id="overview-title">项目概览</h1><p>查看知识库、已索引资料、历史会话和回答质量状态。</p></div></header>
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {bases === null && !error ? <div className="evaluation-state pulse">正在汇总工作空间…</div> : null}
      {bases ? <>
        <div className="stat-grid">
          <article><span>知识库</span><strong>{bases.length}</strong><small>相互隔离的数据空间</small></article>
          <article><span>已索引资料</span><strong>{documentCount}</strong><small>{chunkCount} 个可检索片段</small></article>
          <article><span>历史会话</span><strong>{conversations.length}</strong><small>回答记录可追溯</small></article>
          <article><span>回答质量门</span><strong className={quality?.passed ? "status-pass" : "status-fail"}>{quality ? quality.passed ? "通过" : "未通过" : "暂无"}</strong><small>{quality?.prompt_version ?? "等待正式报告"}</small></article>
        </div>
        <div className="overview-grid">
          <section className="surface-card"><div className="section-heading"><div><span className="section-kicker">知识库</span><h2>最近更新</h2></div><button onClick={() => onOpen("/knowledge-bases")}>查看全部 →</button></div>
            <div className="compact-list">{bases.map((item) => <button key={item.knowledge_base_id} onClick={() => onOpen(`/knowledge-bases/${item.knowledge_base_id}`)}><span><b>{item.name}</b><small>{item.description || "暂无说明"}</small></span><em>{item.document_count} 份资料</em></button>)}</div>
          </section>
          <section className="surface-card"><div className="section-heading"><div><span className="section-kicker">质量证据</span><h2>正式回答评测</h2></div></div>
            {quality ? <div className="quality-summary"><span className={quality.passed ? "release-badge" : "release-badge is-failed"}>{quality.passed ? "全部指标通过" : "存在未通过指标"}</span><strong>{quality.dataset_version}</strong><p>Prompt {quality.prompt_version} · {quality.models.generation}</p><button onClick={() => onOpen("/evaluation/answers")}>查看回答评测 →</button></div> : <p className="empty-copy">还没有正式回答评测报告。</p>}
          </section>
        </div>
      </> : null}
    </section>
  );
}
