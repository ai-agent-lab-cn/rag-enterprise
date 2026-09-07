import type { IndexVersion, LifecycleEvent, ValidationLayerResult, ValidationReport } from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Dialog, DialogActions } from "./ui/Dialog";

const STATUS: Record<string, string> = {
  building: "构建中", validating: "验证中", ready: "待激活", active: "当前生效",
  previous: "上一版本", retired: "已退役", cleaned: "已清理",
  build_failed: "构建失败", validation_failed: "验证失败",
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
  build_failed: "构建失败", validation_passed: "验证通过", validation_failed: "验证失败",
  activated: "激活", deactivated: "退下", rolled_back: "回滚", retired: "退役", cleaned: "清理",
};

const LAYERS = [
  { key: "integrity_result", label: "完整性" },
  { key: "technical_result", label: "技术" },
  { key: "retrieval_result", label: "检索质量" },
] as const;

function valueLabel(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function statusTone(status: string) {
  if (status === "build_failed" || status === "validation_failed" || status === "failed") return "danger" as const;
  if (status === "building" || status === "validating" || status === "running") return "brand" as const;
  if (status === "retired" || status === "cleaned") return "neutral" as const;
  return "success" as const;
}

export function IndexVersionDetailDialog({
  version,
  reports,
  events,
  onClose,
}: {
  version: IndexVersion;
  reports: ValidationReport[];
  events: LifecycleEvent[];
  onClose: () => void;
}) {
  const legacy = version.config_completeness === "unknown";

  return (
    <Dialog open size="lg" title="索引版本详情" description={version.index_version_id} onClose={onClose}>
      {legacy ? (
        <p className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
          Legacy Version：历史数据没有完整配置快照与组件清单，仅用于追溯；不能把未知字段视为通过新门禁。
        </p>
      ) : null}

      <dl className="grid grid-cols-3 gap-x-6 gap-y-3 text-sm max-md:grid-cols-2 max-sm:grid-cols-1">
        <div><dt className="text-ink-faint">版本号</dt><dd className="m-0 mt-1">{version.version_no ? `v${version.version_no}` : "Legacy"}</dd></div>
        <div><dt className="text-ink-faint">状态</dt><dd className="m-0 mt-1"><Badge shape="status" tone={statusTone(version.status)}>{STATUS[version.status] || version.status}</Badge></dd></div>
        <div><dt className="text-ink-faint">创建原因</dt><dd className="m-0 mt-1">{REASON[version.creation_reason] || version.creation_reason}</dd></div>
        <div><dt className="text-ink-faint">请求人</dt><dd className="m-0 mt-1 break-all">{version.requested_by || "系统 / 历史数据"}</dd></div>
        <div><dt className="text-ink-faint">文档快照</dt><dd className="m-0 mt-1 break-all">{version.document_snapshot_id || "无快照"}</dd></div>
        <div><dt className="text-ink-faint">绑定验证报告</dt><dd className="m-0 mt-1 break-all">{version.validation_report_id || "待验证"}</dd></div>
        <div><dt className="text-ink-faint">Parser</dt><dd className="m-0 mt-1">{version.parser_version}</dd></div>
        <div><dt className="text-ink-faint">Chunking</dt><dd className="m-0 mt-1">{version.chunking_version}</dd></div>
        <div><dt className="text-ink-faint">Embedding</dt><dd className="m-0 mt-1">{version.embedding_model} · {version.embedding_dimension} 维</dd></div>
        <div className="col-span-full"><dt className="text-ink-faint">强制创建说明</dt><dd className="m-0 mt-1">{version.force_reason || "—"}</dd></div>
      </dl>

      <section className="mt-4 grid gap-2 border-t border-divider pt-3">
        <h4 className="m-0 text-[13px] text-ink">冻结发布证据</h4>
        <dl className="grid gap-2 text-sm">
          <div><dt className="text-ink-faint">配置指纹</dt><dd className="m-0 mt-1 break-all">{version.config_fingerprint}</dd></div>
          <div><dt className="text-ink-faint">发布指纹</dt><dd className="m-0 mt-1 break-all">{version.release_fingerprint || "Legacy 未生成"}</dd></div>
        </dl>
        {Object.keys(version.config_snapshot).length ? (
          <details className="rounded border border-divider p-2 text-sm">
            <summary className="cursor-pointer font-medium text-ink">查看不可变配置快照</summary>
            <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all text-xs text-ink-muted">{JSON.stringify(version.config_snapshot, null, 2)}</pre>
          </details>
        ) : null}
      </section>

      <section className="mt-4 grid gap-2 border-t border-divider pt-3">
        <h4 className="m-0 text-[13px] text-ink">统一组件清单</h4>
        {Object.keys(version.component_manifest).length ? (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm max-sm:grid-cols-1">
            {Object.entries(version.component_manifest).map(([key, value]) => (
              <div key={key}><dt className="text-ink-faint">{COMPONENT[key] || key}</dt><dd className="m-0 mt-1 break-all">{valueLabel(value)}</dd></div>
            ))}
          </dl>
        ) : <p className="m-0 text-sm text-warning">组件版本未知，无法证明 Vector / Keyword / Metadata / ACL / Citation 同代。</p>}
      </section>

      <section className="mt-4 grid gap-2 border-t border-divider pt-3">
        <h4 className="m-0 text-[13px] text-ink">三层 Validate 报告</h4>
        {reports.length ? reports.map((report) => (
          <article key={report.validation_report_id} className="grid gap-2 rounded border border-divider p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Badge shape="status" tone={statusTone(report.status)}>{report.status === "pass" ? "验证通过" : report.status === "failed" ? "验证失败" : report.status}</Badge>
              <small className="text-ink-faint">规则 {report.policy_version} · {new Date(report.created_at).toLocaleString("zh-CN")}</small>
            </div>
            <ul className="m-0 grid grid-cols-3 gap-2 p-0 text-sm max-sm:grid-cols-1">
              {LAYERS.map(({ key, label }) => {
                const layer = report[key] as ValidationLayerResult;
                return <li key={key} className="list-none"><span className="text-ink-faint">{label}</span><strong className={`ml-2 ${layer?.status === "fail" ? "text-danger-text" : "text-ink"}`}>{layer?.status === "pass" ? "通过" : layer?.status === "fail" ? "未通过" : "无法核对"}</strong></li>;
              })}
            </ul>
            {report.failure_items.length ? <ul className="m-0 grid gap-1 pl-5 text-sm text-danger-text">{report.failure_items.map((item) => <li key={`${item.layer}:${item.check_key}`}>{item.check_key}：期望 {valueLabel(item.expected)}，实际 {valueLabel(item.actual)}</li>)}</ul> : null}
          </article>
        )) : <p className="m-0 text-sm text-ink-faint">暂无验证报告。</p>}
      </section>

      <section className="mt-4 grid gap-2 border-t border-divider pt-3">
        <h4 className="m-0 text-[13px] text-ink">生命周期</h4>
        {events.length ? <ol className="m-0 grid gap-2 p-0">{events.map((event) => (
          <li key={event.event_id} className="grid gap-0.5 rounded border border-divider p-2 text-sm">
            <div className="flex flex-wrap items-baseline gap-2"><strong>{EVENT[event.event_type] || event.event_type}</strong>{event.from_status && event.to_status ? <span className="text-ink-faint">{STATUS[event.from_status] || event.from_status} → {STATUS[event.to_status] || event.to_status}</span> : null}</div>
            <small className="text-ink-faint">{new Date(event.created_at).toLocaleString("zh-CN")} · {event.actor_id || "系统自动"}{event.reason ? ` · ${event.reason}` : ""}</small>
          </li>
        ))}</ol> : <p className="m-0 text-sm text-ink-faint">暂无生命周期事件。</p>}
      </section>

      <DialogActions><Button variant="secondary" onClick={onClose}>关闭</Button></DialogActions>
    </Dialog>
  );
}
