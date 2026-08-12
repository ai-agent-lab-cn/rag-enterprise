import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ConversationDetail, ConversationSummary, DocumentInfo, KnowledgeBase, QueryResult } from "../types";
import { AnswerPanel } from "./AnswerPanel";
import { DocumentPanel } from "./DocumentPanel";

const EXAMPLES = ["这个项目解决了什么问题？", "系统采用了哪些技术？", "如何评估回答质量？"];

export function ChatPage({ conversationId, onOpen }: { conversationId?: string; onOpen: (path: string) => void }) {
  const initialBase = new URLSearchParams(window.location.search).get("knowledge_base_id") ?? "kb_default";
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [baseId, setBaseId] = useState(initialBase);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [history, setHistory] = useState<ConversationDetail | null>(null);
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const loadBase = useCallback(async (id: string) => {
    // 文档与会话必须使用同一个知识库 ID 并行读取，避免跨库展示。
    const [docs, items] = await Promise.all([api.listKnowledgeBaseDocuments(id), api.listConversations(id)]);
    setDocuments(docs); setConversations(items);
  }, []);
  useEffect(() => { api.listKnowledgeBases().then(setBases, (reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取知识库。")); }, []);
  useEffect(() => { Promise.resolve().then(() => loadBase(baseId)).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取工作台。")); }, [baseId, loadBase]);
  useEffect(() => { if (!conversationId) return; api.getConversation(baseId, conversationId).then(setHistory, (reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取会话。")); }, [baseId, conversationId]);

  const selectBase = (id: string) => { setResult(null); setHistory(null); setBaseId(id); onOpen(`/chat?knowledge_base_id=${id}`); };
  const upload = async (file: File) => { setBusy(true); try { await api.uploadKnowledgeBaseDocument(baseId, file); await loadBase(baseId); } catch (reason) { setError(reason instanceof Error ? reason.message : "上传失败。"); } finally { setBusy(false); } };
  const remove = async (id: string) => { try { await api.deleteKnowledgeBaseDocument(baseId, id); await loadBase(baseId); setResult(null); } catch (reason) { setError(reason instanceof Error ? reason.message : "删除失败。"); } };
  const ask = async (event: FormEvent) => { event.preventDefault(); const value = question.trim(); if (!value) return; setBusy(true); setError(""); try { const answer = await api.queryKnowledgeBase(baseId, value, conversationId); setResult(answer); setQuestion(""); await loadBase(baseId); if (!conversationId && answer.conversation_id) onOpen(`/chat/${answer.conversation_id}?knowledge_base_id=${baseId}`); } catch (reason) { setError(reason instanceof Error ? reason.message : "查询失败。"); } finally { setBusy(false); } };

  return <div className="chat-layout"><aside className="history-panel"><label>当前知识库<select aria-label="当前知识库" value={baseId} onChange={(event) => selectBase(event.target.value)}>{bases.map((item) => <option key={item.knowledge_base_id} value={item.knowledge_base_id}>{item.name}</option>)}</select></label><button className="new-chat" onClick={() => onOpen(`/chat?knowledge_base_id=${baseId}`)}>＋ 新建会话</button><span className="section-kicker">历史会话</span><div className="history-list">{conversations.map((item) => <button className={item.conversation_id === conversationId ? "is-active" : ""} key={item.conversation_id} onClick={() => onOpen(`/chat/${item.conversation_id}?knowledge_base_id=${baseId}`)}><b>{item.title}</b><small>{item.turn_count} 轮 · {new Date(item.updated_at).toLocaleDateString("zh-CN")}</small></button>)}{conversations.length === 0 ? <p className="empty-copy">还没有历史会话。</p> : null}</div></aside>
    <div className="workspace chat-workspace"><DocumentPanel documents={documents} loading={busy} onUpload={upload} onDelete={remove} /><section className="conversation"><div className="hero-copy"><span className="eyebrow">多知识库问答工作台</span><h1>让资料给出<em>有证据的答案。</em></h1><p>当前资料、查询和会话都严格绑定所选知识库。</p></div>{error ? <div className="error-banner" role="alert">{error}</div> : null}
      {history?.records.map((record) => <article className="history-turn" key={record.record_id}><p className="history-question">{record.question}</p><p>{record.answer ?? record.error_message ?? "本次回答失败。"}</p><small>{record.sources.length} 条来源 · {new Date(record.created_at).toLocaleString("zh-CN")}</small></article>)}
      <AnswerPanel result={result} loading={busy && Boolean(question)} /><div className="examples">{EXAMPLES.map((item) => <button key={item} onClick={() => setQuestion(item)}>{item}</button>)}</div><form className="question-box" onSubmit={ask}><label className="sr-only" htmlFor="question">向知识库提问</label><textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="向当前知识库提问…" rows={2} maxLength={2000}/><div className="question-footer"><span>{documents.length ? `正在检索 ${documents.length} 份资料` : "空知识库会稳定提示资料不足"}</span><button disabled={busy || !question.trim()}>{busy ? "处理中" : "提问"} →</button></div></form>
    </section></div></div>;
}
