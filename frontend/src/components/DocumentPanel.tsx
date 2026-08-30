import { useRef, useState } from "react";
import type { DocumentCategory, DocumentInfo } from "../types";
import { Button } from "./ui/Button";
import { Dialog, DialogActions } from "./ui/Dialog";
import { Input } from "./ui/Input";
import { Select } from "./ui/Select";

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
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<DocumentInfo | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [editing, setEditing] = useState<DocumentInfo | null>(null);
  const [category, setCategory] = useState("");
  const [tagsText, setTagsText] = useState("");
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [categoryFilter, setCategoryFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [batchCategory, setBatchCategory] = useState("");
  const [retrying, setRetrying] = useState<string[]>([]);
  const matchesCategory = (item: DocumentInfo) =>
    !categoryFilter
    || (categoryFilter === UNCATEGORIZED ? item.category_id === null : item.category_id === categoryFilter);
  const visibleDocuments = documents.filter((item) => matchesCategory(item) && (!statusFilter || item.classification_status === statusFilter));
  const reclassify = async (documentIds: string[]) => {
    // 行级进度：重新分类只影响这一份资料，整页 Loading 会让其他行也变得不可用。
    setRetrying((current) => [...current, ...documentIds]);
    try {
      await onReclassify(documentIds);
    } finally {
      setRetrying((current) => current.filter((id) => !documentIds.includes(id)));
    }
  };

  const acceptFiles = async (files: FileList | File[]) => {
    const selected = Array.from(files);
    if (selected.length) await onUpload(selected);
  };

  return (
    <section className="document-panel" aria-label="知识库文档">
      <button
        className={`drop-zone ${dragging ? "is-dragging" : ""}`}
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void acceptFiles(event.dataTransfer.files);
        }}
        disabled={loading}
      >
        <span className="upload-icon" aria-hidden="true">↑</span>
        <strong>{uploadProgress ? `正在上传 ${uploadProgress.completed} / ${uploadProgress.total}` : loading ? "正在建立索引…" : "批量添加资料"}</strong>
        <small>拖入或多选 MD、TXT、PDF、HTML、DOCX、XLSX、CSV · 单文件最大 15 MB</small>
        {uploadProgress ? <progress aria-label="批量上传进度" max={uploadProgress.total} value={uploadProgress.completed}/> : null}
      </button>
      <input
        ref={inputRef}
        className="sr-only"
        type="file"
        multiple
        aria-label="批量上传资料"
        accept=".md,.txt,.pdf,.html,.htm,.docx,.xlsx,.csv"
        onChange={(event) => { if (event.target.files) void acceptFiles(event.target.files); event.currentTarget.value = ""; }}
      />

      <div className="document-governance-toolbar">
        <label>分类<Select size="sm" className="w-36" aria-label="分类筛选" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}><option value="">全部分类</option><option value={UNCATEGORIZED}>无分类</option>{categories.map((item) => <option key={item.category_id} value={item.category_id}>{item.name}{item.active ? "" : "（已停用）"}</option>)}</Select></label>
        <label>状态<Select size="sm" className="w-28" aria-label="分类状态筛选" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">全部</option>{(Object.keys(STATUS_LABEL) as Array<keyof typeof STATUS_LABEL>).map((key) => <option key={key} value={key}>{STATUS_LABEL[key]}</option>)}</Select></label>
        <label>批量归类<Select size="sm" className="w-32" aria-label="批量归类目标" value={batchCategory} onChange={(event) => setBatchCategory(event.target.value)}><option value="">选择分类</option>{categories.filter((item) => item.active).map((item) => <option key={item.category_id} value={item.category_id}>{item.name}</option>)}</Select></label>
        <Button variant="secondary" size="sm" loading={loading} reasonHidden blockedReason={!selected.length ? "请先勾选资料" : !batchCategory ? "请先选择目标分类" : undefined} onClick={async () => { await onBatchCategory(selected, batchCategory); setSelected([]); }}>应用到 {selected.length} 份</Button>
        <Button variant="secondary" size="sm" loading={Boolean(retrying.length)} reasonHidden blockedReason={selected.length ? undefined : "请先勾选资料"} onClick={async () => { await reclassify(selected); setSelected([]); }}>重新分类 {selected.length} 份</Button>
      </div>

      <div className="document-table-wrap">
        {documents.length === 0 ? (
          <p className="empty-copy">还没有资料。先上传一份你亲自编写的项目文档。</p>
        ) : (
          <table className="document-table">
            <thead><tr><th><input type="checkbox" aria-label="选择全部资料" checked={Boolean(visibleDocuments.length) && visibleDocuments.every((item) => selected.includes(item.document_id))} onChange={(event) => setSelected(event.target.checked ? visibleDocuments.map((item) => item.document_id) : [])}/> 文件名</th><th>分类</th><th>标签</th><th>格式</th><th>切片数</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>{visibleDocuments.map((document) => {
              const extension = document.filename.split(".").pop()?.toUpperCase() || "—";
              return <tr key={document.document_id}>
                <td><input type="checkbox" aria-label={`选择 ${document.filename}`} checked={selected.includes(document.document_id)} onChange={(event) => setSelected((items) => event.target.checked ? [...items, document.document_id] : items.filter((id) => id !== document.document_id))}/><strong title={document.filename}>{document.filename}</strong></td>
                {/* 分类列只放真实分类名；没有分类就是「—」。状态是另一件事，单独一行显示。 */}
                <td>{document.category ?? "—"}<small className="classification-state">{document.classification_status === "review_required" ? `${STATUS_LABEL.review_required} ${Math.round((document.classification_confidence || 0) * 100)}%` : STATUS_LABEL[document.classification_status]}</small>{document.classification_status === "failed" && document.classification_failure_reason ? <small className="classification-failure" title={document.classification_failure_code ?? undefined}>{document.classification_failure_reason}</small> : null}</td>
                <td><span className="document-tags" title={(document.tags || []).join("、")}>{(document.tags || []).length ? document.tags.join("、") : "—"}</span></td>
                <td><span className="file-type-tag">{extension}</span></td>
                <td>{document.chunk_count}</td>
                <td><span className="status-tag status-ready">已索引</span></td>
                <td><div className="table-actions">{document.classification_status === "failed" || document.classification_status === "pending" ? <Button variant="ghost" size="sm" aria-label={`重新分类 ${document.filename}`} loading={retrying.includes(document.document_id)} onClick={() => void reclassify([document.document_id])}>重新分类</Button> : null}<Button variant="ghost" size="sm" aria-label={`编辑 ${document.filename} 元数据`} onClick={() => { setEditing(document); setCategory(document.category ?? ""); setTagsText((document.tags || []).join("，")); }}>编辑</Button><Button variant="ghost" size="sm" className="text-danger-text hover:bg-danger-subtle" aria-label={`删除 ${document.filename}`} onClick={() => setPendingDelete(document)}>删除</Button></div></td>
              </tr>;
            })}</tbody>
          </table>
        )}
      </div>
      {pendingDelete ? <Dialog open title="删除资料" description="此操作会同时删除原始文件和对应向量索引。" onClose={() => { if (!deleting) setPendingDelete(null); }}><div className="confirm-copy">确认删除 <strong>{pendingDelete.filename}</strong> 吗？删除后无法在当前知识库中检索该资料。</div><DialogActions><Button variant="secondary" loading={deleting} onClick={() => setPendingDelete(null)}>取消</Button><Button variant="destructive" autoFocus loading={deleting} onClick={async () => { setDeleting(true); try { await onDelete(pendingDelete.document_id); setPendingDelete(null); } finally { setDeleting(false); } }}>确认删除</Button></DialogActions></Dialog> : null}
      {editing ? <Dialog open title="编辑资料元数据" description={editing.filename} onClose={() => { if (!saving) setEditing(null); }}><form className="metadata-form" onSubmit={async (event) => { event.preventDefault(); const tags = tagsText.split(/[，,]/).map((item) => item.trim()).filter(Boolean); const target = categories.find((item) => item.name === category); setSaving(true); try { if (target && target.category_id !== editing.category_id) await onBatchCategory([editing.document_id], target.category_id); await onUpdateMetadata(editing.document_id, target?.name ?? "", [...new Set(tags)]); setEditing(null); } finally { setSaving(false); } }}><label>分类<Select value={category} onChange={(event) => setCategory(event.target.value)}><option value="">无分类</option>{categories.filter((item) => item.active || item.category_id === editing.category_id).map((item) => <option key={item.category_id}>{item.name}</option>)}</Select></label><label>标签<Input maxLength={400} value={tagsText} onChange={(event) => setTagsText(event.target.value)} placeholder="多个标签用逗号分隔"/></label><DialogActions><Button variant="secondary" loading={saving} onClick={() => setEditing(null)}>取消</Button><Button type="submit" loading={saving}>保存</Button></DialogActions></form></Dialog> : null}
    </section>
  );
}
