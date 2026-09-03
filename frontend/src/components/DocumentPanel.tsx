import { useState } from "react";
import type { DocumentCategory, DocumentInfo } from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Column, DataTable } from "./ui/DataTable";
import { Dialog, DialogActions } from "./ui/Dialog";
import { FileButton } from "./ui/FileButton";
import { Input } from "./ui/Input";
import { RowAction, RowActions } from "./ui/RowActions";
import { Select } from "./ui/Select";
import { Toolbar } from "./ui/Toolbar";
import { useConfirm } from "./ui/useConfirm";
import { useToast } from "./ui/Toast";

/** 分类筛选里代表「没有分类」的哨兵值。它不是分类 ID，也不对应任何分类记录。 */
const UNCATEGORIZED = "__uncategorized__";

/**
 * 分类处理状态的展示文案。
 *
 * 这些是**状态**，不是分类名。它们绝不能出现在分类列或分类下拉里——把「待分类」
 * 显示成分类，正是这次要消灭的那种混淆。
 */
const STATUS_LABEL: Record<DocumentInfo["classification_status"], string> = {
  pending: "待分类",
  auto_assigned: "自动分类",
  review_required: "待确认",
  manual: "人工归类",
  failed: "分类失败",
};

interface DocumentPanelProps {
  documents: DocumentInfo[];
  categories: DocumentCategory[];
  loading: boolean;
  uploadProgress?: { completed: number; total: number } | null;
  onUpload: (files: File[]) => Promise<void>;
  onDelete: (documentId: string) => Promise<void>;
  onUpdateMetadata: (documentId: string, category: string, tags: string[]) => Promise<void>;
  onBatchCategory: (documentIds: string[], categoryId: string) => Promise<void>;
  onReclassify: (documentIds: string[]) => Promise<void>;
}

