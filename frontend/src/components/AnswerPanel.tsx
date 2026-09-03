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

const ANSWER_STATUS_DESCRIPTIONS: Record<QueryResult["answer_status"], string> = {
  answered: "证据与引用校验通过。",
  insufficient_evidence: "未达到证据阈值，不生成确定性结论。",
  source_conflict: "来源之间存在冲突，请核对引用原文。",
  retrieval_only: "生成模型不可用，当前仅保留可核对的检索证据。",
  generation_failed: "答案生成失败，当前仍保留可用引用证据。",
};

/**
 * 原 `.answer-label`/`.answer-status-*`：默认绿，证据不足/冲突橙，检索降级/生成失败红。
 * 迁移时改用语义 token（`text-success`/`text-warning`/`text-danger-text`）而不是照抄
 * 原始 hex——这是表意色，颜色本身就是"通过/需注意/失败"三种状态的唯一信号，
 * 理应走全局约束点名的语义色 token，不是装饰色，没有"如实复刻"的豁免理由。
 * 精确色值因此从 #2d9257/#ce7b12/#d74747 分别变成 token 定义的 #197d4a/#ef8500/
 * #c2354a，基线会有可见但符合预期的色差。
 */
const ANSWER_STATUS_COLOR: Record<QueryResult["answer_status"], string> = {
  answered: "text-success",
  insufficient_evidence: "text-warning",
  source_conflict: "text-warning",
  retrieval_only: "text-danger-text",
  generation_failed: "text-danger-text",
};

function answerWithSourceLinks(answer: string, sourceCount: number) {
  return answer.split(SOURCE_REFERENCE).map((part, index) => {
    const sourceNumber = Number(part);
    if (index % 3 === 2 && sourceNumber >= 1 && sourceNumber <= sourceCount) {
      return <a className="rounded bg-[#efedff] px-1 py-px text-[.88em] font-semibold text-[#5548ce] no-underline" href={`#source-${sourceNumber}`} key={`${part}-${index}`}>[来源 {sourceNumber}]</a>;
    }
    if (index % 3 === 1 || index % 3 === 2) return null;
    return part;
  });
}

/** 原 `.answer-placeholder`：无边框、无背景，纯文字/图标居中版。 */
const PLACEHOLDER_CLASS = "mx-auto mt-[8vh] mb-0 grid max-w-[700px] min-h-[280px] content-center justify-items-center p-[26px] text-center text-[14px] text-ink-faint";

export function AnswerPanel({ result, loading, showSources = true }: AnswerPanelProps) {
  if (loading) {
    // `.pulse` 是共享 legacy 动画（OverviewPage 也在用），不在本任务的独占 class
    // 清单里，保留字面 class 名而不是换成 Tailwind 的 animate-pulse——后者时长
    // （2s）和缓动都与原动画（1.5s ease-in-out）不同，会是一次不必要的视觉变化。
    return <div className={`${PLACEHOLDER_CLASS} pulse`}>正在检索、精排并组织答案…</div>;
  }
  if (!result) {
    return (
      <div className={PLACEHOLDER_CLASS}>
        <span className="grid h-[50px] w-[50px] place-items-center rounded-2xl bg-brand-subtle text-[25px] text-[#6659d8]" aria-hidden="true">✦</span>
        <h2 className="mt-[9px] mb-0.5 text-[20px] text-[#283149]">答案从证据开始</h2>
        <p className="mt-3.5 mb-3.5 max-w-[400px] text-[14px] leading-[1.7] text-[#747d91]">上传个人项目资料，然后提出一个能在资料中找到依据的问题。</p>
      </div>
    );
  }

  return (
    <section className="mx-auto mt-0 mb-5 max-w-[760px] min-h-0 rounded-[10px] border border-line bg-surface px-[22px] py-5 shadow-[0_6px_20px_rgba(31,38,63,0.04)]" aria-live="polite">
      <div className={`text-[10px] font-semibold ${ANSWER_STATUS_COLOR[result.answer_status]}`}>
        {ANSWER_STATUS_LABELS[result.answer_status]}
      </div>
      {/* text-ink 是如实迁移：原 CSS `color: var(--text-muted)` 里 --text-muted 从未定义过，
          实测该文字渲染的是继承的正文墨色，不是灰色说明文字。 */}
      <p className="mt-1.5 mb-3 text-[0.82rem] text-ink">{ANSWER_STATUS_DESCRIPTIONS[result.answer_status]}</p>
      {/* mt/mb 是如实迁移：原 CSS 从未给 `.answer-text` 设过 margin，实测它吃的是 <p> 的
          UA 默认纵向 margin（1em，15px 字号下结算为 15px）。 */}
      <p className="mt-[15px] mb-[15px] text-[15px] leading-[1.9] whitespace-pre-wrap text-[#31394d]">{answerWithSourceLinks(result.answer, result.sources.length)}</p>
      <div className="my-5 grid grid-cols-5 overflow-hidden rounded-md border border-line max-[768px]:grid-cols-2" aria-label="查询性能">
        {METRICS.map(([key, label]) => {
          const value = result.latency_ms[key];
          return value === undefined ? null : (
            <div className="flex min-w-0 flex-col gap-[3px] border-r border-line p-[11px] last:border-r-0" key={key}>
              <span className="text-[9px] text-ink-faint">{label}</span>
              <strong className="overflow-hidden text-ellipsis whitespace-nowrap text-[10px] text-[#31394c]">{value.toFixed(0)} ms</strong>
            </div>
          );
        })}
        <div className="flex min-w-0 flex-col gap-[3px] p-[11px]"><span className="text-[9px] text-ink-faint">模型</span><strong className="overflow-hidden text-ellipsis whitespace-nowrap text-[10px] text-[#31394c]" title={result.model}>{result.model}</strong></div>
      </div>
      {/* showSources 恒为 false（ChatPage 是唯一调用方），下面这段目前是不可达代码；
          仍按原样迁移，皮肤直接采用与 evidence-list 一致的观感，未来若复用需重新评估。 */}
      {showSources ? <><div className="mt-[22px] flex items-center justify-between">
        <h3 className="m-0 text-[14px]">引用证据</h3>
        <span className="text-[10px] text-[#8c93a5]">{result.sources.length} 条</span>
      </div>
      <div className="mt-2.5 grid">
        {result.sources.map((source, index) => (
          <SourceCard key={source.chunk_id} source={source} index={index} />
        ))}
      </div>
      </> : null}
      <TechnicalDrawer result={result} />
    </section>
  );
}
