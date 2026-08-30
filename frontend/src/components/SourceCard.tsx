import { useState } from "react";
import { api } from "../api";
import type { Citation, Source } from "../types";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";

interface SourceCardProps { source: Source; index: number; defaultOpen?: boolean }

export function SourceCard({ source, index, defaultOpen = false }: SourceCardProps) {
  const [citation, setCitation] = useState<Citation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const location = source.page ? `第 ${source.page} 页` : `第 ${source.paragraph + 1} 段`;
  const channels = source.retrieval_channels ?? [];
  const openOriginal = async () => {
    setLoading(true); setError("");
    try { setCitation(await api.getCitation(source.knowledge_base_id, source.chunk_id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "原文定位失败。"); }
    finally { setLoading(false); }
  };
  const originalLocation = citation ? [
    citation.page ? `第 ${citation.page} 页` : `第 ${citation.paragraph + 1} 段`,
    citation.heading_path?.length ? citation.heading_path.join(" / ") : "",
    citation.sheet_name ? `${citation.sheet_name}${citation.row_start ? ` · 第 ${citation.row_start}${citation.row_end && citation.row_end !== citation.row_start ? `–${citation.row_end}` : ""} 行` : ""}${citation.column_start ? ` · 第 ${citation.column_start}${citation.column_end && citation.column_end !== citation.column_start ? `–${citation.column_end}` : ""} 列` : ""}` : "",
  ].filter(Boolean).join(" · ") : "";

  return <>
    <details className="source-card" id={`source-${index + 1}`} open={defaultOpen}>
      <summary>
        <span className="source-number">{index + 1}</span>
        <span className="source-main"><strong>{source.filename}</strong><small>{location} · 片段 {source.chunk_index}</small><span className="source-summary">{source.summary}</span></span>
        <span className="score-pair">
          {channels.length === 0 ? <span title="向量粗召回相似度">召回 {source.retrieval_score.toFixed(3)}</span> : <>{channels.includes("vector") ? <span title="向量召回相似度">向量 {source.retrieval_score.toFixed(3)}</span> : null}{channels.includes("lexical") && source.lexical_score != null ? <span title="BM25 词法召回分数">词法 {source.lexical_score.toFixed(2)}</span> : null}</>}
          <span title="CrossEncoder 精排分数">精排 {source.rerank_score.toFixed(3)}</span>
        </span>
      </summary>
      <p>{source.text}</p>
      {/* 内边距接原来的 .source-original-action：卡片本体没有 padding，不给就贴边。 */}
      <Button className="mx-3.5 mb-3" variant="ghost" size="sm" loading={loading} onClick={() => void openOriginal()}>查看 {source.filename} 原文</Button>
      {error ? <small className="source-error" role="alert">{error}</small> : null}
    </details>
    {citation ? <Dialog open size="md" title="可信引用原文" description={`${citation.filename} · ${originalLocation}`} onClose={() => setCitation(null)}>
      <div className="citation-original"><p>{citation.text}</p><dl><div><dt>文档版本</dt><dd>{citation.document_version_id}</dd></div><div><dt>内容哈希</dt><dd title={citation.content_sha256}>{citation.content_sha256.slice(0, 16)}…</dd></div>{citation.external_resource_id ? <div><dt>外部资源</dt><dd>{citation.external_resource_id}</dd></div> : null}</dl>{citation.source_url ? <a href={citation.source_url} target="_blank" rel="noreferrer">打开外部原文</a> : null}</div>
    </Dialog> : null}
  </>;
}
