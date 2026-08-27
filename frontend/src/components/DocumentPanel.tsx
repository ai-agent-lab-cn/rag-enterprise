import { useRef, useState } from "react";
import type { DocumentCategory, DocumentInfo } from "../types";
import { Modal } from "./Modal";

interface DocumentPanelProps {
  documents: DocumentInfo[];
  categories: DocumentCategory[];
  loading: boolean;
  uploadProgress?: { completed: number; total: number } | null;
  onUpload: (files: File[]) => Promise<void>;
  onDelete: (documentId: string) => Promise<void>;
  onUpdateMetadata: (documentId: string, category: string, tags: string[]) => Promise<void>;
  onBatchCategory: (documentIds: string[], categoryId: string) => Promise<void>;
}

export function DocumentPanel({ documents, categories, loading, uploadProgress, onUpload, onDelete, onUpdateMetadata, onBatchCategory }: DocumentPanelProps) {
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
  const visibleDocuments = documents.filter((item) => (!categoryFilter || item.category_id === categoryFilter) && (!statusFilter || item.classification_status === statusFilter));

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
        <small>拖入或多选 MD、TXT、PDF · 单文件最大 15 MB</small>
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
        <label>分类<select aria-label="资料分类筛选" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}><option value="">全部</option>{categories.map((item) => <option key={item.category_id} value={item.category_id}>{item.name}{item.active ? "" : "（已停用）"}</option>)}</select></label>
        <label>状态<select aria-label="分类状态筛选" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">全部</option><option value="manual">已确认</option><option value="auto_assigned">自动分类</option><option value="review_required">待确认</option><option value="failed">失败</option><option value="pending">未分类</option></select></label>
        <label>批量归类<select aria-label="批量归类目标" value={batchCategory} onChange={(event) => setBatchCategory(event.target.value)}><option value="">选择分类</option>{categories.filter((item) => item.active).map((item) => <option key={item.category_id} value={item.category_id}>{item.name}</option>)}</select></label>
        <button type="button" disabled={!selected.length || !batchCategory || loading} onClick={async () => { await onBatchCategory(selected, batchCategory); setSelected([]); }}>应用到 {selected.length} 份</button>
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
                <td>{document.category || "未分类"}<small className="classification-state">{document.classification_status === "review_required" ? `待确认 ${Math.round((document.classification_confidence || 0) * 100)}%` : document.classification_status === "auto_assigned" ? "自动" : document.classification_status === "failed" ? "分类失败" : document.classification_status === "manual" ? "已确认" : "未分类"}</small></td>
                <td><span className="document-tags" title={(document.tags || []).join("、")}>{(document.tags || []).length ? document.tags.join("、") : "—"}</span></td>
                <td><span className="file-type-tag">{extension}</span></td>
                <td>{document.chunk_count}</td>
                <td><span className="status-tag status-ready">已索引</span></td>
                <td><div className="table-actions"><button type="button" aria-label={`编辑 ${document.filename} 元数据`} onClick={() => { setEditing(document); setCategory(document.category || "未分类"); setTagsText((document.tags || []).join("，")); }}>编辑</button><button className="table-danger-action" type="button" aria-label={`删除 ${document.filename}`} onClick={() => setPendingDelete(document)}>删除</button></div></td>
              </tr>;
            })}</tbody>
          </table>
        )}
      </div>
      {pendingDelete ? <Modal title="删除资料" description="此操作会同时删除原始文件和对应向量索引。" onClose={() => { if (!deleting) setPendingDelete(null); }}><div className="confirm-copy">确认删除 <strong>{pendingDelete.filename}</strong> 吗？删除后无法在当前知识库中检索该资料。</div><footer className="modal-actions"><button className="secondary-action" type="button" onClick={() => setPendingDelete(null)} disabled={deleting}>取消</button><button className="danger-action" autoFocus type="button" disabled={deleting} onClick={async () => { setDeleting(true); try { await onDelete(pendingDelete.document_id); setPendingDelete(null); } finally { setDeleting(false); } }}>{deleting ? "删除中…" : "确认删除"}</button></footer></Modal> : null}
      {editing ? <Modal title="编辑资料元数据" description={editing.filename} onClose={() => { if (!saving) setEditing(null); }}><form className="metadata-form" onSubmit={async (event) => { event.preventDefault(); const tags = tagsText.split(/[，,]/).map((item) => item.trim()).filter(Boolean); const target = categories.find((item) => item.name === category); setSaving(true); try { if (target && target.category_id !== editing.category_id) await onBatchCategory([editing.document_id], target.category_id); await onUpdateMetadata(editing.document_id, target?.name || editing.category, [...new Set(tags)]); setEditing(null); } finally { setSaving(false); } }}><label>分类<select required value={category} onChange={(event) => setCategory(event.target.value)}>{categories.filter((item) => item.active || item.category_id === editing.category_id).map((item) => <option key={item.category_id}>{item.name}</option>)}</select></label><label>标签<input maxLength={400} value={tagsText} onChange={(event) => setTagsText(event.target.value)} placeholder="多个标签用逗号分隔"/></label><footer className="modal-actions"><button className="secondary-action" type="button" onClick={() => setEditing(null)} disabled={saving}>取消</button><button className="primary-action" type="submit" disabled={saving}>{saving ? "保存中…" : "保存"}</button></footer></form></Modal> : null}
    </section>
  );
}
