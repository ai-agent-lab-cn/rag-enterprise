import { FormEvent, useState } from "react";
import { api } from "../api";
import type { CategoryTemplate, CategoryTemplateItem } from "../types";
import { Modal } from "./Modal";

type Draft = { name: string; description: string; sort_order: number };
const EMPTY_DRAFT: Draft = { name: "", description: "", sort_order: 100 };

export function CategoryTemplateModal({ template, onClose, onChanged }: { template: CategoryTemplate; onClose: () => void; onChanged: (template: CategoryTemplate) => void }) {
  const [editing, setEditing] = useState<CategoryTemplateItem | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = async () => onChanged(await api.getDefaultCategoryTemplate());
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft.name.trim()) { setError("请输入分类名称。"); return; }
    setBusy(true); setError("");
    try {
      await api.createDefaultCategoryTemplateItem(draft);
      setDraft(EMPTY_DRAFT); await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "模板分类保存失败。"); }
    finally { setBusy(false); }
  };
  const saveEditing = async (event: FormEvent) => {
    event.preventDefault();
    if (!editing || !draft.name.trim()) { setError("请输入分类名称。"); return; }
    setBusy(true); setError("");
    try {
      await api.updateDefaultCategoryTemplateItem(editing.template_item_id, { ...draft, active: editing.active });
      await refresh(); setEditing(null); setDraft(EMPTY_DRAFT);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "模板分类保存失败。"); }
    finally { setBusy(false); }
  };
  const toggle = async (item: CategoryTemplateItem) => {
    setBusy(true); setError("");
    try { await api.updateDefaultCategoryTemplateItem(item.template_item_id, { name: item.name, description: item.description, sort_order: item.sort_order, active: !item.active }); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "模板分类更新失败。"); }
    finally { setBusy(false); }
  };
  const remove = async (item: CategoryTemplateItem) => {
    setBusy(true); setError("");
    try { await api.deleteDefaultCategoryTemplateItem(item.template_item_id); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "模板分类删除失败。"); }
    finally { setBusy(false); }
  };
  if (editing) return <Modal title="编辑模板分类" onClose={() => { if (!busy) { setEditing(null); setDraft(EMPTY_DRAFT); setError(""); } }}>
    <form className="modal-form" onSubmit={(event) => void saveEditing(event)}>
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      <label>分类名称<input aria-label="编辑分类名称" value={draft.name} maxLength={64} onChange={(event) => setDraft({ ...draft, name: event.target.value })}/></label>
      <label>说明 <span>选填</span><textarea aria-label="编辑分类说明" value={draft.description} maxLength={300} rows={3} onChange={(event) => setDraft({ ...draft, description: event.target.value })}/></label>
      <label>排序<input aria-label="编辑分类排序" type="number" min={0} max={10000} value={draft.sort_order} onChange={(event) => setDraft({ ...draft, sort_order: Number(event.target.value) })}/></label>
      <footer className="modal-actions"><button type="button" className="secondary-action" disabled={busy} onClick={() => { setEditing(null); setDraft(EMPTY_DRAFT); setError(""); }}>取消</button><button className="primary-action" disabled={busy}>{busy ? "保存中…" : "保存"}</button></footer>
    </form>
  </Modal>;
  return <Modal title="默认分类模板" onClose={() => { if (!busy) onClose(); }}>
    <div className="category-template-panel">
      <p className="template-copy-note">模板只在创建知识库时复制，修改模板不会影响已有知识库。</p>
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      <form className="category-template-form" onSubmit={(event) => void submit(event)}>
        <input aria-label="模板分类名称" placeholder="分类名称" value={draft.name} maxLength={64} onChange={(event) => setDraft({ ...draft, name: event.target.value })}/>
        <input aria-label="模板分类说明" placeholder="说明" value={draft.description} maxLength={300} onChange={(event) => setDraft({ ...draft, description: event.target.value })}/>
        <input aria-label="模板分类排序" type="number" min={0} max={10000} value={draft.sort_order} onChange={(event) => setDraft({ ...draft, sort_order: Number(event.target.value) })}/>
        <button className="primary-action" disabled={busy}>新建分类</button>
      </form>
      <div className="category-template-list">{template.items.map((item) => <div key={item.template_item_id}>
        <span><strong>{item.name}</strong><small>排序 {item.sort_order}{item.active ? "" : " · 已停用"}</small>{item.description ? <small>{item.description}</small> : null}</span>
        <div><button type="button" disabled={busy} onClick={() => { setError(""); setEditing(item); setDraft({ name: item.name, description: item.description, sort_order: item.sort_order }); }}>编辑</button><button type="button" disabled={busy} onClick={() => void toggle(item)}>{item.active ? "停用" : "启用"}</button><button type="button" className="table-danger-action" disabled={busy} onClick={() => void remove(item)}>删除</button></div>
      </div>)}</div>
    </div>
  </Modal>;
}
