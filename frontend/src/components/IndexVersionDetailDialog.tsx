import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type {
  IndexEvaluationRun,
  IndexEvidenceChain,
  IndexVersion,
  LifecycleEvent,
  ValidationLayerResult,
  ValidationReport,
} from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { DataTable, type Column } from "./ui/DataTable";
import { Dialog } from "./ui/Dialog";
import { ErrorBanner } from "./ui/ErrorBanner";
import { ReasonHint } from "./ui/ReasonHint";
import { Skeleton } from "./ui/Skeleton";
import { ValidationReportViewer } from "./ValidationReportViewer";

const STATUS: Record<string, string> = {
  building: "构建中", validating: "验证中", ready: "待激活", active: "当前生效",
  previous: "上一版本", retired: "已退役", cleaned: "已清理",
  build_failed: "构建失败", validation_failed: "验证未通过",
};

const REASON: Record<string, string> = {
  legacy: "历史迁移", initial_build: "创建首个索引版本", config_changed: "配置已变更",
  document_snapshot_changed: "文档集合已变化", component_upgraded: "索引组件已升级",
  consistency_repair: "索引一致性修复", manual_rebuild: "主动创建回滚版本",
};

const COMPONENT: Record<string, string> = {
  parser_schema_version: "Parser 结构", chunking_policy_version: "Chunking 策略",
  embedding_model: "Embedding 模型", embedding_dimension: "Embedding 维度",
  vector_index_schema_version: "Vector 索引", keyword_index_schema_version: "Keyword 索引",
  metadata_schema_version: "Metadata 结构", acl_schema_version: "ACL 结构",
  citation_schema_version: "Citation 结构", reranker_model: "Reranker 模型",
};

const EVENT: Record<string, string> = {
  created: "创建", build_retried: "重试构建", build_succeeded: "构建成功",
  build_failed: "构建失败", validation_passed: "验证通过", validation_failed: "验证未通过",
  activated: "激活", deactivated: "退下", rolled_back: "回滚", retired: "退役", cleaned: "清理",
};

const EVALUATION_STATUS: Record<string, string> = {
  queued: "排队中", running: "评测中", succeeded: "已完成", failed: "失败", cancelled: "已取消",
};

const EVIDENCE_STATUS: Record<string, string> = {
  complete: "链路完整", partial: "链路不完整", missing: "缺少证据",
  match: "配置一致", mismatch: "配置不一致", unknown: "无法核对",
  passed: "验证通过", failed: "验证未通过", pending: "尚未完成",
  historical: "历史证据", released: "已发布", eligible: "可发布", blocked: "禁止发布",
};

function evidenceTone(value: string) {
  if (["mismatch", "failed", "blocked", "missing"].includes(value)) return "danger" as const;
  if (["partial", "unknown", "pending", "historical"].includes(value)) return "warning" as const;
  return "success" as const;
}

/**
 * 三层发布检查。区域标题用「三层验证」，不用 Validation Gate / 门禁这类内部治理术语。
 */
const LAYERS = [
  { key: "integrity_result", label: "完整性检查" },
  { key: "technical_result", label: "技术检查" },
  { key: "retrieval_result", label: "检索质量检查" },
] as const;

/**
 * 门禁状态 → 主 UI 文案。PASS / FAILED / BLOCKED / PENDING 不直接出现在页面上。
 *
 * 这张表同时覆盖两个取值域：**层**是 `pass | fail | unknown`
 * （`ValidationLayerResult.status`，unknown 表示这一层无法核对、不是通过），
 * **报告**是 `pending | running | pass | failed | cancelled`（`ValidationReport.status`）。
 * 合成一张表是因为两者要翻译成同一套用户语言；改的时候记住 `fail` 与 `failed` 分属不同层级。
 */
const LAYER_STATUS: Record<string, string> = {
  pass: "已通过", failed: "未通过", fail: "未通过",
  running: "检查中", blocked: "等待前置条件", pending: "未开始", unknown: "无法核对",
};

