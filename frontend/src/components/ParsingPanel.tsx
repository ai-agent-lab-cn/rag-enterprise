import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { DocumentVersion, ParsingChunk, ParsingPreview } from "../types";

const PARSE_STATUS = { pending: "等待解析", parsing: "解析中", chunking: "切片中", ready: "可预览", failed: "解析失败" } as const;

function locationLabel(chunk: ParsingChunk) {
  const metadata = chunk.metadata;
  if (metadata.page) return `第 ${metadata.page} 页`;
  if (metadata.sheet_name) return `${metadata.sheet_name} · 第 ${metadata.row_start || "—"}–${metadata.row_end || "—"} 行`;
  const headings = metadata.heading_path;
  return Array.isArray(headings) && headings.length ? headings.join(" / ") : `段落 ${Number(metadata.paragraph || 0) + 1}`;
}

export function ParsingPanel({ knowledgeBaseId, versions, canManage, onRefresh }: { knowledgeBaseId: string; versions: DocumentVersion[]; canManage: boolean; onRefresh: () => Promise<void> }) {
  const [versionId, setVersionId] = useState("");
  const [preview, setPreview] = useState<ParsingPreview | null>(null);
  const [selectedNode, setSelectedNode] = useState("");
  const [selectedChunk, setSelectedChunk] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [chunkSize, setChunkSize] = useState(700);
  const [chunkOverlap, setChunkOverlap] = useState(100);
  const [reprocessing, setReprocessing] = useState(false);
  const ordered = useMemo(() => [...versions].sort((a, b) => Number(b.is_current) - Number(a.is_current) || b.version_number - a.version_number), [versions]);
  const effectiveVersionId = versionId || ordered[0]?.document_version_id || "";
  useEffect(() => {
    if (!effectiveVersionId) return;
    let active = true;
    void Promise.resolve().then(async () => { setLoading(true); setError(""); try { const value = await api.getDocumentParsingPreview(knowledgeBaseId, effectiveVersionId); if (active) { setPreview(value); setSelectedNode(value.tree[0]?.node_id || ""); setSelectedChunk(value.chunks[0]?.chunk_id || ""); const size = Number(value.processing_options.chunk_size); const overlap = Number(value.processing_options.chunk_overlap); if (size) setChunkSize(size); if (Number.isFinite(overlap)) setChunkOverlap(overlap); } } catch (reason) { if (active) setError(reason instanceof Error ? reason.message : "解析结果读取失败。"); } finally { if (active) setLoading(false); } });
    return () => { active = false; };
  }, [effectiveVersionId, knowledgeBaseId]);
  const visibleChunks = preview?.chunks.filter((item) => !selectedNode || item.metadata.node_id === selectedNode) || [];
  const activeChunk = preview?.chunks.find((item) => item.chunk_id === selectedChunk) || visibleChunks[0];
  const reprocess = async () => { if (!effectiveVersionId) return; setReprocessing(true); setError(""); try { await api.reprocessDocumentVersion(knowledgeBaseId, effectiveVersionId, chunkSize, chunkOverlap); await onRefresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : "重新处理失败。"); } finally { setReprocessing(false); } };
  if (!ordered.length) return <p className="empty-copy">还没有可解析的资料版本。</p>;
  return <section className="parsing-panel">
    <div className="parsing-toolbar"><label>文档版本<select aria-label="解析文档版本" value={effectiveVersionId} onChange={(event) => setVersionId(event.target.value)}>{ordered.map((item) => <option key={item.document_version_id} value={item.document_version_id}>{item.filename} · V{item.version_number}{item.is_current ? "（当前）" : "（历史）"}</option>)}</select></label><span className={`status-tag status-${preview?.parse_status === "failed" ? "failed" : preview?.parse_status === "ready" ? "ready" : "processing"}`}>{preview ? PARSE_STATUS[preview.parse_status] : "等待读取"}</span>{preview && !preview.is_current ? <em>历史版本 · 不进入当前检索</em> : null}</div>
    {error ? <div className="error-banner" role="alert">{error}</div> : null}
    {loading ? <p className="local-loading" aria-live="polite">正在读取解析结果…</p> : null}
    {preview?.parse_status === "failed" ? <div className="parsing-failure"><strong>{preview.parse_failure_code || "PARSER_FAILED"}</strong><span>{preview.failure_reason || "解析失败，请重新处理。"}</span></div> : null}
    {preview && canManage ? <div className="chunking-policy"><strong>切片策略</strong><label>目标长度<input type="number" min={100} max={4000} value={chunkSize} onChange={(event) => setChunkSize(Number(event.target.value))}/></label><label>重叠长度<input type="number" min={0} max={1000} value={chunkOverlap} onChange={(event) => setChunkOverlap(Number(event.target.value))}/></label><button type="button" disabled={reprocessing || chunkOverlap >= chunkSize} onClick={() => void reprocess()}>{reprocessing ? "已排队…" : "重新处理"}</button><small>修改参数后需重新处理，成功前仍使用当前可用索引。</small></div> : null}
    {preview?.parse_status === "ready" ? <div className="parsing-workbench">
      <div className="structure-tree"><h3>文档结构</h3>{preview.tree.map((node) => <button type="button" className={selectedNode === node.node_id ? "is-active" : ""} style={{ paddingLeft: `${10 + node.level * 10}px` }} key={node.node_id} onClick={() => { setSelectedNode(node.node_id); const first = preview.chunks.find((item) => item.metadata.node_id === node.node_id); setSelectedChunk(first?.chunk_id || ""); }}><span>{node.node_type}</span><strong>{node.text}</strong></button>)}</div>
      <div className="source-preview"><h3>原文预览</h3>{activeChunk ? <><small>{locationLabel(activeChunk)}</small><p>{activeChunk.content}</p></> : <p className="empty-copy">该结构节点没有独立 Chunk。</p>}</div>
      <div className="chunk-preview"><h3>Chunk <span>{visibleChunks.length}</span></h3>{visibleChunks.map((chunk) => <button type="button" className={activeChunk?.chunk_id === chunk.chunk_id ? "is-active" : ""} key={chunk.chunk_id} onClick={() => setSelectedChunk(chunk.chunk_id)}><strong>#{chunk.chunk_index + 1}</strong><small>{locationLabel(chunk)}</small><span>{chunk.content}</span></button>)}</div>
    </div> : !loading && preview ? <p className="empty-copy">解析完成后可查看结构树、原文和 Chunk。</p> : null}
  </section>;
}
