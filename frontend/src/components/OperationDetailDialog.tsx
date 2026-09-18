import type {
  DocumentIndexState,
  EvaluationMetric,
  GovernedOperation,
  IndexBuild,
  IndexEvaluationRunDetail,
} from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { DataTable } from "./ui/DataTable";
import { Dialog, DialogActions } from "./ui/Dialog";
import { ReasonHint } from "./ui/ReasonHint";
import { Skeleton } from "./ui/Skeleton";

const OPERATION_TYPE_LABEL: Record<string, string> = {
  index_build: "索引构建",
  index_evaluation: "正式评测",
  sync_run: "数据同步",
  file_upload: "文件上传",
  file_update: "文件更新",
  document_reprocess: "资料重新处理",
};

const STATUS_LABEL: Record<string, string> = {
  queued: "等待处理", running: "处理中", succeeded: "已完成",
  partial_failed: "部分失败", failed: "失败", cancelled: "已取消", aborted: "已中止",
};

const STAGE_LABEL: Record<string, string> = {
  queued: "等待处理", parse: "解析资料", parsing: "解析资料", chunk: "资料切片",
  chunking: "资料切片", vector: "构建向量索引", keyword: "构建关键词索引",
  metadata: "构建元数据索引", build: "构建索引", validating: "验证索引",
  validate: "验证索引", activate: "激活版本", retry: "正在重试", retry_wait: "等待重试",
  complete: "已完成", completed: "已完成", cancelled: "已取消", failed: "失败",
  // 与 backend/app/index_evaluation_runs.py 的 EVALUATION_STAGES 逐字对应。
  prepare_dataset: "准备数据集", build_corpus: "构建评测语料", retrieve: "执行召回",
  rerank: "执行精排", calculate_metrics: "计算指标", persist_report: "沉淀报告",
};

const LANE_STATUS_LABEL: Record<string, string> = {
  pending: "等待处理", queued: "等待处理", building: "构建中",
  ready: "可用", succeeded: "已完成", failed: "失败", cancelled: "已取消",
};

/** 关键指标的展示顺序与中文名。缺项显示「—」，不用 0 冒充。 */
const METRIC_LABEL: Array<[keyof MetricSource, string]> = [
  ["recall_at_5", "Recall@5"],
  ["recall_at_10", "Recall@10"],
  ["vector_mrr", "向量 MRR"],
  ["rerank_mrr", "精排 MRR"],
  ["ndcg_at_10", "nDCG@10"],
  ["metadata_filter_accuracy", "元数据过滤准确率"],
];

type MetricSource = {
  recall_at_5?: EvaluationMetric | null;
  recall_at_10?: EvaluationMetric | null;
  vector_mrr?: EvaluationMetric | null;
  rerank_mrr?: EvaluationMetric | null;
  ndcg_at_10?: EvaluationMetric | null;
  metadata_filter_accuracy?: EvaluationMetric | null;
};

function statusTone(status: string) {
  if (status === "failed" || status === "aborted") return "danger" as const;
  if (status === "partial_failed") return "warning" as const;
  if (status === "succeeded") return "success" as const;
  if (status === "cancelled") return "neutral" as const;
  return "brand" as const;
}

function timeLabel(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
}

/**
 * 运行记录的统一详情弹框。
 *
 * 此前同一个「详情」按钮通往两种完全不同的 UI：找得到 Index Build 就在表格下方展开一块
 * 行内区域，找不到就弹一个框。用户在一行学会的操作方式，到下一行行为就变了
 * （CLAUDE.md 第二条）。而且行内展开区持有的是打开那一刻的对象副本，1.5 秒一次的轮询
 * 只刷新列表不刷新它——构建过程中那块「x/y 份完成」是冻住的。
 *
 * 现在两种任务类型走同一个框架：通用摘要 + 按 `operation_type` 分支的类型化内容。
 * 数据全部来自真实响应，没有本地推导的进度或结论。
 */
