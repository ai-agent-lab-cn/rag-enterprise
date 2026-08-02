import type { Source } from "../types";

interface SourceCardProps {
  source: Source;
  index: number;
}

export function SourceCard({ source, index }: SourceCardProps) {
  const location = source.page ? `第 ${source.page} 页` : `第 ${source.paragraph + 1} 段`;
  return (
    <details className="source-card">
      <summary>
        <span className="source-number">{index + 1}</span>
        <span className="source-main">
          <strong>{source.filename}</strong>
          <small>{location} · chunk {source.chunk_index}</small>
        </span>
        <span className="score-pair">
          <span title="向量粗召回相似度">召回 {source.retrieval_score.toFixed(3)}</span>
          <span title="CrossEncoder 精排分数">精排 {source.rerank_score.toFixed(3)}</span>
        </span>
      </summary>
      <p>{source.text}</p>
    </details>
  );
}