export function DocumentPanel({ documents, categories, loading, uploadProgress, onUpload, onDelete, onUpdateMetadata, onBatchCategory, onReclassify }: DocumentPanelProps) {
  const [dragging, setDragging] = useState(false);
  const [editing, setEditing] = useState<DocumentInfo | null>(null);
  const [category, setCategory] = useState("");
  const [tagsText, setTagsText] = useState("");
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [categoryFilter, setCategoryFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [batchCategory, setBatchCategory] = useState("");
  // 行级进度：重新分类只影响这一份资料，整页 Loading 会让其他行也变得不可用。
  const [retrying, setRetrying] = useState<string[]>([]);
  const { confirm, dialog: confirmDialog } = useConfirm();
  const toast = useToast();

  const matchesCategory = (item: DocumentInfo) =>
    !categoryFilter
    || (categoryFilter === UNCATEGORIZED ? item.category_id === null : item.category_id === categoryFilter);
  const visibleDocuments = documents.filter((item) => matchesCategory(item) && (!statusFilter || item.classification_status === statusFilter));
  const filtered = Boolean(categoryFilter || statusFilter);

  // 供行级与批量重新分类共用。返回是否成功，调用方据此决定要不要清空勾选。
  const reclassify = async (documentIds: string[]) => {
    setRetrying((current) => [...current, ...documentIds]);
    try {
      await onReclassify(documentIds);
      toast.success(documentIds.length > 1 ? `已重新提交 ${documentIds.length} 份资料的分类` : "已重新提交分类");
      return true;
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "重新分类失败。");
      return false;
    } finally {
      setRetrying((current) => current.filter((id) => !documentIds.includes(id)));
    }
  };

  const acceptFiles = async (files: File[]) => {
    if (!files.length) return;
    try {
      await onUpload(files);
      toast.success(files.length > 1 ? `已上传 ${files.length} 份资料` : `已上传「${files[0].name}」`);
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "上传失败。");
    }
  };

  const applyBatchCategory = async () => {
    const targetIds = selected;
    try {
      await onBatchCategory(targetIds, batchCategory);
      toast.success(`已将 ${targetIds.length} 份资料归入所选分类`);
      setSelected([]);
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "批量归类失败。");
    }
  };

  const startDelete = (document: DocumentInfo) => {
    confirm({
      title: "删除资料",
      consequence: `确认删除「${document.filename}」吗？此操作会同时删除原始文件和对应向量索引，删除后无法在当前知识库中检索该资料。`,
      confirmLabel: "确认删除",
      tone: "destructive",
      onConfirm: async () => {
        try {
          await onDelete(document.document_id);
          toast.success(`已删除「${document.filename}」`);
        } catch (reason) {
          toast.error(reason instanceof Error ? reason.message : "删除失败。");
          throw reason;
        }
      },
    });
  };

  const saveMetadata = async () => {
    if (!editing) return;
    const tags = [...new Set(tagsText.split(/[，,]/).map((item) => item.trim()).filter(Boolean))];
    const target = categories.find((item) => item.category_id === category);
    setSaving(true);
    try {
      if (target && target.category_id !== editing.category_id) await onBatchCategory([editing.document_id], target.category_id);
      await onUpdateMetadata(editing.document_id, target?.name ?? "", tags);
      toast.success(`已保存「${editing.filename}」的元数据`);
      setEditing(null);
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "元数据更新失败。");
    } finally {
      setSaving(false);
    }
  };

  const rowActions = (document: DocumentInfo): RowAction[] => {
    const actions: RowAction[] = [];
    if (document.classification_status === "pending" || document.classification_status === "failed") {
      actions.push({
        label: "重新分类",
        blockedReason: retrying.includes(document.document_id) ? "正在重新分类" : undefined,
        onSelect: () => void reclassify([document.document_id]),
      });
    }
    actions.push({
      label: "编辑",
      onSelect: () => {
        setEditing(document);
        setCategory(document.category_id ?? "");
        setTagsText((document.tags || []).join("，"));
      },
    });
    actions.push({ label: "删除", tone: "destructive", onSelect: () => startDelete(document) });
    return actions;
  };

  const columns: Column<DocumentInfo>[] = [
    {
      key: "filename", header: "文件名", width: "26%", truncate: false,
      render: (document) => <strong className="block truncate font-medium text-ink" title={document.filename}>{document.filename}</strong>,
    },
    {
      key: "category", header: "分类", width: "170px", truncate: false,
      render: (document) => (
        // 分类列只放真实分类名；没有分类就是「—」。状态是另一件事，单独一行显示——
        // 把「待分类」显示成分类，正是这次要消灭的那种混淆。
        <div className="grid gap-0.5 py-1">
          <span className="truncate">{document.category ?? "—"}</span>
          <small className="truncate text-sm text-ink-faint">
            {document.classification_status === "review_required"
              ? `${STATUS_LABEL.review_required} ${Math.round((document.classification_confidence || 0) * 100)}%`
              : STATUS_LABEL[document.classification_status]}
          </small>
          {document.classification_status === "failed" && document.classification_failure_reason ? (
            <small className="truncate text-sm text-danger-text" title={document.classification_failure_code ?? undefined}>
              {document.classification_failure_reason}
            </small>
          ) : null}
        </div>
      ),
    },
    {
      key: "tags", header: "标签", width: "160px",
      render: (document) => <span title={(document.tags || []).join("、") || undefined}>{(document.tags || []).length ? document.tags.join("、") : "—"}</span>,
    },
    {
      key: "format", header: "格式", width: "80px",
      render: (document) => <Badge shape="type">{document.filename.split(".").pop()?.toUpperCase() || "—"}</Badge>,
    },
    { key: "chunk_count", header: "切片数", width: "80px", numeric: true, render: (document) => document.chunk_count },
    {
      key: "status", header: "状态", width: "90px",
      render: () => <Badge shape="status" tone="success">已索引</Badge>,
    },
    {
      key: "actions", header: "操作", width: "210px", align: "right", truncate: false,
      render: (document) => <RowActions rowLabel={document.filename} actions={rowActions(document)} />,
    },
  ];

  return (
    <section aria-label="知识库文档" className="grid gap-3">
      <FileButton
        variant="outline"
        className={`h-auto min-h-28 w-full flex-col items-center justify-center gap-1.5 whitespace-normal border-dashed p-4 text-center normal-case ${dragging ? "border-brand bg-brand-subtle" : "border-line-firm bg-canvas"}`}
        accept=".md,.txt,.pdf,.html,.htm,.docx,.xlsx,.csv"
        multiple
        inputLabel="批量上传资料"
        loading={loading}
        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => { event.preventDefault(); setDragging(false); void acceptFiles(Array.from(event.dataTransfer.files)); }}
        onSelect={(files) => void acceptFiles(files)}
      >
        <span aria-hidden className="grid h-8 w-8 place-items-center rounded-md bg-brand-subtle text-lg text-brand">↑</span>
        <strong className="text-md font-semibold text-ink">
          {uploadProgress ? `正在上传 ${uploadProgress.completed} / ${uploadProgress.total}` : loading ? "正在建立索引…" : "批量添加资料"}
        </strong>
        <small className="text-sm text-ink-faint">拖入或多选 MD、TXT、PDF、HTML、DOCX、XLSX、CSV · 单文件最大 15 MB</small>
        {uploadProgress ? <progress aria-label="批量上传进度" className="w-60 max-w-full" max={uploadProgress.total} value={uploadProgress.completed} /> : null}
      </FileButton>

      <Toolbar
        filters={<>
          <label className="flex items-center gap-2 text-md">分类
            <Select size="sm" className="w-36" aria-label="分类筛选" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}>
              <option value="">全部分类</option>
              <option value={UNCATEGORIZED}>无分类</option>
              {categories.map((item) => <option key={item.category_id} value={item.category_id}>{item.name}{item.active ? "" : "（已停用）"}</option>)}
            </Select>
          </label>
          <label className="flex items-center gap-2 text-md">状态
            <Select size="sm" className="w-28" aria-label="分类状态筛选" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="">全部</option>
              {(Object.keys(STATUS_LABEL) as Array<keyof typeof STATUS_LABEL>).map((key) => <option key={key} value={key}>{STATUS_LABEL[key]}</option>)}
            </Select>
          </label>
        </>}
        batch={{
          count: selected.length,
          children: <>
            <Select size="sm" className="w-32" aria-label="批量归类目标" value={batchCategory} onChange={(event) => setBatchCategory(event.target.value)}>
              <option value="">选择分类</option>
              {categories.filter((item) => item.active).map((item) => <option key={item.category_id} value={item.category_id}>{item.name}</option>)}
            </Select>
            {/* 两个禁用原因必须都能说出来：勾了资料但没选目标分类时，「请先选择目标分类」
                不能被「请先勾选资料」挡住不显示。 */}
            <Button
              variant="secondary"
              size="sm"
              loading={loading}
              blockedReason={[selected.length ? "" : "请先勾选资料", batchCategory ? "" : "请先选择目标分类"].filter(Boolean)}
              onClick={() => void applyBatchCategory()}
            >
              应用到 {selected.length} 份
            </Button>
            <Button
              variant="secondary"
              size="sm"
              loading={Boolean(retrying.length)}
              blockedReason={selected.length ? undefined : "请先勾选资料"}
              onClick={() => { const ids = selected; void reclassify(ids).then((ok) => { if (ok) setSelected([]); }); }}
            >
              重新分类 {selected.length} 份
            </Button>
          </>,
        }}
      />

      <DataTable
        rows={visibleDocuments}
        columns={columns}
        rowKey={(document) => document.document_id}
        label="资料列表"
        selection={{ selected, onChange: setSelected, rowLabel: (document) => document.filename }}
        emptyState={filtered
          ? { kind: "filtered", title: "没有符合条件的资料", description: "调整分类或状态筛选后重试。" }
          : { kind: "empty", title: "还没有资料", description: "先上传一份你亲自编写的项目文档。" }}
      />

      {editing ? (
        <Dialog open title="编辑资料元数据" description={editing.filename} onClose={() => { if (!saving) setEditing(null); }}>
          <form className="grid gap-3.5" onSubmit={(event) => { event.preventDefault(); void saveMetadata(); }}>
            <label className="grid gap-[7px] text-[12px] text-ink-muted">分类
              <Select className="min-h-[40px]" value={category} onChange={(event) => setCategory(event.target.value)}>
                <option value="">无分类</option>
                {categories.filter((item) => item.active || item.category_id === editing.category_id).map((item) => <option key={item.category_id} value={item.category_id}>{item.name}</option>)}
              </Select>
            </label>
            <label className="grid gap-[7px] text-[12px] text-ink-muted">标签
              <Input className="min-h-[40px]" maxLength={400} value={tagsText} onChange={(event) => setTagsText(event.target.value)} placeholder="多个标签用逗号分隔" />
            </label>
            <DialogActions>
              <Button variant="secondary" loading={saving} onClick={() => setEditing(null)}>取消</Button>
              <Button type="submit" loading={saving}>保存</Button>
            </DialogActions>
          </form>
        </Dialog>
      ) : null}
      {confirmDialog}
    </section>
  );
}
