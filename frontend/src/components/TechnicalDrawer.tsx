import type { QueryResult, Source } from "../types";

interface TechnicalDrawerProps {
  result: QueryResult;
}

const LATENCY_LABELS: Record<string, string> = {
  retrieval: "召回",
  rerank: "交叉编码精排",
  generation: "答案生成",
  total: "总耗时",
};

/** 统计候选分别由哪几路召回命中；通路缺失的历史记录不参与统计。 */
function channelBreakdown(sources: Source[]) {
  let both = 0;
  let vectorOnly = 0;
  let lexicalOnly = 0;
  let labelled = 0;
  for (const source of sources) {
    const channels = source.retrieval_channels ?? [];
    if (channels.length === 0) continue;
    labelled += 1;
    const hasVector = channels.includes("vector");
    const hasLexical = channels.includes("lexical");
    if (hasVector && hasLexical) both += 1;
    else if (hasVector) vectorOnly += 1;
    else if (hasLexical) lexicalOnly += 1;
  }
  return { both, vectorOnly, lexicalOnly, labelled };
}

export function TechnicalDrawer({ result }: TechnicalDrawerProps) {
  const firstSource = result.sources[0];
  const breakdown = channelBreakdown(result.sources);
  const hybrid = breakdown.labelled > 0 && (breakdown.both > 0 || breakdown.lexicalOnly > 0);
  return (
    <details className="technical-drawer">
      <summary>查看技术细节 <span aria-hidden="true">＋</span></summary>
      <div className="technical-grid">
        <section>
          <span className="section-kicker">检索过程</span>
          <h3>{hybrid ? "向量 + 词法 → 精排 → 生成" : "召回 → 精排 → 生成"}</h3>
          <p>返回 {result.sources.length} 条来源，并按融合排序结果展示。</p>
          {hybrid ? (
            <p>
              命中通路：双路 {breakdown.both} 条 / 仅向量 {breakdown.vectorOnly} 条 / 仅词法{" "}
              {breakdown.lexicalOnly} 条
            </p>
          ) : null}
          {firstSource ? (
            <p>最高来源分数：召回 {firstSource.retrieval_score.toFixed(3)} / 精排 {firstSource.rerank_score.toFixed(3)}</p>
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