function valueLabel(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function statusTone(status: string) {
  if (status === "build_failed" || status === "validation_failed" || status === "failed") return "danger" as const;
  if (status === "building" || status === "validating" || status === "running" || status === "queued") return "brand" as const;
  if (status === "retired" || status === "cleaned" || status === "cancelled") return "neutral" as const;
  return "success" as const;
}

/**
 * 索引版本详情弹框。
 *
 * 从独立页面改回弹层：当初拆成页面的理由是「弹层放不下五块内容，size=lg 也只有
 * 900px」，而那其实是 `ui/Dialog` 没有 max-height、内容超出视口就被裁掉且滚不到造成的。
 * 这一轮把受控高度与内部滚动补进了 Dialog 本身，窄屏下 lg 还会转成全屏——放不下的
 * 前提不成立了。改回弹层换来的是：看完一个版本不用离开索引治理页，也不会像此前那样
 * 「返回」后落到「资料」Tab（`?tab=` 只认 data_sources 与 documents）。
 *
 * 深链 `/knowledge-bases/{kb}/index-versions/{version}` 仍然可用：它进知识库详情后
 * 自动打开这个弹框，关闭时把地址恢复成 `/knowledge-bases/{kb}`。
 *
 * **它自己按 ID 加载数据**，不依赖父组件传入：直接粘链接进来时没有上游状态。
 */
export function IndexVersionDetailDialog({
  open,
  knowledgeBaseId,
  versionId,
  onClose,
  onActionComplete,
  onOpen,
}: {
  open: boolean;
  knowledgeBaseId: string;
  versionId: string;
  onClose: () => void;
  /** 弹框内触发了会改变列表的动作时通知父组件刷新。本轮只有关闭时的一次收口。 */
  onActionComplete?: () => void;
  onOpen?: (path: string) => void;
}) {
  const [version, setVersion] = useState<IndexVersion | null>(null);
  const [reports, setReports] = useState<ValidationReport[]>([]);
  const [events, setEvents] = useState<LifecycleEvent[]>([]);
  const [evaluations, setEvaluations] = useState<IndexEvaluationRun[]>([]);
  const [evidence, setEvidence] = useState<IndexEvidenceChain | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  /** 治理数据单独记错误：拿不到它不该让整个弹框失败，配置与指纹仍然该看得到。 */
  const [governanceError, setGovernanceError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const versions = await api.listKnowledgeBaseIndexVersions(knowledgeBaseId);
      const found = versions.find((item) => item.index_version_id === versionId) ?? null;
      setVersion(found);
      if (!found) return;
      try {
        const [reportItems, eventItems, evaluationItems, evidenceItem] = await Promise.all([
          api.listIndexVersionValidations(knowledgeBaseId, versionId),
          api.listIndexVersionEvents(knowledgeBaseId, versionId),
          api.listIndexEvaluationRuns(knowledgeBaseId, versionId),
          api.getIndexEvidenceChain(knowledgeBaseId, versionId),
        ]);
        setReports(reportItems);
        setEvents(eventItems);
        setEvaluations(evaluationItems);
        setEvidence(evidenceItem);
        setGovernanceError("");
      } catch (reason) {
        // 静默吞掉会让「治理数据拉取失败」看起来像「这个版本没有治理数据」——
        // 两者在页面上长得一模一样，含义相反。
        setGovernanceError(
          reason instanceof Error ? reason.message : "验证报告、评测记录与生命周期读取失败。",
        );
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取索引版本。");
    } finally {
      setLoading(false);
    }
  }, [knowledgeBaseId, versionId]);

  // 用 Promise.resolve().then 而不是直接调用：load() 第一行是 setLoading(true)，
  // 在 effect 体内同步 setState 会触发级联渲染（eslint 的 react-hooks 规则会报 error）。
  useEffect(() => {
    if (!open) return;
    void Promise.resolve().then(load);
  }, [open, load]);

  const close = () => {
    onActionComplete?.();
    onClose();
  };

  const title = version?.version_no ? `索引版本 v${version.version_no}` : "索引版本详情";
  const legacy = version?.config_completeness === "unknown";
  const latestReport = reports[0] ?? null;

  return (
    <Dialog open={open} size="lg" title={title} description={versionId} onClose={close}>
      {loading ? (
        <div className="grid gap-3">
          <span role="status" className="sr-only">正在读取索引版本详情</span>
          <Skeleton className="h-6 w-56" />
          <Skeleton className="h-24 rounded-lg" />
          <Skeleton className="h-40 rounded-lg" />
        </div>
      ) : error || !version ? (
        <ErrorBanner>
          {error || `找不到索引版本 ${versionId}。它可能已被清理，或不属于当前知识库。`}
        </ErrorBanner>
      ) : (
        <div className="grid gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge shape="status" tone={statusTone(version.status)}>
              {STATUS[version.status] || version.status}
            </Badge>
            <span className="text-sm text-ink-faint">
              {REASON[version.creation_reason] || version.creation_reason}
            </span>
          </div>

          {legacy ? (
            <p className="m-0 rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
              历史版本 · 部分治理信息未记录。该版本创建于当前索引治理机制启用之前，
              因此没有完整的配置快照与组件清单，只能用于追溯，不能视为通过了现在的发布验证。
            </p>
          ) : null}

          {governanceError ? <ErrorBanner>{governanceError}</ErrorBanner> : null}

          <section className="grid gap-2 border-t border-divider pt-3">
            <h3 className="m-0 text-md font-semibold text-ink">版本信息</h3>
            <dl className="grid grid-cols-3 gap-x-6 gap-y-3 text-sm max-md:grid-cols-2 max-sm:grid-cols-1">
              <div><dt className="text-ink-faint">请求人</dt><dd className="m-0 mt-1 break-all">{version.requested_by || "系统 / 历史数据"}</dd></div>
              <div><dt className="text-ink-faint">创建时间</dt><dd className="m-0 mt-1">{new Date(version.created_at).toLocaleString("zh-CN")}</dd></div>
              <div><dt className="text-ink-faint">Parser</dt><dd className="m-0 mt-1">{version.parser_version}</dd></div>
              <div><dt className="text-ink-faint">Chunking</dt><dd className="m-0 mt-1">{version.chunking_version}</dd></div>
              <div><dt className="text-ink-faint">Embedding</dt><dd className="m-0 mt-1">{version.embedding_model} · {version.embedding_dimension} 维</dd></div>
              <div><dt className="text-ink-faint">文档快照</dt><dd className="m-0 mt-1 break-all">{version.document_snapshot_id || "无快照"}</dd></div>
              {version.excluded_documents_acknowledged ? <div><dt className="text-ink-faint">排除资料</dt><dd className="m-0 mt-1 text-warning">创建时已确认排除范围</dd></div> : null}
              {version.force_reason ? (
                <div className="col-span-full"><dt className="text-ink-faint">强制创建说明</dt><dd className="m-0 mt-1">{version.force_reason}</dd></div>
              ) : null}
            </dl>
          </section>

          {/* 正式评测。它是三层验证的前置证据，因此排在验证之前——页面顺序与业务顺序
              保持一致，用户不必自己在两块内容之间推导先后。 */}
          <section className="grid gap-2 border-t border-divider pt-3">
            <h3 className="m-0 text-md font-semibold text-ink">正式评测</h3>
            {evaluations.length ? (
              <ul className="m-0 grid gap-2 p-0">
                {evaluations.map((run) => (
                  <li key={run.evaluation_run_id} className="grid list-none gap-1 rounded-md border border-divider p-2 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge shape="status" tone={statusTone(run.status)}>
                        {EVALUATION_STATUS[run.status] || run.status}
                      </Badge>
                      <span className="text-ink-faint">{run.dataset_id} · {run.dataset_version}</span>
                      {run.status === "succeeded" ? (
                        <span className={run.passed ? "text-success" : "text-warning"}>
                          {run.passed ? "已达冻结阈值" : "未达冻结阈值"}
                        </span>
                      ) : null}
                    </div>
                    <small className="text-ink-faint">
                      {new Date(run.created_at).toLocaleString("zh-CN")}
                      {run.report_id ? ` · 报告 ${run.report_id}` : ""}
                      {run.attempt_count > 1 ? ` · 第 ${run.attempt_count} 次尝试` : ""}
                    </small>
                    {run.failure_reason ? (
                      <span className="text-danger-text">{run.failure_reason}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="m-0 text-sm text-ink-faint">
                还没有针对本版本的正式评测。三层验证需要一份使用本版本配置跑出来的正式报告。
              </p>
            )}
          </section>

          <section className="grid gap-2 border-t border-divider pt-3">
            <h3 className="m-0 text-md font-semibold text-ink">证据链</h3>
            {evidence ? (
              <dl className="grid grid-cols-4 gap-2 text-sm max-md:grid-cols-2 max-sm:grid-cols-1">
                {[
                  ["可追溯性", evidence.governance.traceability],
                  ["配置一致性", evidence.governance.configuration],
                  ["验证结论", evidence.governance.validation],
                  ["发布状态", evidence.governance.release],
                ].map(([label, value]) => (
                  <div key={label} className="flex items-center justify-between gap-2 rounded-md bg-canvas p-2">
                    <dt className="text-ink-faint">{label}</dt>
                    <dd className="m-0">
                      <Badge shape="status" tone={evidenceTone(value)}>{EVIDENCE_STATUS[value] || value}</Badge>
                    </dd>
                  </div>
                ))}
              </dl>
            ) : null}
            <ol className="m-0 grid grid-cols-5 gap-2 p-0 max-lg:grid-cols-3 max-sm:grid-cols-1">
              {[
                { label: "索引版本", value: evidence?.version.index_version_id },
                { label: "正式评测运行", value: evidence?.evaluation_run?.evaluation_run_id },
                { label: "正式报告", value: evidence?.formal_report?.report_id, report: true },
                { label: "验证报告", value: evidence?.validation_report?.validation_report_id },
                { label: "线上激活", value: evidence?.activation?.event_id },
              ].map((node, index) => (
                <li key={node.label} className="grid list-none gap-1 rounded-md border border-divider p-2 text-sm">
                  <span className="text-ink-faint">{index + 1}. {node.label}</span>
                  {node.report && node.value && onOpen ? (
                    <Button variant="link" className="h-auto min-w-0 justify-start break-all px-0 py-0 text-left font-mono text-xs" aria-label={`查看正式报告 ${node.value}`} onClick={() => onOpen(`/evaluation?view=reports&report=${encodeURIComponent(node.value!)}`)}>{node.value}</Button>
                  ) : (
                    <strong className="break-all font-mono text-xs">{node.value || "未形成证据"}</strong>
                  )}
                </li>
              ))}
            </ol>
            {evidence?.governance.reasons.length ? (
              <ul className="m-0 grid gap-1 pl-5 text-sm text-warning">
                {evidence.governance.reasons.map((reason) => (
                  <li key={reason.code}>
                    {reason.message} <code className="text-xs text-ink-faint">{reason.code}</code>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>

          <section className="grid gap-2 border-t border-divider pt-3">
            <h3 className="m-0 text-md font-semibold text-ink">三层验证</h3>
            {!latestReport ? (
              <p className="m-0 text-sm text-ink-faint">
                尚未执行三层验证。需要先有一份使用本版本配置生成的正式质量报告。
              </p>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge shape="status" tone={statusTone(latestReport.status)}>
                    {latestReport.status === "pass" ? "所有发布检查均已通过" : LAYER_STATUS[latestReport.status] || latestReport.status}
                  </Badge>
                  <small className="text-ink-faint">
                    规则 {latestReport.policy_version} · {new Date(latestReport.created_at).toLocaleString("zh-CN")}
                    {latestReport.report_source === "legacy_backfill" ? " · 历史回填，非正式验证" : ""}
                    {latestReport.report_source === "bootstrap" ? " · 首次索引，未经发布验证" : ""}
                  </small>
                </div>
                <ul className="m-0 grid grid-cols-3 gap-3 p-0 max-sm:grid-cols-1">
                  {LAYERS.map(({ key, label }) => {
                    const layer = latestReport[key] as ValidationLayerResult | undefined;
                    const state = layer?.status || "unknown";
                    return (
                      <li key={key} className="grid list-none gap-0.5 rounded-md border border-divider p-2">
                        <span className="text-sm text-ink-faint">{label}</span>
                        <span className="flex items-center gap-1.5">
                          <strong className={state === "fail" ? "text-danger-text" : state === "pass" ? "text-success" : "text-ink-faint"}>
                            {LAYER_STATUS[state] || state}
                          </strong>
                          <ReasonHint reason={layer?.note} label={`${label}的说明`} tone="muted" />
                        </span>
                      </li>
                    );
                  })}
                </ul>
                {latestReport.failure_items.length ? (
                  <ul className="m-0 grid gap-1 pl-5 text-sm text-danger-text">
                    {latestReport.failure_items.map((item) => (
                      <li key={`${item.layer}:${item.check_key}`}>
                        {item.check_key}：期望 {valueLabel(item.expected)}，实际 {valueLabel(item.actual)}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {reports.length > 1 ? (
                  <DataTable
                    label="历史验证报告"
                    density="compact"
                    rows={reports}
                    rowKey={(item) => item.validation_report_id}
                    columns={REPORT_COLUMNS}
                    emptyState={{ kind: "empty", title: "暂无验证报告", description: "执行三层验证后，每一次的检查结果都会保留在这里。" }}
                  />
                ) : null}
              </>
            )}
          </section>

          {/* 发布记录。只回答四件事的有无，完整 Hash 下沉到可展开的技术标识。 */}
          <section className="grid gap-2 border-t border-divider pt-3">
            <h3 className="m-0 text-md font-semibold text-ink">发布记录</h3>
            <dl className="grid grid-cols-2 gap-x-10 gap-y-1.5 text-sm max-sm:grid-cols-1 md:max-w-[720px]">
              <div className="grid grid-cols-[5rem_minmax(0,1fr)] items-baseline gap-2">
                <dt className="text-ink-faint">配置快照</dt>
                <dd className="m-0 truncate">{version.config_fingerprint ? "已记录" : "历史版本 · 未记录"}</dd>
              </div>
              <div className="grid grid-cols-[5rem_minmax(0,1fr)] items-baseline gap-2">
                <dt className="text-ink-faint">质量报告</dt>
                <dd className="m-0 truncate" title={version.evaluation_report_id || undefined}>{version.evaluation_report_id || "尚未绑定"}</dd>
              </div>
              <div className="grid grid-cols-[5rem_minmax(0,1fr)] items-baseline gap-2">
                <dt className="text-ink-faint">验证报告</dt>
                <dd className="m-0 min-w-0 truncate">
                  {version.validation_report_id ? (
                    <ValidationReportViewer
                      knowledgeBaseId={knowledgeBaseId}
                      versionId={versionId}
                      reportId={version.validation_report_id}
                      report={reports.find((item) => item.validation_report_id === version.validation_report_id)}
                    />
                  ) : "尚未执行"}
                </dd>
              </div>
              <div className="grid grid-cols-[5rem_minmax(0,1fr)] items-baseline gap-2">
                <dt className="text-ink-faint">线上激活</dt>
                <dd className="m-0 truncate">{version.activated_at ? new Date(version.activated_at).toLocaleString("zh-CN") : "尚未执行"}</dd>
              </div>
            </dl>
            <details className="rounded-md border border-divider p-2 text-sm">
              <summary className="cursor-pointer font-medium text-ink">技术标识与配置快照</summary>
              <dl className="mt-2 grid gap-2">
                <div><dt className="text-ink-faint">配置指纹</dt><dd className="m-0 mt-1 break-all font-mono text-xs">{version.config_fingerprint || "—"}</dd></div>
                <div>
                  <dt className="text-ink-faint">发布指纹</dt>
                  <dd className="m-0 mt-1 break-all font-mono text-xs">
                    {version.release_fingerprint || "—"}
                    {version.release_fingerprint ? null : (
                      <span className="ml-2 font-sans text-ink-faint">该历史版本发布时尚未启用发布指纹机制。</span>
                    )}
                  </dd>
                </div>
              </dl>
              {Object.keys(version.config_snapshot).length ? (
                <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all text-xs text-ink-muted">
                  {JSON.stringify(version.config_snapshot, null, 2)}
                </pre>
              ) : (
                <p className="m-0 mt-2 text-sm text-ink-faint">没有配置快照。</p>
              )}
            </details>
          </section>

          <section className="grid gap-2 border-t border-divider pt-3">
            <h3 className="m-0 text-md font-semibold text-ink">组件清单</h3>
            {Object.keys(version.component_manifest).length ? (
              <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm max-sm:grid-cols-1">
                {Object.entries(version.component_manifest).map(([key, value]) => (
                  <div key={key}><dt className="text-ink-faint">{COMPONENT[key] || key}</dt><dd className="m-0 mt-1 break-all">{valueLabel(value)}</dd></div>
                ))}
              </dl>
            ) : (
              <p className="m-0 text-sm text-warning">
                组件版本未记录，无法证明 Vector / Keyword / Metadata / ACL / Citation 属于同一代。
              </p>
            )}
          </section>

          <section className="grid gap-2 border-t border-divider pt-3">
            <h3 className="m-0 text-md font-semibold text-ink">生命周期</h3>
            {events.length ? (
              <ol className="m-0 grid gap-2 p-0">
                {events.map((event) => (
                  <li key={event.event_id} className="grid list-none gap-0.5 rounded-md border border-divider p-2 text-sm">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <strong>{EVENT[event.event_type] || event.event_type}</strong>
                      {event.from_status && event.to_status ? (
                        <span className="text-ink-faint">{STATUS[event.from_status] || event.from_status} → {STATUS[event.to_status] || event.to_status}</span>
                      ) : null}
                    </div>
                    <small className="text-ink-faint">
                      {new Date(event.created_at).toLocaleString("zh-CN")} · {event.actor_id || "系统自动"}
                      {event.reason ? ` · ${event.reason}` : ""}
                    </small>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="m-0 text-sm text-ink-faint">暂无生命周期事件。</p>
            )}
          </section>
        </div>
      )}
    </Dialog>
  );
}

const REPORT_COLUMNS: Column<ValidationReport>[] = [
  {
    key: "status",
    header: "结论",
    width: "16%",
    truncate: false,
    render: (item) => (
      <Badge shape="status" tone={statusTone(item.status)}>
        {item.status === "pass" ? "已通过" : LAYER_STATUS[item.status] || item.status}
      </Badge>
    ),
  },
  ...LAYERS.map(({ key, label }) => ({
    key,
    header: label,
    width: "18%",
    render: (item: ValidationReport) => {
      const layer = item[key] as ValidationLayerResult | undefined;
      const state = layer?.status || "unknown";
      return (
        <span className={state === "fail" ? "text-danger-text" : state === "pass" ? "text-success" : "text-ink-faint"}>
          {LAYER_STATUS[state] || state}
        </span>
      );
    },
  })),
  {
    key: "created",
    header: "时间",
    width: "30%",
    render: (item) => <span className="whitespace-nowrap">{new Date(item.created_at).toLocaleString("zh-CN")}</span>,
  },
];
