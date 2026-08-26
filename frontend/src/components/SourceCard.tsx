import type { Source } from "../types";

interface SourceCardProps {
  source: Source;
  index: number;
  defaultOpen?: boolean;
}

export function SourceCard({ source, index, defaultOpen = false }: SourceCardProps) {
  const location = source.page ? `第 ${source.page} 页` : `第 ${source.paragraph + 1} 段`;
  // 通路缺失代表历史记录未保存该信息，此时沿用旧的"召回"标签，不臆测为向量召回。
  const channels = source.retrieval_channels ?? [];
  return (
    <details className="source-card" id={`source-${index + 1}`} open={defaultOpen}>
      <summary>
        <span className="source-number">{index + 1}</span>
        <span className="source-main">
          <strong>{source.filename}</strong>
          <small>{location} · 片段 {source.chunk_index}</small>
          <span className="source-summary">{source.summary}</span>
        </span>
        <span className="score-pair">
          {channels.length === 0 ? (
            <span title="向量粗召回相似度">召回 {source.retrieval_score.toFixed(3)}</span>
          ) : (
            <>
              {channels.includes("vector") ? (
                <span title="向量召回相似度">向量 {source.retrieval_score.toFixed(3)}</span>
              ) : null}
              {channels.includes("lexical") && source.lexical_score != null ? (
                <span title="BM25 词法召回分数">词法 {source.lexical_score.toFixed(2)}</span>
              ) : null}
            </>
          )}
          <span title="CrossEncoder 精排分数">精排 {source.rerank_score.toFixed(3)}</span>
        </span>
      </summary>
      <p>{source.text}</p>
    </details>
  );
}
