import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Bot, Download, FileText, MessageSquarePlus, Paperclip, Send, Trash2 } from "lucide-react";
import { api } from "../api";
import type { AnswerRecord, ConversationDetail, ConversationSummary, DocumentCategory, DocumentInfo, KnowledgeBase, QueryResult, Source } from "../types";
import { AnswerPanel } from "./AnswerPanel";
import { Modal } from "./Modal";
import { SourceCard } from "./SourceCard";
import { TechnicalDrawer } from "./TechnicalDrawer";

const EXAMPLES = ["这个项目解决了什么问题？", "系统采用了哪些技术？", "如何评估回答质量？"];

interface EvidencePanelProps {
  sources: Source[];
  documents: DocumentInfo[];
  activeRecord: AnswerRecord | null;
  conversation: ConversationDetail | null;
  result: QueryResult | null;
}

function EvidencePanel({ sources, documents, activeRecord, conversation, result }: EvidencePanelProps) {
  const model = result?.model ?? activeRecord?.models.generation ?? "尚未生成回答";
  return (
    <aside className="evidence-panel" aria-label="引用来源">
      <section className="evidence-card">
        <div className="evidence-tabs" role="tablist">
          <button className="is-active" role="tab" aria-selected="true">引用来源 <span>{sources.length}</span></button>
        </div>
        <div className="evidence-heading"><small>按相关度展示</small></div>
        <div className="evidence-list">{sources.length ? sources.map((source, index) => <SourceCard source={source} index={index} key={source.chunk_id}/>) : <div className="evidence-empty"><FileText size={28}/><strong>回答后查看证据</strong><p>答案引用的文件、位置与原文会显示在这里。</p></div>}</div>
      </section>
      <section className="conversation-info">
        <h3>对话信息</h3>
        <dl><div><dt>对话 ID</dt><dd>{conversation?.conversation_id ?? "新会话"}</dd></div><div><dt>消息数量</dt><dd>{conversation?.records.length ?? (result ? 1 : 0)}</dd></div><div><dt>使用模型</dt><dd title={model}>{model}</dd></div><div><dt>资料数量</dt><dd>{documents.length}</dd></div></dl>
      </section>
    </aside>
  );
}

