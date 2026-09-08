import { useEffect, useMemo, useState } from "react";
import type {
  IndexVersionCandidatePreview,
  IndexVersionCreationContext,
  IndexVersionCreationReason,
} from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Dialog, DialogActions } from "./ui/Dialog";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input, Textarea } from "./ui/Input";
import { Select } from "./ui/Select";

const STEPS = ["创建原因", "配置差异", "文档范围", "影响确认", "提交构建"] as const;
const REASONS: Array<{ value: IndexVersionCreationReason; label: string; help: string }> = [
  { value: "initial_build", label: "创建首个索引版本", help: "知识库尚无生效版本。" },
  { value: "config_changed", label: "配置已变更", help: "Parser、Chunking 或 Embedding 配置变化。" },
  { value: "document_snapshot_changed", label: "文档集合已变化", help: "按当前资料集合创建新的可回滚版本。" },
  { value: "component_upgraded", label: "索引组件已升级", help: "Vector、Keyword、Metadata、ACL 或 Citation 结构升级。" },
  { value: "consistency_repair", label: "索引一致性修复", help: "怀疑索引缺失、重复或物理结构损坏。" },
  { value: "manual_rebuild", label: "主动创建回滚版本", help: "配置未变，但需要新的候选与回滚点。" },
];

function initialReason(context: IndexVersionCreationContext): IndexVersionCreationReason {
  if (context.scenario === "initial_build") return "initial_build";
  if (context.config_changed) return "config_changed";
  if (context.document_changed) return "document_snapshot_changed";
  return "consistency_repair";
}

