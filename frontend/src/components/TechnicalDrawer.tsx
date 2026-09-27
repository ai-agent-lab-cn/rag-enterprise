import { Badge } from "./ui/Badge";
import { asWebDecision, describeWebExecution } from "../types";
import type { ModuleExecution, QueryResult, Source } from "../types";

interface TechnicalDrawerProps {
  result: QueryResult;
}

const LATENCY_LABELS: Record<string, string> = {
  retrieval: "召回",
  rerank: "交叉编码精排",
  generation: "答案生成",
  total: "总耗时",
};
const SOURCE_TYPE_LABELS: Record<string, string> = { file: "文件", object_storage: "对象存储", web: "网页", connector: "连接器" };
const INTENT_LABELS: Record<string, string> = { greeting: "问候", fact_lookup: "事实查找", summarize: "摘要总结", compare: "对比分析", procedure: "操作流程" };
/** 没有映射就把英文枚举原样吐给用户，所以四个取值一个都不能少。 */
const CONTROL_OUTCOME_LABELS: Record<string, string> = { route: "进入管线", social: "问候旁路", clarify: "需要澄清", out_of_scope: "超出范围" };
const GATE_OUTCOME_LABELS: Record<string, string> = { pass: "通过", needs_web: "需要 Web 补检", stale: "时效未验证", reject: "拒答" };
/** evidence_gate.ReasonCode 的全部 13 个取值（evidence_gate.py:34-48）。 */
const REASON_CODE_LABELS: Record<string, string> = {
  kb_evidence_sufficient: "知识库证据充足",
  kb_evidence_below_minimum: "知识库证据低于最低数量",
  evidence_below_minimum: "证据总数低于最低数量",
  no_kb_anchor: "缺少可锚定的知识库证据",
  relevance_below_threshold: "相关性低于阈值",
  citation_incomplete: "引用字段不完整",
  web_supplement_available: "可发起 Web 补检",
  web_unavailable: "Web 不可用",
  web_not_executed: "未发起 Web 检索",
  web_no_qualified_result: "Web 无合格结果",
  freshness_required: "问题要求时效",
  freshness_verified: "时效已验证",
  freshness_unverified: "时效未验证",
};

