import { FormEvent, useState } from "react";
import { api } from "../api";
import type { CategoryTemplate, CategoryTemplateItem } from "../types";
import { Button } from "./ui/Button";
import { Dialog, DialogActions } from "./ui/Dialog";
import { Input, Textarea } from "./ui/Input";

type Draft = { name: string; description: string; sort_order: number };
const EMPTY_DRAFT: Draft = { name: "", description: "", sort_order: 100 };

/** 新建与编辑共用一个表单：同一个对象的两种操作长得一样，字段也一致。 */
type FormState = { mode: "create" } | { mode: "edit"; item: CategoryTemplateItem };

export function CategoryTemplateModal({ template, onClose, onChanged }: { template: CategoryTemplate; onClose: () => void; onChanged: (template: CategoryTemplate) => void }) {
  const [form, setForm] = useState<FormState | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = async () => onChanged(await api.getDefaultCategoryTemplate());

  const closeForm = () => { setForm(null); setDraft(EMPTY_DRAFT); setError(""); };
  const openCreate = () => {
    setError("");
    setForm({ mode: "create" });
    // 排序默认排到末尾。写死 100 会让每个新分类都和已有的挤在同一档。
    setDraft({ ...EMPTY_DRAFT, sort_order: template.items.reduce((max, item) => Math.max(max, item.sort_order), 0) + 100 });
  };
  const openEdit = (item: CategoryTemplateItem) => {
    setError("");
    setForm({ mode: "edit", item });
    setDraft({ name: item.name, description: item.description, sort_order: item.sort_order });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!form) return;
    // 必填校验用「点击后报错」而不是禁用提交按钮，见 CLAUDE.md 第一条。
    const name = draft.name.trim();
    if (!name) { setError("请输入分类名称。"); return; }
    const payload = { name, description: draft.description.trim(), sort_order: draft.sort_order };
    setBusy(true); setError("");
    try {
      if (form.mode === "create") await api.createDefaultCategoryTemplateItem(payload);
      else await api.updateDefaultCategoryTemplateItem(form.item.template_item_id, { ...payload, active: form.item.active });
      await refresh();
      closeForm();
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

  if (form) return <Dialog open title={form.mode === "create" ? "新建模板分类" : "编辑模板分类"} description="模板只在创建知识库时复制，改动不影响已有知识库。" onClose={() => { if (!busy) closeForm(); }}>
    <form className="modal-form" onSubmit={(event) => void submit(event)}>
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      <label>分类名称<Input autoFocus value={draft.name} maxLength={64} onChange={(event) => { setDraft({ ...draft, name: event.target.value }); setError(""); }}/></label>
      <label>说明<Textarea value={draft.description} maxLength={300} rows={3} onChange={(event) => setDraft({ ...draft, description: event.target.value })}/></label>
      <label>排序<Input type="number" min={0} max={10000} value={draft.sort_order} onChange={(event) => setDraft({ ...draft, sort_order: Number(event.target.value) })}/></label>
      <DialogActions>
        <Button variant="secondary" loading={busy} onClick={closeForm}>取消</Button>
        <Button type="submit" loading={busy}>{form.mode === "create" ? "创建" : "保存"}</Button>
      </DialogActions>
    </form>
  </Dialog>;

  return <Dialog open size="lg" title="默认分类模板" description="模板只在创建知识库时复制，修改模板不会影响已有知识库。" onClose={() => { if (!busy) onClose(); }}>
    <div className="category-template-panel">
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {/* sm 而不是 md：这一屏是「表格 + 上方新建」，跟行内操作同属一套密度。
          md 留给切到表单视图后的输入框那一屏。 */}
      <div className="category-template-actions"><Button size="sm" loading={busy} onClick={openCreate}>＋ 新建分类</Button></div>
      {/* 表头 + 数据行：原来把名称、排序、说明堆成三行，同一个属性在每行的位置都不同，
          扫读要一行行看过去。表格让同类信息对齐在同一列。 */}
      {/* 不复用 .management-table：它带着 min-width:1050px（为知识库列表那 8 列定的），
          模板只有 4 列，套上去会撑破弹层。这里用 Tailwind 直接写，列宽按内容分配。 */}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-base">
          <thead><tr className="border-b border-line text-left text-sm text-ink-faint">
            <th className="py-2 pr-3 font-semibold">分类名称</th>
            <th className="w-16 py-2 pr-3 font-semibold">排序</th>
            <th className="py-2 pr-3 font-semibold">说明</th>
            <th className="w-44 py-2 text-right font-semibold">操作</th>
          </tr></thead>
          <tbody>{template.items.map((item) => <tr key={item.template_item_id} className="border-b border-divider">
            <td className="py-2 pr-3"><strong className="text-ink">{item.name}</strong>{item.active ? null : <small className="ml-1.5 text-xs text-ink-faint">已停用</small>}</td>
            <td className="py-2 pr-3 text-ink-muted">{item.sort_order}</td>
            <td className="max-w-0 py-2 pr-3"><span className="truncate-cell text-ink-muted" title={item.description || "—"}>{item.description || "—"}</span></td>
            <td className="py-2"><div className="flex justify-end gap-1">
              <Button variant="ghost" size="sm" loading={busy} onClick={() => openEdit(item)}>编辑</Button>
              <Button variant="ghost" size="sm" loading={busy} onClick={() => void toggle(item)}>{item.active ? "停用" : "启用"}</Button>
              <Button variant="ghost" size="sm" className="text-danger-text hover:bg-danger-subtle" loading={busy} onClick={() => void remove(item)}>删除</Button>
            </div></td>
          </tr>)}</tbody>
        </table>
      </div>
      {template.items.length ? null : <p className="empty-copy">模板里还没有分类。新建的知识库将不会预置任何分类。</p>}
    </div>
  </Dialog>;
}
