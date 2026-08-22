import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import type { KnowledgeBase } from "../types";
import { Modal } from "./Modal";

export function KnowledgeBasesPage({ onOpen, showCreate, onCloseCreate }: { onOpen: (path: string) => void; showCreate: boolean; onCloseCreate: () => void }) {
  const [items, setItems] = useState<KnowledgeBase[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");

  const load = () => api.listKnowledgeBases().then(setItems, (reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取知识库。"));
  useEffect(() => { void load(); }, []);
  const create = async (event: FormEvent) => {
    event.preventDefault(); setCreating(true); setError("");
    try { const item = await api.createKnowledgeBase(name.trim(), description.trim()); setName(""); setDescription(""); onCloseCreate(); await load(); onOpen(`/knowledge-bases/${item.knowledge_base_id}`); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "创建失败。"); }
    finally { setCreating(false); }
  };
  return <section className="product-page" aria-label="知识库管理">
    {error ? <div className="error-banner" role="alert">{error}</div> : null}
    {items === null && !error ? <div className="evaluation-state pulse">正在读取知识库…</div> : null}
    {items?.length === 0 ? <div className="evaluation-state"><h2>还没有知识库</h2><p>创建一个知识库后即可上传资料并开始问答。</p></div> : null}
    <div className="base-grid">{items?.map((item) => <article className="base-card" key={item.knowledge_base_id}><div className="base-title-row"><h2>{item.name}</h2><span className={`base-type-tag ${item.is_default ? "is-default" : "is-independent"}`}>{item.is_default ? "默认知识库" : "独立知识库"}</span></div><p>{item.description || "暂无说明"}</p><dl><div><dt>资料</dt><dd>{item.document_count}</dd></div><div><dt>片段</dt><dd>{item.chunk_count}</dd></div></dl><button onClick={() => onOpen(`/knowledge-bases/${item.knowledge_base_id}`)}>进入知识库 →</button></article>)}</div>
    {showCreate ? <Modal title="新建知识库" description="知识库之间的资料、索引和会话相互隔离。" onClose={() => { if (!creating) onCloseCreate(); }}><form className="modal-form" onSubmit={create}><label htmlFor="base-name">知识库名称</label><input autoFocus id="base-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：产品研发资料" maxLength={80} required /><label htmlFor="base-description">用途说明 <span>选填</span></label><textarea id="base-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="简要说明该知识库包含的内容" maxLength={500} rows={4}/><footer className="modal-actions"><button className="secondary-action" type="button" onClick={onCloseCreate} disabled={creating}>取消</button><button className="primary-action" disabled={creating || !name.trim()}>{creating ? "创建中…" : "确认创建"}</button></footer></form></Modal> : null}
  </section>;
}
