import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ConversationSummary, DocumentInfo, KnowledgeBase } from "../types";
import { DocumentPanel } from "./DocumentPanel";

export function KnowledgeBaseDetailPage({ id, onOpen }: { id: string; onOpen: (path: string) => void }) {
  const [base, setBase] = useState<KnowledgeBase | null>(null);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => { const [detail, docs, history] = await Promise.all([api.getKnowledgeBase(id), api.listKnowledgeBaseDocuments(id), api.listConversations(id)]); setBase(detail); setDocuments(docs); setConversations(history); }, [id]);
  useEffect(() => { Promise.resolve().then(load).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取知识库。")); }, [load]);
  const upload = async (file: File) => { setBusy(true); setError(""); try { await api.uploadKnowledgeBaseDocument(id, file); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "上传失败。"); } finally { setBusy(false); } };
  const remove = async (documentId: string) => { setError(""); try { await api.deleteKnowledgeBaseDocument(id, documentId); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "删除失败。"); } };
  if (!base && !error) return <section className="product-page"><div className="evaluation-state pulse">正在读取知识库详情…</div></section>;
  return <section className="product-page"><button className="back-link" onClick={() => onOpen("/knowledge-bases")}>← 返回知识库</button>{error ? <div className="error-banner" role="alert">{error}</div> : null}{base ? <>
    <header className="detail-heading"><div><span className="eyebrow">{base.is_default ? "默认知识库" : "独立知识库"}</span><h1>{base.name}</h1><p>{base.description || "暂无说明"}</p></div><button className="primary-action" onClick={() => onOpen(`/chat?knowledge_base_id=${id}`)}>在此知识库提问 →</button></header>
    <div className="detail-layout"><DocumentPanel documents={documents} loading={busy} onUpload={upload} onDelete={remove} /><section className="surface-card"><div className="section-heading"><div><span className="section-kicker">会话历史</span><h2>{conversations.length} 个会话</h2></div></div>{conversations.length ? <div className="compact-list">{conversations.map((item) => <button key={item.conversation_id} onClick={() => onOpen(`/chat/${item.conversation_id}?knowledge_base_id=${id}`)}><span><b>{item.title}</b><small>{new Date(item.updated_at).toLocaleString("zh-CN")}</small></span><em>{item.turn_count} 轮</em></button>)}</div> : <div className="evaluation-state small"><h2>还没有会话</h2><p>从这个知识库开始第一次提问。</p></div>}</section></div>
  </> : null}</section>;
}
