import { useRef, useState } from "react";
import type { DocumentInfo } from "../types";

interface DocumentPanelProps {
  documents: DocumentInfo[];
  loading: boolean;
  onUpload: (file: File) => Promise<void>;
  onDelete: (documentId: string) => Promise<void>;
}

export function DocumentPanel({ documents, loading, onUpload, onDelete }: DocumentPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const acceptFile = async (file?: File) => {
    if (file) await onUpload(file);
  };

  return (
    <aside className="document-panel" aria-label="知识库文档">
      <div className="panel-heading">
        <div>
          <h2>资料库</h2>
        </div>
        <span className="count-pill">{documents.length}</span>
      </div>

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
          void acceptFile(event.dataTransfer.files[0]);
        }}
        disabled={loading}
      >
        <span className="upload-icon" aria-hidden="true">↑</span>
        <strong>{loading ? "正在建立索引…" : "添加你的资料"}</strong>
        <small>拖入或选择 MD、TXT、PDF · 最大 15 MB</small>
      </button>
      <input
        ref={inputRef}
        className="sr-only"
        type="file"
        accept=".md,.txt,.pdf"
        onChange={(event) => void acceptFile(event.target.files?.[0])}
      />

      <div className="document-list">
        {documents.length === 0 ? (
          <p className="empty-copy">还没有资料。先上传一份你亲自编写的项目文档。</p>
        ) : (
          documents.map((document) => (
            <article className="document-item" key={document.document_id}>
              <div className="file-mark" aria-hidden="true">{document.filename.split(".").pop()?.toUpperCase()}</div>
              <div className="document-copy">
                <strong title={document.filename}>{document.filename}</strong>
                <span>{document.chunk_count} 个片段 · 已索引</span>
              </div>
              <button
                className="icon-button"
                type="button"
                aria-label={`删除 ${document.filename}`}
                onClick={() => {
                  if (window.confirm(`确认删除“${document.filename}”及其索引吗？`)) {
                    void onDelete(document.document_id);
                  }
                }}
              >
                ×
              </button>
            </article>
          ))
        )}
      </div>
    </aside>
  );
}
