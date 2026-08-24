import type { Source } from "../types";

interface SourceCardProps {
  source: Source;
  index: number;
  defaultOpen?: boolean;
}

export function SourceCard({ source, index, defaultOpen = false }: SourceCardProps) {
  const location = source.page ? `第 ${source.page} 页` : `第 ${source.paragraph + 1} 段`;
  const methods = source.retrieval_methods ?? ["vector"];
  const retrievalLabel = methods.length > 1 ? "混合召回" : methods[0] === "lexical" ? "关键词召回" : "向量召回";
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
          <span title={`${retrievalLabel}分数`}>{retrievalLabel} {source.retrieval_score.toFixed(3)}</span>
          <span title="CrossEncoder 精排分数">精排 {source.rerank_score.toFixed(3)}</span>
        </span>
      </summary>
      <p>{source.text}</p>
    </details>
  );
}