/** `metrics` 是 `Record<string, unknown>`：每个读取口就地收窄，读不到就当没有，不猜。 */
function moduleMetrics(modules: ModuleExecution[], moduleKey: string) {
  return modules.find((item) => item.module_key === moduleKey)?.metrics ?? null;
}
function numberMetric(metrics: Record<string, unknown> | null, key: string) {
  const value = metrics?.[key];
  return typeof value === "number" ? value : null;
}
function stringMetric(metrics: Record<string, unknown> | null, key: string) {
  const value = metrics?.[key];
  return typeof value === "string" ? value : null;
}
function reasonCodes(metrics: Record<string, unknown> | null) {
  const value = metrics?.reason_codes;
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}
function reasonText(codes: string[]) {
  return codes.length ? ` · 原因：${codes.map((item) => REASON_CODE_LABELS[item] ?? item).join("、")}` : "";
}

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
  const queryMetadata = result.query_metadata;
  const governance = result.generation_governance;
  const routing = result.routing;
  const modules = result.module_executions ?? [];
  const webSourceCount = result.sources.filter((item) => item.evidence_source_type === "web").length;
  // 联网状态只看模块轨迹，不看「最终有没有 Web 来源」（spec 9.2）。反推会把三件不同的事
  // 说成同一句：Web 关着、搜了没结果、搜到了但没通过门禁——它们的 Web 来源数都是 0。
  const preliminaryMetrics = moduleMetrics(modules, "evidence.preliminary_gate");
  const finalGateMetrics = moduleMetrics(modules, "evidence.final_gate");
  const webMetrics = moduleMetrics(modules, "retrieval.web_policy");
  const preliminaryOutcome = stringMetric(preliminaryMetrics, "outcome");
  const finalGateOutcome = stringMetric(finalGateMetrics, "outcome");
  // clarify / out_of_scope 在 service.py:398 就早返回了，检索管线（含 retrieval.web_policy）
  // 一次都没跑过。「轨迹里没有这个模块」在这里和「历史记录没存轨迹」是两件不同的事：
  // 共用 describeWebExecution 的兜底文案，会让一条刚刚发生的查询被告知"历史记录未保存"。
  const pipelineSkipped =
    routing != null && (routing.control_outcome !== "route" || routing.intent === null);
  const webStatus = describeWebExecution({
    decision: asWebDecision(webMetrics?.decision),
    resultCount: numberMetric(webMetrics, "result_count"),
    webCount: numberMetric(finalGateMetrics, "web_count"),
    reasonCodes: reasonCodes(finalGateMetrics),
  });
  const appliedFilters = queryMetadata?.applied_filters;
  const filterLabels = [
    ...(appliedFilters?.categories ?? []).map((item) => `分类：${item}`),
    ...(appliedFilters?.tags ?? []).map((item) => `标签：${item}`),
    ...(appliedFilters?.source_types ?? []).map((item) => `来源：${SOURCE_TYPE_LABELS[item] ?? item}`),
    ...(appliedFilters?.created_from ? [`开始：${new Date(appliedFilters.created_from).toLocaleDateString("zh-CN")}`] : []),
    ...(appliedFilters?.created_to ? [`结束：${new Date(appliedFilters.created_to).toLocaleDateString("zh-CN")}`] : []),
  ];
  const queryStrategy = queryMetadata?.strategy === "controlled_expansion"
    ? "可控查询扩展"
    : queryMetadata?.strategy === "normalized"
      ? "查询规范化"
      : "原始查询";
  return (
    <details className="mt-4 rounded-md border border-line">
      <summary className="flex list-none cursor-pointer items-center justify-between px-3.5 py-3 text-sm font-semibold text-ink-muted">
        查看技术细节 <span aria-hidden="true">＋</span>
      </summary>
      <div className="grid grid-cols-1 border-t border-divider md:grid-cols-3">
        <section className="min-w-0 border-b border-divider p-3.5 last:border-b-0 md:border-r md:border-b-0 md:last:border-r-0">
          <span className="text-[12px] text-[#7165d8] tracking-[0.04em] font-semibold">检索过程</span>
          <h3 className="mt-[7px] mb-[7px] overflow-hidden text-ellipsis whitespace-nowrap text-sm text-ink">{result.pipeline_profile ? `${result.pipeline_profile}@${result.profile_version ?? "—"}` : hybrid ? "向量 + 词法 → 精排 → 生成" : "召回 → 精排 → 生成"}</h3>
          {routing ? <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">意图：{routing.intent ? INTENT_LABELS[routing.intent] ?? routing.intent : CONTROL_OUTCOME_LABELS[routing.control_outcome] ?? routing.control_outcome} · 置信度 {(routing.confidence * 100).toFixed(0)}%{routing.requires_freshness ? " · 要求时效" : ""}{routing.follow_up_rewritten ? " · 已改写追问" : ""}{routing.fallback_used ? " · 已降级" : ""}<br/>路由依据：{routing.reason}</p> : null}
          {/* 和下面那行「联网」同一个判据：没进检索管线时这句会说成「返回 0 条来源，并按
              融合排序结果展示」——一次没发生过的融合排序。说假话比说不出话更糟，而原因由
              「联网」那行负责讲，这里不必再重复一遍。 */}
          {pipelineSkipped ? null : <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">返回 {result.sources.length} 条来源，并按融合排序结果展示。</p>}
          {preliminaryOutcome ? <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">初步门禁：{GATE_OUTCOME_LABELS[preliminaryOutcome] ?? preliminaryOutcome} · 知识库合格 {numberMetric(preliminaryMetrics, "kb_count") ?? 0} 条{reasonText(reasonCodes(preliminaryMetrics))}</p> : null}
          <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">联网：{pipelineSkipped ? "本次未进入检索管线，没有发起 Web 检索" : webStatus}</p>
          {/* 门禁口径与「返回 N 条来源」不是同一件事：拒答时 selected 为空但合格计数照实上报，
              所以标签也要跟着换——「最终证据：知识库 0 条 / Web 2 条」自己和自己打架。
              没有 Final Gate 轨迹的历史记录退回按来源反推——那时它是唯一能说的话。 */}
          {finalGateOutcome ? <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">{finalGateOutcome === "reject" ? "合格证据" : "最终证据"}：知识库 {numberMetric(finalGateMetrics, "kb_count") ?? 0} 条 / Web {numberMetric(finalGateMetrics, "web_count") ?? 0} 条 · Final Gate {GATE_OUTCOME_LABELS[finalGateOutcome] ?? finalGateOutcome}{reasonText(reasonCodes(finalGateMetrics))}</p>
            : webSourceCount ? <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">来源构成：知识库 {result.sources.length - webSourceCount} 条 / Web {webSourceCount} 条</p> : null}
          {hybrid ? (
            <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">
              命中通路：双路 {breakdown.both} 条 / 仅向量 {breakdown.vectorOnly} 条 / 仅词法{" "}
              {breakdown.lexicalOnly} 条
            </p>
          ) : null}
          {queryMetadata ? (
            <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">{queryStrategy} · {queryMetadata.query_count} 路查询{queryMetadata.fallback_used ? " · 已降级" : ""}</p>
          ) : null}
          {filterLabels.length ? (
            <div className="mt-2 flex flex-wrap gap-1.5" aria-label="实际生效的过滤条件">
              {filterLabels.map((item) => <Badge key={item} tone="brand">{item}</Badge>)}
            </div>
          ) : null}
          {queryMetadata ? <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">候选：召回 {queryMetadata.retrieved_candidate_count} / 融合 {queryMetadata.fused_candidate_count} / 返回 {queryMetadata.returned_source_count}{queryMetadata.filter_match_count !== null ? ` · 过滤命中 ${queryMetadata.filter_match_count}` : ""}</p> : null}
          {firstSource ? (
            <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">最高来源分数：召回 {firstSource.retrieval_score.toFixed(3)} / 精排 {firstSource.rerank_score.toFixed(3)}</p>
          ) : null}
        </section>
        <section className="min-w-0 border-b border-divider p-3.5 last:border-b-0 md:border-r md:border-b-0 md:last:border-r-0">
          <span className="text-[12px] text-[#7165d8] tracking-[0.04em] font-semibold">模型与参数</span>
          <h3 className="mt-[7px] mb-[7px] overflow-hidden text-ellipsis whitespace-nowrap text-sm text-ink" title={result.model}>{result.model}</h3>
          <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">默认召回 10 条候选，精排后返回 5 条来源。</p>
          {governance ? <><p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">证据：{governance.evidence_count} 条 / 最低 {governance.minimum_evidence_count} 条</p><p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">引用校验：{governance.citation_valid ? "通过" : "失败"} · 声明覆盖：{governance.claim_citation_coverage ? "通过" : "失败"}</p><p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">权限、当前版本、检索状态：{governance.acl_revalidated && governance.current_version_revalidated && governance.retrieval_status_revalidated ? "已复核" : "未通过"}</p></> : null}
        </section>
        <section className="min-w-0 p-3.5 last:border-b-0 md:border-r md:border-b-0 md:last:border-r-0">
          <span className="text-[12px] text-[#7165d8] tracking-[0.04em] font-semibold">性能耗时</span>
          <dl className="mt-[7px] mb-0">
            {Object.entries(result.latency_ms).map(([key, value]) => (
              <div key={key} className="flex justify-between gap-2.5 py-1">
                <dt className="m-0 text-xs text-ink-faint">{LATENCY_LABELS[key] ?? key}</dt>
                <dd className="m-0 text-xs text-ink-faint">{value.toFixed(0)} ms</dd>
              </div>
            ))}
          </dl>
          {modules.length ? <ol className="mt-3 grid gap-1.5 border-t border-divider pt-3 pl-0 list-none" aria-label="模块执行时间线">{modules.map((item) => <li className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-2 text-[11px] text-ink-faint" key={item.module_execution_id}><span className="min-w-0 truncate" title={item.module_key}>{item.sequence}. {item.module_key}</span><span className={item.status === "failed" ? "text-danger-text" : item.status === "degraded" ? "text-warning" : ""}>{item.status} · {item.duration_ms.toFixed(0)} ms</span>{item.fallback_reason || item.error_message ? <small className="col-span-2 mt-0.5 leading-5 text-warning">{item.fallback_reason ?? item.error_message}</small> : null}</li>)}</ol> : null}
          {result.active_index_version_id ? <p className="mt-3 mb-0 break-all text-[10px] text-ink-faint">Active Index：{result.active_index_version_id}</p> : null}
          {result.execution_id ? <p className="mt-3 mb-0 break-all text-[10px] text-ink-faint" title={result.execution_id}>执行记录：{result.execution_id}</p> : null}
        </section>
      </div>
    </details>
  );
}