export function OperationDetailDialog({
  operation,
  build,
  buildDocuments,
  buildLoading = false,
  evaluationRun,
  evaluationLoading = false,
  busy = false,
  onClose,
  onRetryEvaluation,
  onCancelEvaluation,
  onOpenVersion,
  onOpenDocuments,
  onDeleteDocument,
}: {
  operation: GovernedOperation;
  build?: IndexBuild | null;
  /** null 表示还在读取；[] 表示这次构建确实没有单资料记录。 */
  buildDocuments?: DocumentIndexState[] | null;
  buildLoading?: boolean;
  evaluationRun?: IndexEvaluationRunDetail | null;
  evaluationLoading?: boolean;
  busy?: boolean;
  onClose: () => void;
  onRetryEvaluation?: (runId: string) => void;
  onCancelEvaluation?: (runId: string) => void;
  onOpenVersion?: (versionId: string) => void;
  /** 切到资料页并定位到该资料，用户在那里可以替换文件或删除。 */
  onOpenDocuments?: (documentId: string) => void;
  onDeleteDocument?: (documentId: string, filename: string) => void;
}) {
  const typeLabel = OPERATION_TYPE_LABEL[operation.operation_type] || operation.operation_type;
  const versionId = build?.index_version_id || evaluationRun?.index_version_id || null;
  const missingSources = (buildDocuments ?? []).filter(
    (item) => item.failure_code === "SOURCE_FILE_MISSING",
  );

  return (
    <Dialog open size="lg" title={`${typeLabel}详情`} description={operation.operation_id} onClose={onClose}>
      <div className="grid gap-3">
        <dl className="grid grid-cols-4 gap-x-6 gap-y-3 text-sm max-md:grid-cols-2 max-sm:grid-cols-1">
          <div>
            <dt className="text-ink-faint">状态</dt>
            <dd className="m-0 mt-1">
              <Badge shape="status" tone={statusTone(operation.status)}>
                {STATUS_LABEL[operation.status] || operation.status}
              </Badge>
            </dd>
          </div>
          <div>
            <dt className="text-ink-faint">当前阶段</dt>
            <dd className="m-0 mt-1">{STAGE_LABEL[operation.current_stage] || operation.current_stage}</dd>
          </div>
          <div>
            <dt className="text-ink-faint">进度</dt>
            <dd className="m-0 mt-1 tabular-nums">
              {operation.progress_percent === null ? "—" : `${Math.round(operation.progress_percent)}%`}
            </dd>
          </div>
          <div>
            <dt className="text-ink-faint">目标版本</dt>
            <dd className="m-0 mt-1 break-all">
              {versionId ? (
                onOpenVersion ? (
                  <Button variant="link" size="sm" onClick={() => onOpenVersion(versionId)}>
                    {versionId}
                  </Button>
                ) : (
                  versionId
                )
              ) : (
                "—"
              )}
            </dd>
          </div>
          <div><dt className="text-ink-faint">开始时间</dt><dd className="m-0 mt-1">{timeLabel(operation.started_at)}</dd></div>
          <div><dt className="text-ink-faint">结束时间</dt><dd className="m-0 mt-1">{timeLabel(operation.finished_at)}</dd></div>
          <div>
            <dt className="text-ink-faint">处理数量</dt>
            <dd className="m-0 mt-1 tabular-nums">
              {operation.completed_count}/{operation.total_count}
              {operation.processing_count ? ` · 处理中 ${operation.processing_count}` : ""}
            </dd>
          </div>
          <div>
            <dt className="text-ink-faint">失败数量</dt>
            <dd className={`m-0 mt-1 tabular-nums ${operation.failed_count ? "text-danger-text" : ""}`}>
              {operation.failed_count}
            </dd>
          </div>
        </dl>

        {operation.error_message ? (
          <p className="m-0 rounded-md border border-danger/30 bg-danger-subtle p-3 text-sm text-danger-text">
            {/* 长错误在弹框里换行显示，主表格那一行因此不会被撑高。 */}
            <span className="break-all whitespace-pre-wrap">{operation.error_message}</span>
          </p>
        ) : null}

        {operation.operation_type === "index_build" ? (
          <section className="grid gap-2 border-t border-divider pt-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="m-0 text-md font-semibold text-ink">资料索引状态</h3>
              {build ? (
                <small className="text-ink-faint">
                  第 {build.attempt_no} 次构建 · {build.succeeded_documents}/{build.total_documents} 份完成
                  · {build.failed_documents} 份失败{buildLoading ? " · 读取中" : ""}
                </small>
              ) : null}
            </div>
            {missingSources.length ? (
              <div className="grid gap-2 rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
                <strong>
                  {missingSources.length} 份资料的源文件已丢失，索引无法构建。
                </strong>
                <span>
                  重新上传同名文件，或删除这些失效资料。两种做法之后都需要
                  <strong>重新创建一个索引版本</strong>——已冻结的版本快照不会被修改。
                </span>
                <div className="flex flex-wrap gap-2">
                  {onOpenDocuments ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => onOpenDocuments(missingSources[0].document_id)}
                    >
                      前往资料页重新上传
                    </Button>
                  ) : null}
                  {onDeleteDocument ? (
                    <Button
                      size="sm"
                      variant="destructive"
                      loading={busy}
                      onClick={() =>
                        onDeleteDocument(missingSources[0].document_id, missingSources[0].filename)
                      }
                    >
                      删除「{missingSources[0].filename}」
                    </Button>
                  ) : null}
                </div>
              </div>
            ) : null}
            <DataTable
              label="资料索引状态"
              density="compact"
              rows={buildDocuments ?? null}
              rowKey={(item) => item.document_id}
              columns={[
                {
                  key: "document",
                  header: "资料",
                  width: "34%",
                  tooltip: (item) => item.filename,
                  render: (item) => item.filename,
                },
                { key: "vector", header: "Vector", width: "11%", render: (item) => LANE_STATUS_LABEL[item.vector_status] || item.vector_status },
                { key: "keyword", header: "Keyword", width: "11%", render: (item) => LANE_STATUS_LABEL[item.keyword_status] || item.keyword_status },
                { key: "metadata", header: "Metadata", width: "12%", render: (item) => LANE_STATUS_LABEL[item.metadata_status] || item.metadata_status },
                { key: "chunks", header: "切片数", width: "10%", numeric: true, render: (item) => item.chunk_count },
                {
                  key: "status",
                  header: "整体状态",
                  width: "22%",
                  truncate: false,
                  render: (item) => (
                    <span className="flex items-center gap-1.5">
                      <Badge shape="status" tone={item.overall_status === "failed" ? "danger" : item.overall_status === "ready" ? "success" : "brand"}>
                        {LANE_STATUS_LABEL[item.overall_status] || item.overall_status}
                      </Badge>
                      <ReasonHint
                        reason={item.failure_reason || item.failure_code}
                        label={`${item.filename} 的索引失败原因`}
                      />
                    </span>
                  ),
                },
              ]}
              emptyState={{ kind: "empty", title: "暂无资料状态", description: "旧构建批次未记录单资料状态。" }}
            />
          </section>
        ) : null}

        {operation.operation_type === "index_evaluation" ? (
          <section className="grid gap-2 border-t border-divider pt-3">
            <h3 className="m-0 text-md font-semibold text-ink">正式评测结果</h3>
            {evaluationLoading && !evaluationRun ? (
              <Skeleton className="h-24 rounded-lg" />
            ) : !evaluationRun ? (
              <p className="m-0 text-sm text-ink-faint">读取不到这次评测的明细。</p>
            ) : (
              <>
                <dl className="grid grid-cols-3 gap-x-6 gap-y-3 text-sm max-sm:grid-cols-1">
                  <div><dt className="text-ink-faint">数据集</dt><dd className="m-0 mt-1">{evaluationRun.dataset_id} · {evaluationRun.dataset_version}</dd></div>
                  <div>
                    <dt className="text-ink-faint">候选配置指纹</dt>
                    <dd className="m-0 mt-1 break-all font-mono text-xs" title={evaluationRun.config_fingerprint || undefined}>
                      {evaluationRun.config_fingerprint ? `${evaluationRun.config_fingerprint.slice(0, 12)}…` : "—"}
                    </dd>
                  </div>
                  <div><dt className="text-ink-faint">基线报告</dt><dd className="m-0 mt-1 break-all">{evaluationRun.baseline_report_id || "无基线（首次评测）"}</dd></div>
                  <div><dt className="text-ink-faint">报告 ID</dt><dd className="m-0 mt-1 break-all">{evaluationRun.report_id || "—"}</dd></div>
                  <div><dt className="text-ink-faint">请求人</dt><dd className="m-0 mt-1 break-all">{evaluationRun.requested_by || "—"}</dd></div>
                  <div><dt className="text-ink-faint">尝试次数</dt><dd className="m-0 mt-1 tabular-nums">{evaluationRun.attempt_count}/{evaluationRun.max_attempts}</dd></div>
                </dl>

                {evaluationRun.status === "succeeded" ? (
                  <p className={`m-0 rounded-md border p-3 text-sm ${evaluationRun.passed ? "border-success/30 bg-success-subtle text-success" : "border-warning/30 bg-warning/10 text-warning"}`}>
                    {evaluationRun.passed
                      ? "全部指标达到冻结阈值。"
                      : "部分指标未达到冻结阈值。这份报告仍是受控正式运行的证据，可用于三层验证——最终是否可发布由三层验证决定，不由绝对阈值单独决定。"}
                  </p>
                ) : null}

                {evaluationRun.report ? (
                  <dl className="grid grid-cols-3 gap-x-6 gap-y-3 text-sm max-sm:grid-cols-1">
                    {METRIC_LABEL.map(([key, label]) => {
                      const metric = evaluationRun.report?.[key] as EvaluationMetric | null | undefined;
                      return (
                        <div key={key}>
                          <dt className="text-ink-faint">{label}</dt>
                          <dd className="m-0 mt-1 tabular-nums">
                            {metric ? (
                              <span className={metric.passed ? "text-ink" : "text-warning"}>
                                {(metric.value * 100).toFixed(1)}%
                                <span className="ml-1 text-xs text-ink-faint">
                                  / 阈值 {(metric.threshold * 100).toFixed(1)}%
                                </span>
                                {metric.regressed ? <span className="ml-1 text-danger-text">较基线回退</span> : null}
                              </span>
                            ) : (
                              "—"
                            )}
                          </dd>
                        </div>
                      );
                    })}
                  </dl>
                ) : null}

                {evaluationRun.failure_reason ? (
                  <p className="m-0 rounded-md border border-danger/30 bg-danger-subtle p-3 text-sm text-danger-text">
                    {evaluationRun.failure_reason}
                    {evaluationRun.failure_code ? (
                      <span className="ml-2 font-mono text-xs">{evaluationRun.failure_code}</span>
                    ) : null}
                  </p>
                ) : null}
              </>
            )}
          </section>
        ) : null}
      </div>

      <DialogActions>
        {evaluationRun && evaluationRun.status === "queued" && onCancelEvaluation ? (
          <Button
            variant="secondary"
            loading={busy}
            onClick={() => onCancelEvaluation(evaluationRun.evaluation_run_id)}
          >
            取消评测
          </Button>
        ) : null}
        {evaluationRun && evaluationRun.status === "failed" && onRetryEvaluation ? (
          <Button
            loading={busy}
            blockedReason={
              evaluationRun.attempt_count >= evaluationRun.max_attempts
                ? "已达最大重试次数，请创建新的评测任务"
                : undefined
            }
            onClick={() => onRetryEvaluation(evaluationRun.evaluation_run_id)}
          >
            重新运行评测
          </Button>
        ) : null}
        <Button variant="secondary" onClick={onClose}>关闭</Button>
      </DialogActions>
    </Dialog>
  );
}