export function ChatPage({ conversationId, onOpen }: { conversationId?: string; onOpen: (path: string) => void }) {
  const initialBase = new URLSearchParams(window.location.search).get("knowledge_base_id") ?? "kb_default";
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [baseId, setBaseId] = useState(initialBase);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [categories, setCategories] = useState<DocumentCategory[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [history, setHistory] = useState<ConversationDetail | null>(null);
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [sourceTypeFilter, setSourceTypeFilter] = useState("");

  const loadBase = useCallback(async (id: string) => {
    const [docs, items, categoryItems] = await Promise.all([api.listKnowledgeBaseDocuments(id), api.listConversations(id), api.listKnowledgeBaseCategories(id)]);
    setDocuments(docs);
    setConversations(items);
    setCategories(categoryItems);
  }, []);
  useEffect(() => { api.listKnowledgeBases().then(setBases, (reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取知识库。")); }, []);
  useEffect(() => { Promise.resolve().then(() => loadBase(baseId)).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取工作台。")); }, [baseId, loadBase]);
  useEffect(() => {
    if (!conversationId) { Promise.resolve().then(() => setHistory(null)); return; }
    api.getConversation(baseId, conversationId).then((value) => { setHistory(value); setResult(null); }, (reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取会话。"));
  }, [baseId, conversationId]);

  const activeRecord = useMemo(() => history?.records.at(-1) ?? null, [history]);
  const historicalResult = useMemo<QueryResult | null>(() => activeRecord ? {
    answer: activeRecord.answer ?? activeRecord.error_message ?? "本次回答失败。",
    answer_status: activeRecord.answer_status ?? (activeRecord.status === "failed" ? "generation_failed" : "answered"),
    error_code: activeRecord.error_code,
    error_message: activeRecord.error_message,
    sources: activeRecord.sources,
    model: activeRecord.models.generation ?? "未记录",
    latency_ms: activeRecord.latency_ms,
    conversation_id: activeRecord.conversation_id,
    record_id: activeRecord.record_id,
    models: activeRecord.models,
    model_metadata: activeRecord.model_metadata,
    prompt_version: activeRecord.prompt_version,
    prompt_hash: activeRecord.prompt_hash,
    generation_governance: activeRecord.generation_governance,
    query_metadata: activeRecord.query_metadata,
  } : null, [activeRecord]);
  const sources = result?.sources ?? activeRecord?.sources ?? [];
  const selectBase = (id: string) => { setResult(null); setHistory(null); setCategoryFilter(""); setTagFilter(""); setSourceTypeFilter(""); setBaseId(id); onOpen(`/chat?knowledge_base_id=${id}`); };
  const newConversation = () => { setResult(null); setHistory(null); onOpen(`/chat?knowledge_base_id=${baseId}`); };
  const openConversation = (id: string) => { setResult(null); onOpen(`/chat/${id}?knowledge_base_id=${baseId}`); };
  const ask = async (event: FormEvent) => { event.preventDefault(); const value = question.trim(); if (!value) return; const tags = tagFilter.split(/[，,]/).map((item) => item.trim()).filter(Boolean); const filters = categoryFilter || tags.length || sourceTypeFilter ? { ...(categoryFilter ? { category_ids: [categoryFilter] } : {}), ...(tags.length ? { tags: [...new Set(tags)] } : {}), ...(sourceTypeFilter ? { source_types: [sourceTypeFilter] } : {}) } : undefined; setBusy(true); setError(""); try { const answer = await api.queryKnowledgeBase(baseId, value, conversationId, filters); setResult(answer); setQuestion(""); await loadBase(baseId); if (!conversationId && answer.conversation_id) onOpen(`/chat/${answer.conversation_id}?knowledge_base_id=${baseId}`); } catch (reason) { setError(reason instanceof Error ? reason.message : "查询失败。"); } finally { setBusy(false); } };
  const removeConversation = async () => { if (!conversationId) return; setBusy(true); try { await api.deleteConversation(baseId, conversationId); await loadBase(baseId); setConfirmDelete(false); onOpen(`/chat?knowledge_base_id=${baseId}`); } catch (reason) { setError(reason instanceof Error ? reason.message : "删除会话失败。"); } finally { setBusy(false); } };
  const exportConversation = () => {
    if (!history) return;
    const content = history.records.map((record) => `# ${record.question}\n\n${record.answer ?? record.error_message ?? "本次回答失败。"}`).join("\n\n---\n\n");
    const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `${history.title || "RAG-对话"}.md`; link.click(); URL.revokeObjectURL(url);
  };

  return <div className="chat-layout">
    <aside className="history-panel">
      <button className="new-chat" onClick={newConversation}><MessageSquarePlus size={17}/> 新建对话</button>
      <div className="history-heading"><h2>最近对话</h2><span>{conversations.length}</span></div>
      <div className="history-list">{conversations.map((item) => <button className={item.conversation_id === conversationId ? "is-active" : ""} key={item.conversation_id} onClick={() => openConversation(item.conversation_id)}><b>{item.title}</b><small>{item.turn_count} 轮 · {new Date(item.updated_at).toLocaleDateString("zh-CN")}</small></button>)}{conversations.length === 0 ? <p className="empty-copy">还没有历史会话，上传资料后开始提问。</p> : null}</div>
    </aside>
    <div className="chat-stage">
      <section className="conversation">
        <header className="conversation-header"><h1>{history?.title ?? "新对话"}</h1><div className="conversation-actions"><button disabled={!history} onClick={exportConversation}><Download size={16}/> 导出对话</button><button disabled={!conversationId} onClick={() => setConfirmDelete(true)}><Trash2 size={16}/> 清空对话</button></div></header>
        <div className="conversation-scroll">
          {error ? <div className="error-banner" role="alert">{error}</div> : null}
          {history?.records.map((record, index) => <div className="turn-pair" key={record.record_id}><article className="user-message"><span>{record.question}</span><b>你</b></article><article className="assistant-message"><span className="assistant-avatar"><Bot size={17}/></span><div><p>{record.answer ?? record.error_message ?? "本次回答失败。"}</p><small>{record.sources.length} 条来源 · {new Date(record.created_at).toLocaleString("zh-CN")}</small>{index === history.records.length - 1 && historicalResult ? <TechnicalDrawer result={historicalResult}/> : null}</div></article></div>)}
          {!history?.records.length && !result ? <AnswerPanel result={null} loading={false} showSources={false}/> : null}
          {result || (busy && Boolean(question)) ? <AnswerPanel result={result} loading={busy && Boolean(question)} showSources={false}/> : null}
        </div>
        <div className="composer-wrap"><div className="examples">{EXAMPLES.map((item) => <button key={item} onClick={() => setQuestion(item)}>{item}</button>)}</div><form className="question-box" onSubmit={ask}><div className="query-filters" aria-label="检索过滤条件"><label>分类<select aria-label="过滤分类" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}><option value="">全部</option>{categories.map((item) => <option key={item.category_id} value={item.category_id}>{item.name}{item.active ? "" : "（已停用）"}</option>)}</select></label><label>标签<input aria-label="过滤标签" value={tagFilter} onChange={(event) => setTagFilter(event.target.value)} placeholder="逗号分隔"/></label><label>来源<select aria-label="过滤来源类型" value={sourceTypeFilter} onChange={(event) => setSourceTypeFilter(event.target.value)}><option value="">全部</option><option value="file">文件</option><option value="object_storage">对象存储</option><option value="web">网页</option><option value="connector">连接器</option></select></label></div><label className="sr-only" htmlFor="question">向知识库提问</label><textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="输入你的问题…" rows={2} maxLength={2000}/><div className="question-footer"><div className="composer-context"><label className="base-picker"><span>知识库：</span><select aria-label="当前知识库" value={baseId} onChange={(event) => selectBase(event.target.value)}>{bases.map((item) => <option key={item.knowledge_base_id} value={item.knowledge_base_id}>{item.name}</option>)}</select></label><span className="connected-documents"><Paperclip size={15}/>{documents.length ? `已连接 ${documents.length} 份资料` : "暂无资料"}</span></div><button aria-label="提问并发送" disabled={busy || !question.trim()}>{busy ? <span className="button-spinner"/> : <Send size={17}/>}</button></div></form><small className="ai-notice">内容由人工智能生成，请注意甄别信息准确性</small></div>
      </section>
      <EvidencePanel sources={sources} documents={documents} activeRecord={activeRecord} conversation={history} result={result}/>
    </div>
    {confirmDelete ? <Modal title="清空当前会话" description="删除后无法恢复，知识库资料不会受到影响。" onClose={() => setConfirmDelete(false)}><footer className="modal-actions"><button className="secondary-action" onClick={() => setConfirmDelete(false)}>取消</button><button className="danger-action" onClick={() => void removeConversation()} disabled={busy}>{busy ? "删除中…" : "确认删除"}</button></footer></Modal> : null}
  </div>;
}
