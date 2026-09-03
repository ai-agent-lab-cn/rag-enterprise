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
    {/* 皮肤等同旧 `.evidence-list .source-card`：这是这个组件唯一的真实使用路径
        （AnswerPanel 的 `.source-list` 分支恒为 showSources=false，从未被渲染）。 */}
    <details className="overflow-hidden rounded-md border border-line bg-surface transition-[border-color,box-shadow] duration-[160ms] hover:border-[#ccc5f1] hover:shadow-[0_8px_20px_rgba(57,46,134,0.07)]" id={`source-${index + 1}`} open={defaultOpen}>
      <summary className="flex list-none items-start gap-2.5 p-3 cursor-pointer">
        <span className="grid h-6 w-6 flex-none place-items-center rounded-sm border border-[#d9d5fa] text-[9px] text-[#574ad0]">{index + 1}</span>
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <strong className="overflow-hidden text-ellipsis whitespace-nowrap text-[13px]">{source.filename}</strong>
          <small className="text-[11px] text-ink-faint">{location} · 片段 {source.chunk_index}</small>
          <span className="mt-[7px] line-clamp-2 text-[10px] font-normal leading-[1.55] text-[#6f778a]">{source.summary}</span>
        </span>
        <span className="flex flex-none flex-col gap-1 max-[561px]:hidden">
          {channels.length === 0 ? <span className="whitespace-nowrap rounded bg-[#f3f4f8] px-[5px] py-[3px] text-[8px] text-[#737b8f]" title="向量粗召回相似度">召回 {source.retrieval_score.toFixed(3)}</span> : <>{channels.includes("vector") ? <span className="whitespace-nowrap rounded bg-[#f3f4f8] px-[5px] py-[3px] text-[8px] text-[#737b8f]" title="向量召回相似度">向量 {source.retrieval_score.toFixed(3)}</span> : null}{channels.includes("lexical") && source.lexical_score != null ? <span className="whitespace-nowrap rounded bg-[#f3f4f8] px-[5px] py-[3px] text-[8px] text-[#737b8f]" title="BM25 词法召回分数">词法 {source.lexical_score.toFixed(2)}</span> : null}</>}
          <span className="whitespace-nowrap rounded bg-[#f3f4f8] px-[5px] py-[3px] text-[8px] text-[#737b8f]" title="CrossEncoder 精排分数">精排 {source.rerank_score.toFixed(3)}</span>
        </span>
      </summary>
      <p className="m-0 max-h-[170px] overflow-y-auto border-t border-divider px-3 pt-[11px] pb-[13px] text-[11px] leading-[1.65] text-[#6c7487]">{source.text}</p>
      {/* 内边距接原来的 .source-original-action：卡片本体没有 padding，不给就贴边。 */}
      <Button className="mx-3.5 mb-3" variant="ghost" size="sm" loading={loading} onClick={() => void openOriginal()}>查看 {source.filename} 原文</Button>
      {/* text-danger-text 而不是"如实复刻" var(--danger) 死变量导致的墨色：这是
          role="alert" 的错误提示，颜色是它唯一的表意信号，属于全局约束点名的
          "颜色只留给表意"一类，不适用装饰色的"如实迁移"豁免。仓库已有判例——
          DataTable.tsx 的组件注释把同类死变量（--border 等）列为一次性收掉的缺陷，
          不是继续复刻的对象。 */}
      {error ? <small className="block px-3.5 pt-0 pb-3 text-danger-text" role="alert">{error}</small> : null}
    </details>
    {citation ? <Dialog open size="md" title="可信引用原文" description={`${citation.filename} · ${originalLocation}`} onClose={() => setCitation(null)}>
      <div className="grid gap-3.5">
        {/* mt-3.5/mb-3.5 是如实迁移：原 CSS 没给这个 <p> 设 margin，实测它在真实浏览器里
            吃的是 <p> 的 UA 默认纵向 margin（1em，此处字号 14px 结算为 14px）。grid 容器
            不会折叠子项 margin，这 14px 会叠加在 gap 之上，必须显式声明才不会在 preflight
            开启后塌陷。dl 同理。 */}
        <p className="mt-3.5 mb-3.5 rounded-md p-3.5 leading-[1.75] whitespace-pre-wrap">{citation.text}</p>
        <dl className="mt-3.5 mb-3.5 grid gap-2">
          <div className="grid grid-cols-[90px_1fr] gap-3"><dt className="text-[#70798f]">文档版本</dt><dd className="m-0 [overflow-wrap:anywhere]">{citation.document_version_id}</dd></div>
          <div className="grid grid-cols-[90px_1fr] gap-3"><dt className="text-[#70798f]">内容哈希</dt><dd className="m-0 [overflow-wrap:anywhere]" title={citation.content_sha256}>{citation.content_sha256.slice(0, 16)}…</dd></div>
          {citation.external_resource_id ? <div className="grid grid-cols-[90px_1fr] gap-3"><dt className="text-[#70798f]">外部资源</dt><dd className="m-0 [overflow-wrap:anywhere]">{citation.external_resource_id}</dd></div> : null}
        </dl>
        {citation.source_url ? <a href={citation.source_url} target="_blank" rel="noreferrer">打开外部原文</a> : null}
      </div>
    </Dialog> : null}
  </>;
}