function display(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function IndexVersionCreationWizard({
  open,
  context,
  busy,
  onClose,
  onPreview,
  onCreate,
}: {
  open: boolean;
  context: IndexVersionCreationContext;
  busy: boolean;
  onClose: () => void;
  onPreview: (payload: {
    reason: IndexVersionCreationReason;
    chunk_size: number;
    chunk_overlap: number;
    force: boolean;
    force_reason: string | null;
  }) => Promise<IndexVersionCandidatePreview>;
  onCreate: (preview: IndexVersionCandidatePreview) => Promise<void>;
}) {
  const [step, setStep] = useState(0);
  const [reason, setReason] = useState<IndexVersionCreationReason>(() => initialReason(context));
  const [chunkSize, setChunkSize] = useState(context.definition.chunking.chunk_size);
  const [chunkOverlap, setChunkOverlap] = useState(context.definition.chunking.chunk_overlap);
  const [forceReason, setForceReason] = useState("");
  const [preview, setPreview] = useState<IndexVersionCandidatePreview | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setStep(0);
    setReason(initialReason(context));
    setChunkSize(context.definition.chunking.chunk_size);
    setChunkOverlap(context.definition.chunking.chunk_overlap);
    setForceReason("");
    setPreview(null);
    setError("");
  }, [context, open]);

  const forced = reason === "consistency_repair" || reason === "manual_rebuild";
  const selectedReason = REASONS.find((item) => item.value === reason);
  const hardBlocks = useMemo(
    () => context.blocked_reasons.filter((item) => !item.includes("配置与文档集合均未变化")),
    [context.blocked_reasons],
  );

  const generatePreview = async () => {
    setError("");
    if (chunkOverlap >= chunkSize) {
      setError("Chunk Overlap 必须小于 Chunk Size。");
      return;
    }
    if (forced && !forceReason.trim()) {
      setError("修复性或主动重建必须填写业务原因。");
      return;
    }
    try {
      const result = await onPreview({
        reason,
        chunk_size: chunkSize,
        chunk_overlap: chunkOverlap,
        force: forced,
        force_reason: forced ? forceReason.trim() : null,
      });
      setPreview(result);
      if (result.creation_allowed) setStep(2);
      else setError(result.blocked_reasons.join("；"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法生成索引版本预览。");
    }
  };

  const submitPreview = async () => {
    if (!preview) return;
    setError("");
    try { await onCreate(preview); }
    catch (cause) {
      setError(cause instanceof Error ? cause.message : "索引版本创建失败。");
    }
  };

  return (
    <Dialog
      open={open}
      size="lg"
      title="创建索引版本"
      description="确认前不创建数据库记录；提交后直接进入 building（构建中）"
      onClose={onClose}
    >
      <ol className="mb-5 grid grid-cols-5 gap-2 p-0 max-md:grid-cols-1" aria-label="创建索引版本步骤">
        {STEPS.map((label, index) => (
          <li
            key={label}
            className={`list-none rounded-md border px-2 py-2 text-sm ${index === step ? "border-brand bg-brand-subtle text-brand" : index < step ? "border-success/30 bg-success-subtle text-success" : "border-line text-ink-faint"}`}
          >
            {index + 1}. {label}
          </li>
        ))}
      </ol>

      {error ? <ErrorBanner>{error}</ErrorBanner> : null}

      {step === 0 ? (
        <section className="grid gap-4">
          <label className="grid gap-2 text-sm text-ink-muted">
            创建原因
            <Select
              aria-label="创建原因"
              value={reason}
              onChange={(event) => { setReason(event.target.value as IndexVersionCreationReason); setPreview(null); setError(""); }}
            >
              {REASONS.filter((item) => context.scenario === "initial_build" ? item.value === "initial_build" : item.value !== "initial_build").map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </Select>
          </label>
          <p className="m-0 text-sm text-ink-faint">{selectedReason?.help}</p>
          {forced ? (
            <label className="grid gap-2 text-sm text-ink-muted">
              重建原因
              <Textarea
                aria-label="重建原因"
                value={forceReason}
                maxLength={500}
                placeholder="说明一致性异常、风险或为什么需要新的回滚版本"
                onChange={(event) => setForceReason(event.target.value)}
              />
            </label>
          ) : null}
          {hardBlocks.length ? (
            <ul className="m-0 grid gap-1 pl-5 text-sm text-danger-text">
              {hardBlocks.map((item) => <li key={item}>{item}</li>)}
            </ul>
          ) : null}
        </section>
      ) : null}

      {step === 1 ? (
        <section className="grid gap-4">
          <div className="grid grid-cols-2 gap-3 max-sm:grid-cols-1">
            <label className="grid gap-2 text-sm text-ink-muted">
              Chunk Size
              <Input aria-label="Chunk Size" type="number" min={100} max={4000} value={chunkSize} onChange={(event) => { setChunkSize(Number(event.target.value)); setPreview(null); }} />
            </label>
            <label className="grid gap-2 text-sm text-ink-muted">
              Chunk Overlap
              <Input aria-label="Chunk Overlap" type="number" min={0} max={1000} value={chunkOverlap} onChange={(event) => { setChunkOverlap(Number(event.target.value)); setPreview(null); }} />
            </label>
          </div>
          <dl className="grid grid-cols-2 gap-3 text-sm max-sm:grid-cols-1">
            <div><dt className="text-ink-faint">Parser</dt><dd className="m-0 mt-1">{context.definition.parser.schema_version}</dd></div>
            <div><dt className="text-ink-faint">Embedding</dt><dd className="m-0 mt-1">{context.definition.embedding.model || "未登记"} · {context.definition.embedding.dimension ?? "—"} 维</dd></div>
          </dl>
          <div className="rounded-md border border-line bg-canvas p-3 text-sm">
            <strong className="text-ink">统一组件版本</strong>
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 max-sm:grid-cols-1">
              {Object.entries(context.definition.components).map(([key, value]) => <div key={key}><dt className="text-ink-faint">{key}</dt><dd className="m-0 break-all">{value}</dd></div>)}
            </dl>
          </div>
        </section>
      ) : null}

      {step === 2 && preview ? (
        <section className="grid gap-4">
          <div className="flex flex-wrap gap-2">
            <Badge shape="status" tone="success">纳入 {preview.document_scope.included} 份</Badge>
            <Badge shape="status" tone={preview.document_scope.excluded ? "warning" : "neutral"}>排除 {preview.document_scope.excluded} 份</Badge>
          </div>
          <dl className="grid grid-cols-4 gap-3 text-sm max-sm:grid-cols-2">
            <div><dt className="text-ink-faint">新增</dt><dd className="m-0 mt-1 text-lg font-semibold">{preview.document_diff.added}</dd></div>
            <div><dt className="text-ink-faint">移除</dt><dd className="m-0 mt-1 text-lg font-semibold">{preview.document_diff.removed}</dd></div>
            <div><dt className="text-ink-faint">更新</dt><dd className="m-0 mt-1 text-lg font-semibold">{preview.document_diff.updated}</dd></div>
            <div><dt className="text-ink-faint">未变化</dt><dd className="m-0 mt-1 text-lg font-semibold">{preview.document_diff.unchanged}</dd></div>
          </dl>
          {preview.document_scope.excluded ? (
            <section className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm">
              <strong className="text-warning">排除原因</strong>
              <p className="mb-2 mt-1 text-ink-muted">解析失败 {preview.document_scope.parse_failed} 份 · 无可用 current revision {preview.document_scope.missing_current_revision} 份</p>
              <ul className="m-0 grid max-h-32 gap-1 overflow-y-auto pl-5 text-ink-muted">
                {preview.document_exclusions.map((item) => (
                  <li key={item.document_id}>{item.filename} · {item.reason === "parse_failed" ? `解析失败${item.parse_failure_code ? `（${item.parse_failure_code}）` : ""}` : "无可用 current revision"}</li>
                ))}
              </ul>
            </section>
          ) : null}
          <p className="m-0 break-all text-sm text-ink-faint">文档集合指纹：{preview.document_set_fingerprint}</p>
        </section>
      ) : null}

      {step === 3 && preview ? (
        <section className="grid gap-4 text-sm">
          <p className="m-0 text-base font-medium text-ink">预计处理 {preview.estimated_documents} 份资料</p>
          <p className="m-0 text-ink-muted">约 {preview.estimated_chunks.toLocaleString("zh-CN")} 个 Chunks · {preview.estimated_embedding_units.toLocaleString("zh-CN")} 字节输入规模（估算，不含模型单价）</p>
          <p className="m-0 text-ink-muted">全局构建容量：{preview.build_capacity.active_builds}/{preview.build_capacity.max_concurrent_builds} 使用中 · 单次最多 {preview.build_capacity.max_documents.toLocaleString("zh-CN")} 份资料</p>
          <p className="m-0 text-ink-muted">构建产出的是候选版本，不影响当前线上检索；三层发布验证通过后仍需手动激活。</p>
          <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-warning">
            本次构建使用的是当前这一刻的资料快照。之后的数据同步仍会继续更新线上索引，所以回滚到这个版本时要核对它对应的内容时间点。
          </div>
          {preview.config_diff.length ? (
            <ul className="m-0 grid gap-2 p-0">
              {preview.config_diff.map((item) => <li key={item.field} className="list-none rounded border border-line p-2"><strong>{item.field}</strong><span className="ml-2 text-ink-faint">{display(item.active)} → {display(item.candidate)}</span></li>)}
            </ul>
          ) : <p className="m-0 text-ink-faint">配置无差异，本次属于有原因的修复性/主动重建。</p>}
        </section>
      ) : null}

      {step === 4 && preview ? (
        <section className="grid gap-3 text-sm">
          <div><span className="text-ink-faint">创建原因</span><p className="m-0 mt-1">{selectedReason?.label}</p></div>
          <div><span className="text-ink-faint">配置指纹</span><p className="m-0 mt-1 break-all">{preview.config_fingerprint || "不可用"}</p></div>
          <div><span className="text-ink-faint">发布指纹</span><p className="m-0 mt-1 break-all">{preview.release_fingerprint || "不可用"}</p></div>
          <div><span className="text-ink-faint">处理范围</span><p className="m-0 mt-1">{preview.estimated_documents} 份资料；{preview.document_scope.excluded} 份排除</p></div>
        </section>
      ) : null}

      <DialogActions>
        <Button variant="secondary" loading={busy} onClick={onClose}>取消</Button>
        {step > 0 ? <Button variant="secondary" loading={busy} onClick={() => setStep((value) => value - 1)}>上一步</Button> : null}
        {step === 0 ? <Button blockedReason={hardBlocks} onClick={() => setStep(1)}>下一步</Button> : null}
        {step === 1 ? <Button loading={busy} onClick={() => void generatePreview()}>生成预览</Button> : null}
        {step === 2 || step === 3 ? <Button onClick={() => setStep((value) => value + 1)}>下一步</Button> : null}
        {step === 4 && preview ? <Button loading={busy} blockedReason={preview.creation_allowed ? undefined : preview.blocked_reasons} onClick={() => void submitPreview()}>创建并开始构建</Button> : null}
      </DialogActions>
    </Dialog>
  );
}
