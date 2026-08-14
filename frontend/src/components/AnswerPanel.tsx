import type { QueryResult } from "../types";
import { SourceCard } from "./SourceCard";
import { TechnicalDrawer } from "./TechnicalDrawer";

interface AnswerPanelProps {
  result: QueryResult | null;
  loading: boolean;
  showSources?: boolean;
}

const METRICS = [
  ["retrieval", "召回"],
  ["rerank", "精排"],
  ["generation", "生成"],
  ["total", "总耗时"],
] as const;

const SOURCE_REFERENCE = /(\[来源\s*(\d+)\])/g;

const ANSWER_STATUS_LABELS: Record<QueryResult["answer_status"], string> = {
  answered: "已基于证据回答",
  insufficient_evidence: "证据不足",
  source_conflict: "来源存在冲突",
  retrieval_only: "仅展示检索结果",
  generation_failed: "答案生成已降级",
};

function answerWithSourceLinks(answer: string, sourceCount: number) {
  return answer.split(SOURCE_REFERENCE).map((part, index) => {
    const sourceNumber = Number(part);
    if (index % 3 === 2 && sourceNumber >= 1 && sourceNumber <= sourceCount) {
      return <a className="source-reference" href={`#source-${sourceNumber}`} key={`${part}-${index}`}>[来源 {sourceNumber}]</a>;
    }
    if (index % 3 === 1 || index % 3 === 2) return null;
    return part;
  });
}

export function AnswerPanel({ result, loading, showSources = true }: AnswerPanelProps) {
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
      <div className={`answer-label answer-status-${result.answer_status}`}>
        {ANSWER_STATUS_LABELS[result.answer_status]}
      </div>
      <p className="answer-text">{answerWithSourceLinks(result.answer, result.sources.length)}</p>
      <div className="metric-strip" aria-label="查询性能">
        {METRICS.map(([key, label]) => {
          const value = result.latency_ms[key];
          return value === undefined ? null : (
            <div key={key}>
              <span>{label}</span>
              <strong>{value.toFixed(0)} ms</strong>
            </div>
          );
        })}
        <div><span>模型</span><strong title={result.model}>{result.model}</strong></div>
      </div>
      {showSources ? <><div className="sources-heading">
        <h3>引用证据</h3>
        <span>{result.sources.length} 条</span>
      </div>
      <div className="source-list">
        {result.sources.map((source, index) => (
          <SourceCard key={source.chunk_id} source={source} index={index} />
        ))}
      </div>
      </> : null}
      <TechnicalDrawer result={result} />
    </section>
  );
}
