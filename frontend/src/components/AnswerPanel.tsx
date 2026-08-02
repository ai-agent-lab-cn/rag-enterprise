import type { QueryResult } from "../types";
import { SourceCard } from "./SourceCard";

interface AnswerPanelProps {
  result: QueryResult | null;
  loading: boolean;
}

export function AnswerPanel({ result, loading }: AnswerPanelProps) {
  if (loading) {
    return <div className="answer-placeholder pulse">正在检索、精排并组织答案…</div>;
  }
  if (!result) {
    return (
      <div className="answer-placeholder">
        <span className="constellation" aria-hidden="true">✦</span>
        <h2>答案从证据开始</h2>
        <p>上传个人项目资料，然后提出一个能在资料中找到依据的问题。</p>
      </div>
    );
  }

  return (
    <section className="answer-result" aria-live="polite">
      <div className="answer-label">生成答案</div>
      <p className="answer-text">{result.answer}</p>
      <div className="metric-strip" aria-label="查询性能">
        {Object.entries(result.latency_ms).map(([name, value]) => (
          <div key={name}>
            <span>{name}</span>
            <strong>{value.toFixed(0)} ms</strong>
          </div>
        ))}
        <div><span>model</span><strong>{result.model}</strong></div>
      </div>
      <div className="sources-heading">
        <h3>引用证据</h3>
        <span>{result.sources.length} 条</span>
      </div>
      <div className="source-list">
        {result.sources.map((source, index) => (
          <SourceCard key={source.chunk_id} source={source} index={index} />
        ))}
      </div>
    </section>
  );
}
