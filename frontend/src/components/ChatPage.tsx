import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bot, Download, FileText, MessageSquarePlus, Paperclip, Send, Square, Trash2 } from "lucide-react";
import { api } from "../api";
import type { AnswerRecord, ConversationDetail, ConversationSummary, DocumentCategory, DocumentInfo, KnowledgeBase, QueryResult, Source } from "../types";
import { AnswerPanel } from "./AnswerPanel";
import { SourceCard } from "./SourceCard";
import { TechnicalDrawer } from "./TechnicalDrawer";
import { Button } from "./ui/Button";
import { Dialog, DialogActions } from "./ui/Dialog";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input } from "./ui/Input";
import { ListItemButton } from "./ui/ListItemButton";
import { Select } from "./ui/Select";

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
    <aside className="min-h-0 min-w-0 overflow-y-auto bg-[#f8f9fc] py-5 px-3.5 min-[1025px]:py-3.5 min-[1025px]:px-[11px] max-[901px]:border-t max-[901px]:border-line" aria-label="引用来源">
      <section className="overflow-hidden rounded-[10px] border border-line bg-surface">
        <div className="grid h-[50px] grid-cols-1 border-b border-line bg-surface min-[1025px]:h-[46px]" role="tablist">
          <ListItemButton className="relative block px-4 text-left text-[14px] font-bold text-[#222a3e]" role="tab" aria-selected="true">引用来源 <span className="ml-[5px] rounded-full bg-brand-subtle px-1.5 py-0.5 text-[9px] font-bold text-[#786bdd]">{sources.length}</span></ListItemButton>
        </div>
        <div className="flex items-center justify-end pt-[11px] pr-3.5 pb-[7px] pl-3.5"><small className="text-[9px] text-ink-faint">按相关度展示</small></div>
        <div className="grid grid-cols-1 gap-[9px] px-3 pb-3.5 max-[901px]:grid-cols-2 max-[768px]:grid-cols-1">{sources.length ? sources.map((source, index) => <SourceCard source={source} index={index} key={source.chunk_id}/>) : <div className="grid min-h-[220px] place-items-center text-center text-ink-faint"><FileText size={28}/><strong className="mt-[10px] text-[13px] text-[#5e667a]">回答后查看证据</strong><p className="mt-[6px] mb-0 max-w-[220px] text-[11px] leading-[1.6]">答案引用的文件、位置与原文会显示在这里。</p></div>}</div>
      </section>
      <section className="mt-3.5 rounded-[9px] border border-line bg-surface p-4 min-[1025px]:mt-[11px] min-[1025px]:p-3.5">
        <h3 className="m-0 mb-3.5 text-[13px]">对话信息</h3>
        <dl className="m-0 grid gap-3"><div className="flex justify-between gap-3"><dt className="m-0 text-[10px] text-ink-faint">对话 ID</dt><dd className="m-0 max-w-[165px] overflow-hidden text-ellipsis whitespace-nowrap text-right text-[10px] text-[#40485c]">{conversation?.conversation_id ?? "新会话"}</dd></div><div className="flex justify-between gap-3"><dt className="m-0 text-[10px] text-ink-faint">消息数量</dt><dd className="m-0 max-w-[165px] overflow-hidden text-ellipsis whitespace-nowrap text-right text-[10px] text-[#40485c]">{conversation?.records.length ?? (result ? 1 : 0)}</dd></div><div className="flex justify-between gap-3"><dt className="m-0 text-[10px] text-ink-faint">使用模型</dt><dd className="m-0 max-w-[165px] overflow-hidden text-ellipsis whitespace-nowrap text-right text-[10px] text-[#40485c]" title={model}>{model}</dd></div><div className="flex justify-between gap-3"><dt className="m-0 text-[10px] text-ink-faint">资料数量</dt><dd className="m-0 max-w-[165px] overflow-hidden text-ellipsis whitespace-nowrap text-right text-[10px] text-[#40485c]">{documents.length}</dd></div></dl>
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
  const [streamingText, setStreamingText] = useState("");
  const [streamingStage, setStreamingStage] = useState("");
  const [streamingSources, setStreamingSources] = useState<Source[]>([]);
  const [pendingQuestion, setPendingQuestion] = useState("");
  const streamController = useRef<AbortController | null>(null);

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
  const sources = result?.sources ?? (streamingSources.length ? streamingSources : activeRecord?.sources) ?? [];
  const selectBase = (id: string) => { setResult(null); setHistory(null); setCategoryFilter(""); setTagFilter(""); setSourceTypeFilter(""); setBaseId(id); onOpen(`/chat?knowledge_base_id=${id}`); };
  const newConversation = () => { setResult(null); setHistory(null); onOpen(`/chat?knowledge_base_id=${baseId}`); };
  const openConversation = (id: string) => { setResult(null); onOpen(`/chat/${id}?knowledge_base_id=${baseId}`); };
  const ask = async (event: FormEvent) => {
    event.preventDefault();
    const value = question.trim();
    if (!value || busy) return;
    const tags = tagFilter.split(/[，,]/).map((item) => item.trim()).filter(Boolean);
    const filters = categoryFilter || tags.length || sourceTypeFilter ? { ...(categoryFilter ? { category_ids: [categoryFilter] } : {}), ...(tags.length ? { tags: [...new Set(tags)] } : {}), ...(sourceTypeFilter ? { source_types: [sourceTypeFilter] } : {}) } : undefined;
    const controller = new AbortController();
    streamController.current = controller;
    setBusy(true); setError(""); setResult(null); setStreamingText(""); setStreamingSources([]); setStreamingStage("正在检索资料"); setPendingQuestion(value); setQuestion("");
    let finalAnswer: QueryResult | null = null;
    let streamError = "";
    try {
      await api.streamKnowledgeBaseQuery(baseId, value, conversationId, filters, controller.signal, (message) => {
        if (message.event === "stage") setStreamingStage(message.data.message);
        else if (message.event === "answer_delta") setStreamingText((current) => current + message.data.text);
        else if (message.event === "sources") setStreamingSources(message.data.items);
        else if (message.event === "replace") setStreamingText(message.data.answer);
        else if (message.event === "final") { finalAnswer = message.data; setResult(message.data); setStreamingText(""); }
        else if (message.event === "error") { streamError = message.data.message; setError(message.data.message); }
      });
      if (streamError) return;
      window.dispatchEvent(new Event("rag-generation-status-changed"));
      await loadBase(baseId);
      if (!conversationId && finalAnswer?.conversation_id) onOpen(`/chat/${finalAnswer.conversation_id}?knowledge_base_id=${baseId}`);
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) setError(reason instanceof Error ? reason.message : "查询失败。");
      else setStreamingStage("已停止生成");
    } finally {
      streamController.current = null; setBusy(false); setPendingQuestion("");
    }
  };
  const removeConversation = async () => { if (!conversationId) return; setBusy(true); try { await api.deleteConversation(baseId, conversationId); await loadBase(baseId); setConfirmDelete(false); onOpen(`/chat?knowledge_base_id=${baseId}`); } catch (reason) { setError(reason instanceof Error ? reason.message : "删除会话失败。"); } finally { setBusy(false); } };
  const exportConversation = () => {
    if (!history) return;
    const content = history.records.map((record) => `# ${record.question}\n\n${record.answer ?? record.error_message ?? "本次回答失败。"}`).join("\n\n---\n\n");
    const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `${history.title || "RAG-对话"}.md`; link.click(); URL.revokeObjectURL(url);
  };

  return <div className="grid grid-cols-1 h-auto min-h-[calc(100vh-108px)] overflow-visible bg-surface min-[768px]:min-h-[calc(100vh-60px)] min-[901px]:grid-cols-[220px_minmax(0,1fr)] min-[901px]:h-[calc(100vh-60px)] min-[901px]:min-h-[640px] min-[901px]:overflow-hidden min-[1025px]:grid-cols-[240px_minmax(0,1fr)] min-[1025px]:h-[calc(100vh-56px)] min-[1025px]:min-h-[620px]">
    <aside className="flex min-h-0 min-w-0 flex-col border-b border-line bg-[rgba(255,255,255,0.98)] p-3 max-h-[260px] min-[901px]:max-h-none min-[901px]:border-b-0 min-[901px]:border-r min-[901px]:border-r-line min-[768px]:pt-5 min-[768px]:px-3.5 min-[768px]:pb-3.5 min-[1025px]:pt-3.5 min-[1025px]:px-[11px] min-[1025px]:pb-[11px]">
      <Button className="mb-[18px] w-full" onClick={newConversation}><MessageSquarePlus size={17}/> 新建对话</Button>
      <div className="mx-1.5 mb-2 flex items-center justify-between max-[768px]:hidden"><h2 className="m-0 text-[12px] font-semibold text-ink-faint">最近对话</h2><span className="grid h-[22px] min-w-[22px] place-items-center rounded-full bg-brand-subtle text-[10px] text-[#5d50cc]">{conversations.length}</span></div>
      <div className="m-0 grid grid-cols-1 min-h-0 overflow-y-auto max-[901px]:grid-cols-2">{conversations.map((item) => { const active = item.conversation_id === conversationId; return <ListItemButton active={active} className={`relative grid min-h-[66px] gap-[7px] rounded-[7px] p-3 mb-1 max-[768px]:min-w-[190px] min-[1025px]:min-h-[58px] min-[1025px]:p-[10px] ${active ? "bg-[linear-gradient(100deg,#f0edff,#f7f5ff)] text-[#493cc4] shadow-[inset_3px_0_0_var(--color-brand)]" : "text-[#596176] hover:bg-[#f0eeff] hover:text-[#493cc4]"}`} key={item.conversation_id} onClick={() => openConversation(item.conversation_id)}><b className="overflow-hidden text-ellipsis whitespace-nowrap text-[13px] min-[1025px]:text-[12px]">{item.title}</b><small className="text-[10px] text-ink-faint">{item.turn_count} 轮 · {new Date(item.updated_at).toLocaleDateString("zh-CN")}</small></ListItemButton>; })}{conversations.length === 0 ? <p className="text-md text-[#737c90] leading-[1.6]">还没有历史会话，上传资料后开始提问。</p> : null}</div>
    </aside>
    <div className="grid grid-cols-1 min-w-0 min-h-0 bg-canvas min-[901px]:grid-cols-[minmax(430px,1fr)_290px] min-[1025px]:grid-cols-[minmax(520px,1fr)_300px]">
      <section className="grid grid-rows-[auto_minmax(0,1fr)_auto] min-h-0 min-w-0 border-r border-line bg-[#fbfcff] max-[901px]:min-h-[720px] max-[901px]:border-r-0 max-[768px]:min-h-[680px]">
        <header className="flex h-[72px] items-center justify-between gap-4 border-b border-line bg-surface px-6 max-[768px]:h-[62px] max-[768px]:px-3.5 min-[1025px]:h-16 min-[1025px]:px-5"><h1 className="m-0 max-w-[520px] overflow-hidden text-ellipsis whitespace-nowrap text-[17px] max-[768px]:max-w-[180px] max-[768px]:text-[15px] min-[1025px]:text-[14px]">{history?.title ?? "新对话"}</h1><div className="flex gap-2"><Button variant="outline" size="sm" blockedReason={history ? undefined : "当前对话还没有内容"} onClick={exportConversation}><Download size={16}/> 导出对话</Button><Button variant="outline" size="sm" className="text-danger-text hover:bg-danger-subtle" blockedReason={conversationId ? undefined : "当前是新对话，没有内容可清空"} onClick={() => setConfirmDelete(true)}><Trash2 size={16}/> 清空对话</Button></div></header>
        <div className="min-h-0 overflow-y-auto scroll-smooth pt-[26px] px-7 pb-[18px] max-[768px]:pt-[18px] max-[768px]:px-3 min-[768px]:px-[18px] min-[1025px]:pt-5 min-[1025px]:px-[22px] min-[1025px]:pb-3.5">
          {/* 弹层开着时错误只在弹层里显示：这条横幅在 Radix 的 aria-hidden 背景里。 */}
          {error && !confirmDelete ? <ErrorBanner>{error}</ErrorBanner> : null}
          {history?.records.map((record, index) => <div className="mx-auto mb-[30px] grid max-w-[760px] gap-[18px]" key={record.record_id}><article className="flex items-start justify-end gap-2.5"><span className="block max-w-[78%] rounded-[14px_14px_3px_14px] bg-[#eeeaff] px-4 py-[13px] leading-[1.65] text-[#332878] max-[768px]:max-w-[85%]">{record.question}</span><b className="grid h-[30px] w-[30px] flex-none place-items-center rounded-full bg-[#9a8ce8] text-[11px] font-bold text-white">你</b></article><article className="flex items-start gap-2.5"><span className="grid h-[30px] w-[30px] flex-none place-items-center rounded-full bg-brand text-white"><Bot size={17}/></span><div className="max-w-[min(86%,720px)] rounded-[3px_14px_14px_14px] border border-line bg-surface py-4 px-[18px] shadow-[0_5px_18px_rgba(31,38,63,0.04)] max-[768px]:max-w-[calc(100%-40px)] max-[768px]:p-[13px]"><p className="m-0 leading-[1.85] whitespace-pre-wrap text-[#343c50]">{record.answer ?? record.error_message ?? "本次回答失败。"}</p><small className="block mt-3 text-[10px] text-ink-faint">{record.sources.length} 条来源 · {new Date(record.created_at).toLocaleString("zh-CN")}</small>{index === history.records.length - 1 && historicalResult ? <TechnicalDrawer result={historicalResult}/> : null}</div></article></div>)}
          {pendingQuestion ? <article className="mx-auto mb-[18px] flex max-w-[760px] items-start justify-end gap-2.5"><span className="block max-w-[78%] rounded-[14px_14px_3px_14px] bg-[#eeeaff] px-4 py-[13px] leading-[1.65] text-[#332878]">{pendingQuestion}</span><b className="grid h-[30px] w-[30px] flex-none place-items-center rounded-full bg-[#9a8ce8] text-[11px] font-bold text-white">你</b></article> : null}
          {!history?.records.length && !result && !busy && !streamingText ? <AnswerPanel result={null} loading={false} showSources={false}/> : null}
          {result || busy || streamingText ? <AnswerPanel result={result} loading={busy} streamingText={streamingText} streamingStage={streamingStage} showSources={false}/> : null}
          {busy ? <div className="mx-auto mb-4 flex max-w-[760px] justify-end"><Button type="button" size="sm" variant="outline" onClick={() => streamController.current?.abort()}><Square size={14}/> 停止生成</Button></div> : null}
        </div>
        <div className="pt-0 px-3 pb-3 bg-[linear-gradient(180deg,rgba(251,252,255,0),#fbfcff_22%)] [&>*]:mx-auto [&>*]:max-w-[760px] min-[768px]:px-[18px] min-[768px]:pb-3.5 min-[1025px]:px-[22px] min-[1025px]:pb-2.5"><div className="flex flex-nowrap gap-[7px] overflow-x-auto mt-2 mb-[9px] max-[768px]:mt-1">{EXAMPLES.map((item) => <Button variant="outline" size="sm" key={item} onClick={() => setQuestion(item)}>{item}</Button>)}</div><form className="overflow-hidden rounded-[9px] border border-[#c8c2f5] bg-surface shadow-[0_6px_20px_rgba(75,56,192,0.06)] focus-within:border-[#7568df] focus-within:shadow-[0_0_0_3px_#efedff]" onSubmit={ask}>{/* label 文字要 shrink-0：它和 w-full 的输入框同处一个 flex，不锁住就会被挤成两行。 */}<div className="grid grid-cols-[minmax(100px,0.8fr)_minmax(150px,1.4fr)_minmax(110px,0.8fr)] gap-2 border-b border-divider bg-[#fafbfe] py-[9px] px-3 max-[768px]:grid-cols-1" aria-label="检索过滤条件"><label className="flex min-w-0 items-center gap-1.5 text-[10px] text-ink-faint max-[768px]:grid max-[768px]:grid-cols-[36px_minmax(0,1fr)]"><span className="shrink-0">分类</span><Select size="sm" aria-label="过滤分类" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}><option value="">全部分类</option>{categories.map((item) => <option key={item.category_id} value={item.category_id}>{item.name}{item.active ? "" : "（已停用）"}</option>)}</Select></label><label className="flex min-w-0 items-center gap-1.5 text-[10px] text-ink-faint max-[768px]:grid max-[768px]:grid-cols-[36px_minmax(0,1fr)]"><span className="shrink-0">标签</span><Input size="sm" aria-label="过滤标签" value={tagFilter} onChange={(event) => setTagFilter(event.target.value)} placeholder="逗号分隔"/></label><label className="flex min-w-0 items-center gap-1.5 text-[10px] text-ink-faint max-[768px]:grid max-[768px]:grid-cols-[36px_minmax(0,1fr)]"><span className="shrink-0">来源</span><Select size="sm" aria-label="过滤来源类型" value={sourceTypeFilter} onChange={(event) => setSourceTypeFilter(event.target.value)}><option value="">全部</option><option value="file">文件</option><option value="object_storage">对象存储</option><option value="web">网页</option><option value="connector">连接器</option></Select></label></div><label className="sr-only" htmlFor="question">向知识库提问</label><textarea id="question" className="w-full min-h-[70px] resize-none border-0 bg-transparent pt-3.5 pr-3.5 pb-[7px] pl-3.5 text-[#283047] leading-[1.6] outline-0" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="输入你的问题…" rows={2} maxLength={2000}/><div className="flex min-h-[48px] items-center justify-between border-t border-divider pt-1.5 pr-2 pb-[7px] pl-3 max-[561px]:items-end max-[561px]:gap-2"><div className="flex items-center gap-3 max-[768px]:gap-[7px]"><label className="flex items-center gap-[5px] m-0"><span className="block text-[11px] font-semibold text-[#555f75] max-[768px]:hidden">知识库：</span><Select size="sm" aria-label="当前知识库" value={baseId} onChange={(event) => selectBase(event.target.value)}>{bases.map((item) => <option key={item.knowledge_base_id} value={item.knowledge_base_id}>{item.name}</option>)}</Select></label><span className="flex items-center gap-[5px] whitespace-nowrap text-[10px] text-ink-faint max-[768px]:hidden"><Paperclip size={15}/>{documents.length ? `已连接 ${documents.length} 份资料` : "暂无资料"}</span></div>{/* 禁用原因由 Button 自动渲染成 ⓘ + Tooltip，不占行高。 */}<Button type="submit" size="icon" aria-label="提问并发送" loading={busy} blockedReason={question.trim() ? undefined : "请先输入问题"}>{busy ? <span className="block h-3.5 w-3.5 rounded-full border-2 border-white/45 border-t-white [animation:spin_0.7s_linear_infinite]"/> : <Send size={17}/>}</Button></div></form><small className="block mt-2 text-center text-[9px] text-ink-faint">内容由人工智能生成，请注意甄别信息准确性</small></div>
      </section>
      <EvidencePanel sources={sources} documents={documents} activeRecord={activeRecord} conversation={history} result={result}/>
    </div>
    {confirmDelete ? <Dialog open title="清空当前会话" description="删除后无法恢复，知识库资料不会受到影响。" onClose={() => { if (!busy) setConfirmDelete(false); }}>{error ? <ErrorBanner>{error}</ErrorBanner> : null}<DialogActions><Button variant="secondary" loading={busy} onClick={() => setConfirmDelete(false)}>取消</Button><Button variant="destructive" autoFocus loading={busy} onClick={() => void removeConversation()}>确认删除</Button></DialogActions></Dialog> : null}
  </div>;
}
