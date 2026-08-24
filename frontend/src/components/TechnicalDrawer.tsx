import type { QueryResult } from "../types";

interface TechnicalDrawerProps {
  result: QueryResult;
}

const LATENCY_LABELS: Record<string, string> = {
  retrieval: "候选召回",
  rerank: "交叉编码精排",
  generation: "答案生成",
  total: "总耗时",
};

export function TechnicalDrawer({ result }: TechnicalDrawerProps) {
  const firstSource = result.sources[0];
  const retrievalMethods = new Set(result.sources.flatMap((source) => source.retrieval_methods ?? ["vector"]));
  const retrievalLabel = retrievalMethods.has("lexical") ? "向量 + 关键词混合召回" : "向量召回";
  return (
    <details className="technical-drawer">
      <summary>查看技术细节 <span aria-hidden="true">＋</span></summary>
      <div className="technical-grid">
        <section>
          <span className="section-kicker">检索过程</span>
          <h3>{retrievalLabel} → 精排 → 生成</h3>
          <p>返回 {result.sources.length} 条来源，并按融合排序结果展示。</p>
          {firstSource ? (
            <p>最高来源分数：融合 {firstSource.retrieval_score.toFixed(3)} / 精排 {firstSource.rerank_score.toFixed(3)}</p>
          ) : null}
        </section>
        <section>
          <span className="section-kicker">模型与参数</span>
          <h3 title={result.model}>{result.model}</h3>
          <p>默认召回 10 条候选，精排后返回 5 条来源。</p>
        </section>
        <section>
          <span className="section-kicker">性能耗时</span>
          <dl>
            {Object.entries(result.latency_ms).map(([key, value]) => (
              <div key={key}><dt>{LATENCY_LABELS[key] ?? key}</dt><dd>{value.toFixed(0)} ms</dd></div>
            ))}
          </dl>
        </section>
      </div>
    </details>
  );
}
