import { FormEvent, useState } from "react";
import { api } from "../api";
import type { CategoryTemplate, CategoryTemplateItem } from "../types";
import { Button } from "./ui/Button";
import { Column, DataTable } from "./ui/DataTable";
import { Dialog, DialogActions } from "./ui/Dialog";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input, Textarea } from "./ui/Input";
import { RowAction, RowActions } from "./ui/RowActions";
import { useToast } from "./ui/Toast";

type Draft = { name: string; description: string; sort_order: number };
const EMPTY_DRAFT: Draft = { name: "", description: "", sort_order: 100 };

/** 新建与编辑共用一个表单：同一个对象的两种操作长得一样，字段也一致。 */
type FormState = { mode: "create" } | { mode: "edit"; item: CategoryTemplateItem };

export function CategoryTemplateModal({ template, onClose, onChanged }: { template: CategoryTemplate; onClose: () => void; onChanged: (template: CategoryTemplate) => void }) {
  const [form, setForm] = useState<FormState | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const toast = useToast();
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
      toast.success(form.mode === "create" ? `已创建分类「${name}」` : `已保存分类「${name}」`);
      closeForm();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "模板分类保存失败。";
      setError(message);
      toast.error(message);
    } finally { setBusy(false); }
  };

  const toggle = async (item: CategoryTemplateItem) => {
    setBusy(true); setError("");
    try {
      await api.updateDefaultCategoryTemplateItem(item.template_item_id, { name: item.name, description: item.description, sort_order: item.sort_order, active: !item.active });
      await refresh();
      toast.success(item.active ? `已停用分类「${item.name}」` : `已启用分类「${item.name}」`);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "模板分类更新失败。";
      setError(message);
      toast.error(message);
    } finally { setBusy(false); }
  };
  const remove = async (item: CategoryTemplateItem) => {
    setBusy(true); setError("");
    try {
      await api.deleteDefaultCategoryTemplateItem(item.template_item_id);
      await refresh();
      toast.success(`已删除分类「${item.name}」`);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "模板分类删除失败。";
      setError(message);
      toast.error(message);
    } finally { setBusy(false); }
  };

  const rowActions = (item: CategoryTemplateItem): RowAction[] => [
    { label: "编辑", blockedReason: busy ? "处理中" : undefined, onSelect: () => openEdit(item) },
    { label: item.active ? "停用" : "启用", blockedReason: busy ? "处理中" : undefined, onSelect: () => void toggle(item) },
    { label: "删除", tone: "destructive", blockedReason: busy ? "处理中" : undefined, onSelect: () => void remove(item) },
  ];

  const columns: Column<CategoryTemplateItem>[] = [
    {
      key: "name", header: "分类名称", truncate: false,
      render: (item) => <span><strong className="text-ink">{item.name}</strong>{item.active ? null : <small className="ml-1.5 text-xs text-ink-faint">已停用</small>}</span>,
    },
    { key: "sort_order", header: "排序", width: "70px", numeric: true, render: (item) => item.sort_order },
    { key: "description", header: "说明", render: (item) => <span title={item.description || "—"}>{item.description || "—"}</span> },
    {
      key: "actions", header: "操作", width: "190px", align: "right", truncate: false,
      render: (item) => <RowActions rowLabel={item.name} actions={rowActions(item)} />,
    },
  ];

  if (form) return <Dialog open title={form.mode === "create" ? "新建模板分类" : "编辑模板分类"} description="模板只在创建知识库时复制，改动不影响已有知识库。" onClose={() => { if (!busy) closeForm(); }}>
    <form className="grid gap-[9px] pt-[20px] px-[22px]" onSubmit={(event) => void submit(event)}>
      {error ? <ErrorBanner>{error}</ErrorBanner> : null}
      <label className="text-[#4e576c] text-[13px] font-semibold">分类名称<Input className="py-[10px]" autoFocus value={draft.name} maxLength={64} onChange={(event) => { setDraft({ ...draft, name: event.target.value }); setError(""); }}/></label>
      <label className="text-[#4e576c] text-[13px] font-semibold">说明<Textarea className="resize-y" value={draft.description} maxLength={300} rows={3} onChange={(event) => setDraft({ ...draft, description: event.target.value })}/></label>
      <label className="text-[#4e576c] text-[13px] font-semibold">排序<Input className="py-[10px]" type="number" min={0} max={10000} value={draft.sort_order} onChange={(event) => setDraft({ ...draft, sort_order: Number(event.target.value) })}/></label>
      <DialogActions>
        <Button variant="secondary" loading={busy} onClick={closeForm}>取消</Button>
        <Button type="submit" loading={busy}>{form.mode === "create" ? "创建" : "保存"}</Button>
      </DialogActions>
    </form>
  </Dialog>;

  return <Dialog open size="lg" title="默认分类模板" description="此处管理新知识库的初始分类模板，不会修改已有知识库分类。" onClose={() => { if (!busy) onClose(); }}>
    <div className="grid gap-3">
      {error ? <ErrorBanner>{error}</ErrorBanner> : null}
      {/* sm 而不是 md：这一屏是「表格 + 上方新建」，跟行内操作同属一套密度。
          md 留给切到表单视图后的输入框那一屏。 */}
      <div><Button size="sm" loading={busy} onClick={openCreate}>＋ 新建分类</Button></div>
      <DataTable
        rows={template.items}
        columns={columns}
        rowKey={(item) => item.template_item_id}
        label="模板分类列表"
        emptyState={{ kind: "empty", title: "模板里还没有分类", description: "新建的知识库将不会预置任何分类。" }}
      />
    </div>
  </Dialog>;
}
