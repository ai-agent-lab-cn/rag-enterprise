import { Badge } from "./ui/Badge";
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
const SOURCE_TYPE_LABELS: Record<string, string> = { file: "文件", object_storage: "对象存储", web: "网页", connector: "连接器" };
const INTENT_LABELS: Record<string, string> = { fact_lookup: "事实查找", summarize: "摘要总结", compare: "对比分析", procedure: "操作流程" };

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
  const rolloutStage = typeof result.policy_snapshot?.rollout_stage === "string" ? result.policy_snapshot.rollout_stage : null;
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
          {routing ? <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">意图：{routing.intent ? INTENT_LABELS[routing.intent] ?? routing.intent : routing.control_outcome} · 置信度 {(routing.confidence * 100).toFixed(0)}%{routing.follow_up_rewritten ? " · 已改写追问" : ""}{routing.fallback_used ? " · 已降级" : ""}<br/>路由依据：{routing.reason}</p> : null}
          <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">返回 {result.sources.length} 条来源，并按融合排序结果展示。</p>
          {webSourceCount ? <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">来源构成：知识库 {result.sources.length - webSourceCount} 条 / Web {webSourceCount} 条</p> : null}
          {rolloutStage ? <p className="mt-[10px] mb-[10px] text-xs leading-[1.5] text-ink-faint">发布阶段：{rolloutStage} · 联网：{webSourceCount ? "已触发并采用证据" : "未采用 Web 证据"}</p> : null}
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
