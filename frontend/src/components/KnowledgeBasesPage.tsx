import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import type { KnowledgeBase } from "../types";

export function KnowledgeBasesPage({ onOpen }: { onOpen: (path: string) => void }) {
  const [items, setItems] = useState<KnowledgeBase[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");

  const load = () => api.listKnowledgeBases().then(setItems, (reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取知识库。"));
  useEffect(() => { void load(); }, []);
  const create = async (event: FormEvent) => {
    event.preventDefault(); setCreating(true); setError("");
    try { const item = await api.createKnowledgeBase(name.trim(), description.trim()); setName(""); setDescription(""); await load(); onOpen(`/knowledge-bases/${item.knowledge_base_id}`); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "创建失败。"); }
    finally { setCreating(false); }
  };
  return <section className="product-page" aria-labelledby="bases-title">
    <header className="page-heading"><div><span className="eyebrow">多知识库管理</span><h1 id="bases-title">知识库</h1><p>为不同项目建立隔离的资料、索引与会话空间。</p></div></header>
    {error ? <div className="error-banner" role="alert">{error}</div> : null}
    <form className="create-card" onSubmit={create}><div><label htmlFor="base-name">新建知识库</label><input id="base-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：产品研发资料" maxLength={80} required /><textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="用途说明（可选）" maxLength={500} rows={2} /></div><button disabled={creating || !name.trim()}>{creating ? "创建中…" : "创建知识库"}</button></form>
    {items === null && !error ? <div className="evaluation-state pulse">正在读取知识库…</div> : null}
    {items?.length === 0 ? <div className="evaluation-state"><h2>还没有知识库</h2><p>创建一个知识库后即可上传资料并开始问答。</p></div> : null}
    <div className="base-grid">{items?.map((item) => <article className="base-card" key={item.knowledge_base_id}><div className="base-icon">{item.name.slice(0, 1)}</div><span>{item.is_default ? "默认知识库" : "独立知识库"}</span><h2>{item.name}</h2><p>{item.description || "暂无说明"}</p><dl><div><dt>资料</dt><dd>{item.document_count}</dd></div><div><dt>片段</dt><dd>{item.chunk_count}</dd></div></dl><button onClick={() => onOpen(`/knowledge-bases/${item.knowledge_base_id}`)}>进入知识库 →</button></article>)}</div>
  </section>;
}
