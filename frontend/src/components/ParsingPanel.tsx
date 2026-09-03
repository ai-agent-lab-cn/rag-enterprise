import { useEffect, useMemo, useState } from "react";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { cn } from "./ui/cn";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input } from "./ui/Input";
import { ListItemButton } from "./ui/ListItemButton";
import { Select } from "./ui/Select";
import { api } from "../api";
import type { DocumentVersion, ParsingChunk, ParsingPreview } from "../types";

const PARSE_STATUS = { pending: "等待解析", parsing: "解析中", chunking: "切片中", ready: "可预览", failed: "解析失败" } as const;

/**
 * 结构树 / Chunk 列表两栏共用的行样式，对应 legacy 里
 * `.structure-tree button,.chunk-preview button` 那条合并选择器。搭配
 * `components/ui/ListItemButton` 使用：那边保证 `border-0`/`bg-transparent`/
 * `type="button"`，这里只负责布局——全宽、多行、可选中的列表项不是 CTA，
 * `ui/Button` 的固定高度（sm/md/lg）装不下两行文字，disabled 也只能通过
 * blockedReason 表达，表达不了「选中态」这种语义。
 */
const treeItemClass = (active: boolean) =>
  cn(
    "grid w-full gap-[3px] rounded-sm px-2.5 py-2 hover:bg-brand-subtle",
    active && "bg-brand-subtle",
  );

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

  // 未核（真实分支未触发，靠注入探测元素实测）：以下三处空态段落在当前 demo 数据里
  // 都走不到（结构树每个节点都有 chunk、preview 已就绪不再是空版本列表），margin 是
  // 用 Playwright 往当前登录后的真实页面里注入一个同 class 的临时 <p>、读它的
  // getComputedStyle 实测出的 13px/13px（UI Foundation 阶段 5 收口 .empty-copy 前，
  // styles.css 里它有两条同名选择器，后一条把 font-size 覆盖成了 13px，不是看代码以为
  // 的 12px；也不是按 1em 规则口算的——收口后 text-md/leading-[1.6] 已把这个实测值
  // 固化进 utility class）。mt-[13px] mb-[13px] 这两个显式类就是照实测值补的。

  if (!ordered.length) return <p className="text-md text-[#737c90] leading-[1.6] mt-[13px] mb-[13px]">还没有可解析的资料版本。</p>;

  return (
    <section className="grid gap-3">
      <div className="flex flex-wrap items-end gap-2.5 border-b border-line py-2.5">
        <label className="grid gap-1 text-base text-ink-faint max-md:w-full">
          文档版本
          <Select size="sm" className="w-72 max-md:w-full" aria-label="解析文档版本" value={effectiveVersionId} onChange={(event) => setVersionId(event.target.value)}>
            {ordered.map((item) => (
              <option key={item.document_version_id} value={item.document_version_id}>
                {item.filename} · V{item.version_number}{item.is_current ? "（当前）" : "（历史）"}
              </option>
            ))}
          </Select>
        </label>
        {/* Badge 只有 5 档语义色，没有专门的「进行中/蓝色」；pending/parsing/chunking
            借用 brand（品牌色）表达「正在推进」，与 ready 的 success（绿）、failed 的
            danger（红）区分开——同 KnowledgeBasesPage 的 STATUS_TONE。preview 还没读到时
            也落在这一档，与迁移前的三分支 ternary 行为一致。 */}
        <Badge shape="status" tone={preview?.parse_status === "failed" ? "danger" : preview?.parse_status === "ready" ? "success" : "brand"} className="max-md:min-h-8">
          {preview ? PARSE_STATUS[preview.parse_status] : "等待读取"}
        </Badge>
        {preview && !preview.is_current ? <em className="text-base font-normal text-warning not-italic">历史版本 · 不进入当前检索</em> : null}
      </div>

      {error ? <ErrorBanner>{error}</ErrorBanner> : null}

      {loading ? <p className="m-0 bg-canvas p-3 text-ink-muted" aria-live="polite">正在读取解析结果…</p> : null}

      {preview?.parse_status === "failed" ? (
        <div className="flex gap-2.5 bg-danger-subtle p-3 text-danger-text">
          <strong>{preview.parse_failure_code || "PARSER_FAILED"}</strong>
          <span>{preview.failure_reason || "解析失败，请重新处理。"}</span>
        </div>
      ) : null}

      {preview && canManage ? (
        <div className="flex flex-wrap items-end gap-2.5 border-b border-line py-2.5">
          <strong className="self-center">切片策略</strong>
          <label className="grid gap-1 text-base text-ink-faint max-md:w-full">
            目标长度
            <Input size="sm" type="number" className="w-24 max-md:w-full" min={100} max={4000} value={chunkSize} onChange={(event) => setChunkSize(Number(event.target.value))} />
          </label>
          <label className="grid gap-1 text-base text-ink-faint max-md:w-full">
            重叠长度
            <Input size="sm" type="number" className="w-24 max-md:w-full" min={0} max={1000} value={chunkOverlap} onChange={(event) => setChunkOverlap(Number(event.target.value))} />
          </label>
          <Button size="sm" loading={reprocessing} blockedReason={chunkOverlap >= chunkSize ? "重叠必须小于切片大小" : undefined} onClick={() => void reprocess()}>
            {reprocessing ? "已排队…" : "重新处理"}
          </Button>
          <small className="basis-full text-ink-faint">修改参数后需重新处理，成功前仍使用当前可用索引。</small>
        </div>
      ) : null}

      {preview?.parse_status === "ready" ? (
        <div className="grid grid-cols-1 divide-y divide-line border border-line md:min-h-[480px] md:grid-cols-[minmax(190px,0.7fr)_minmax(320px,1.4fr)_minmax(240px,1fr)] md:divide-x md:divide-y-0">
          <div className="min-w-0 max-h-[360px] overflow-auto p-3 md:max-h-[560px]">
            <h3 className="mt-0 mb-2.5 text-[14px]">文档结构</h3>
            {preview.tree.map((node) => {
              const active = selectedNode === node.node_id;
              return (
                <ListItemButton
                  key={node.node_id}
                  active={active}
                  className={treeItemClass(active)}
                  style={{ paddingLeft: `${10 + node.level * 10}px` }}
                  onClick={() => { setSelectedNode(node.node_id); const first = preview.chunks.find((item) => item.metadata.node_id === node.node_id); setSelectedChunk(first?.chunk_id || ""); }}
                >
                  <span className="truncate text-sm text-ink-faint">{node.node_type}</span>
                  <strong className="truncate">{node.text}</strong>
                </ListItemButton>
              );
            })}
          </div>
          <div className="min-w-0 max-h-[360px] overflow-auto p-3 md:max-h-[560px]">
            <h3 className="mt-0 mb-2.5 text-[14px]">原文预览</h3>
            {activeChunk ? (
              <>
                <small className="text-sm text-ink-faint">{locationLabel(activeChunk)}</small>
                <p className="mt-3 mb-3.5 whitespace-pre-wrap leading-[1.8]">{activeChunk.content}</p>
              </>
            ) : (
              <p className="text-md text-[#737c90] leading-[1.6] mt-[13px] mb-[13px]">该结构节点没有独立 Chunk。</p>
            )}
          </div>
          <div className="min-w-0 max-h-[360px] overflow-auto p-3 md:max-h-[560px]">
            <h3 className="mt-0 mb-2.5 text-[14px]">
              Chunk <span className="font-normal text-ink-faint">{visibleChunks.length}</span>
            </h3>
            {visibleChunks.map((chunk) => {
              const active = activeChunk?.chunk_id === chunk.chunk_id;
              return (
                <ListItemButton key={chunk.chunk_id} active={active} className={treeItemClass(active)} onClick={() => setSelectedChunk(chunk.chunk_id)}>
                  <strong className="text-brand">#{chunk.chunk_index + 1}</strong>
                  <small className="text-sm text-ink-faint">{locationLabel(chunk)}</small>
                  <span className="truncate">{chunk.content}</span>
                </ListItemButton>
              );
            })}
          </div>
        </div>
      ) : !loading && preview ? (
        <p className="text-md text-[#737c90] leading-[1.6] mt-[13px] mb-[13px]">解析完成后可查看结构树、原文和 Chunk。</p>
      ) : null}
    </section>
  );
}
