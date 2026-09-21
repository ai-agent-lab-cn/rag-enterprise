import { useMemo, useRef, useState } from "react";
import type {
  IndexVersionBuildResult,
  IndexVersionCandidatePreview,
  IndexVersionCreationContext,
  IndexVersionCreationReason,
} from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Checkbox } from "./ui/Checkbox";
import { Dialog, DialogActions } from "./ui/Dialog";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input, Textarea } from "./ui/Input";
import { Select } from "./ui/Select";

const STEPS = ["创建场景", "目标配置", "文档快照", "变更与影响", "确认并构建"] as const;
const REASONS: Array<{ value: IndexVersionCreationReason; label: string; help: string }> = [
  { value: "initial_build", label: "创建首个索引版本", help: "知识库尚无生效版本。" },
  { value: "config_changed", label: "配置已变更", help: "Parser、Chunking 或 Embedding 配置变化。" },
  { value: "document_snapshot_changed", label: "文档集合已变化", help: "按当前资料集合创建新的可回滚版本。" },
  { value: "component_upgraded", label: "索引组件已升级", help: "Vector、Keyword、Metadata、ACL 或 Citation 结构升级。" },
  { value: "consistency_repair", label: "索引一致性修复", help: "仅在关联真实健康检查证据后使用。" },
  { value: "manual_rebuild", label: "使用相同输入重新构建", help: "配置和文档未变化，但需要重新生成候选版本。" },
];

function initialReason(context: IndexVersionCreationContext): IndexVersionCreationReason {
  if (context.scenario === "initial_build") return "initial_build";
  if (context.component_changed) return "component_upgraded";
  if (context.config_changed) return "config_changed";
  if (context.document_changed) return "document_snapshot_changed";
  return "manual_rebuild";
}

const CONFIG_FIELD_LABEL: Record<string, string> = {
  chunking_version: "切片策略",
  embedding_model: "向量模型",
  embedding_dimension: "向量维度",
  processing_options: "切片参数",
  parser_schema_version: "解析器结构",
  vector_index_schema_version: "Vector 索引结构",
  keyword_index_schema_version: "Keyword 索引结构",
  metadata_schema_version: "Metadata 结构",
  acl_schema_version: "ACL 结构",
  citation_schema_version: "Citation 结构",
  reranker_model: "Reranker 模型",
  component_manifest: "索引组件版本",
};

const COMPONENT_LABEL: Record<string, string> = {
  parser_schema_version: "解析器结构",
  vector_index_schema_version: "Vector 索引",
  keyword_index_schema_version: "Keyword 索引",
  metadata_schema_version: "Metadata 结构",
  acl_schema_version: "ACL 结构",
  citation_schema_version: "Citation 结构",
  reranker_model: "Reranker 模型",
};

function shortFingerprint(value: string | null) {
  return value ? `${value.slice(0, 10)}…${value.slice(-6)}` : "不可用";
}

function display(value: unknown, field?: string) {
  if (value === null || value === undefined || value === "") return "—";
  if (field === "processing_options" && typeof value === "object") {
    const options = value as Record<string, unknown>;
    return `Chunk 大小 ${options.chunk_size ?? "—"} / 重叠 ${options.chunk_overlap ?? "—"}`;
  }
  if (field === "component_manifest" && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${COMPONENT_LABEL[key] || key}：${String(item)}`)
      .join("；");
  }
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
  onCreate: (preview: IndexVersionCandidatePreview, excludedDocumentsAcknowledged: boolean) => Promise<IndexVersionBuildResult>;
}) {
  const [step, setStep] = useState(0);
  const [reason, setReason] = useState<IndexVersionCreationReason>(() => initialReason(context));
  const [chunkSize, setChunkSize] = useState(context.definition.chunking.chunk_size);
  const [chunkOverlap, setChunkOverlap] = useState(context.definition.chunking.chunk_overlap);
  const [forceReason, setForceReason] = useState("");
  const [preview, setPreview] = useState<IndexVersionCandidatePreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [excludedDocumentsAcknowledged, setExcludedDocumentsAcknowledged] = useState(false);
  const [needsRepreview, setNeedsRepreview] = useState(false);
  const [error, setError] = useState("");
  const previewRequest = useRef(0);

  const forced = reason === "consistency_repair" || reason === "manual_rebuild";
  const selectedReason = REASONS.find((item) => item.value === reason);
  const hardBlocks = useMemo(
    () => context.blocked_reasons.filter((item) => !item.includes("配置与文档集合均未变化")),
    [context.blocked_reasons],
  );

  const generatePreview = async () => {
    setError("");
    if (!Number.isInteger(chunkSize) || chunkSize < 100 || chunkSize > 4000) {
      setError("Chunk 大小必须是 100–4000 之间的整数。");
      return;
    }
    if (!Number.isInteger(chunkOverlap) || chunkOverlap < 0 || chunkOverlap > 1000) {
      setError("重叠长度必须是 0–1000 之间的整数。");
      return;
    }
    if (chunkOverlap >= chunkSize) {
      setError("重叠长度必须小于 Chunk 大小。");
      return;
    }
    if (forced && !forceReason.trim()) {
      setError("修复性或主动重建必须填写业务原因。");
      return;
    }
    const requestId = ++previewRequest.current;
    setPreviewBusy(true);
    try {
      const result = await onPreview({
        reason,
        chunk_size: chunkSize,
        chunk_overlap: chunkOverlap,
        force: forced,
        force_reason: forced ? forceReason.trim() : null,
      });
      if (requestId !== previewRequest.current) return;
      setPreview(result);
      setNeedsRepreview(false);
      setExcludedDocumentsAcknowledged(false);
      setStep(2);
      if (!result.creation_allowed) setError(result.blocked_reasons.join("；"));
    } catch (cause) {
      if (requestId === previewRequest.current) {
        setError(cause instanceof Error ? cause.message : "无法生成索引版本预览。");
      }
    } finally {
      if (requestId === previewRequest.current) setPreviewBusy(false);
    }
  };

  const submitPreview = async () => {
    if (!preview) return;
    setError("");
    try { await onCreate(preview, excludedDocumentsAcknowledged); }
    catch (cause) {
      setNeedsRepreview(true);
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
      <ol className="mb-5 grid grid-cols-5 gap-2 overflow-x-auto p-0 max-md:flex" aria-label="创建索引版本步骤">
        {STEPS.map((label, index) => (
          <li
            key={label}
            aria-current={index === step ? "step" : undefined}
            className={`list-none rounded-md border px-2 py-2 text-sm max-md:min-w-28 ${index === step ? "border-brand bg-brand-subtle text-brand" : index < step ? "border-success/30 bg-success-subtle text-success" : "border-line text-ink-faint"}`}
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
              onChange={(event) => { previewRequest.current += 1; setReason(event.target.value as IndexVersionCreationReason); setPreview(null); setError(""); }}
            >
              {REASONS.filter((item) => context.scenario === "initial_build"
                ? item.value === "initial_build"
                : item.value !== "initial_build" && item.value !== "consistency_repair"
                  && ((item.value === "manual_rebuild" && !context.config_changed && !context.component_changed && !context.document_changed)
                    || (item.value === "config_changed" && context.config_changed)
                    || (item.value === "component_upgraded" && context.component_changed)
                    || (item.value === "document_snapshot_changed" && context.document_changed))).map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </Select>
          </label>
          <p className="m-0 text-sm text-ink-faint">{selectedReason?.help}</p>
          <div className="flex flex-wrap gap-2" aria-label="系统检测结果">
            <Badge shape="status" tone={context.config_changed ? "warning" : "neutral"}>配置{context.config_changed ? "有变化" : "无变化"}</Badge>
            <Badge shape="status" tone={context.component_changed ? "warning" : "neutral"}>组件{context.component_changed ? "有升级" : "无变化"}</Badge>
            <Badge shape="status" tone={context.document_changed ? "warning" : "neutral"}>文档集合{context.document_changed ? "有变化" : "无变化"}</Badge>
          </div>
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
              Chunk 大小
              <Input aria-label="Chunk 大小" type="number" min={100} max={4000} value={chunkSize} onChange={(event) => { previewRequest.current += 1; setChunkSize(Number(event.target.value)); setPreview(null); }} />
            </label>
            <label className="grid gap-2 text-sm text-ink-muted">
              重叠长度
              <Input aria-label="重叠长度" type="number" min={0} max={1000} value={chunkOverlap} onChange={(event) => { previewRequest.current += 1; setChunkOverlap(Number(event.target.value)); setPreview(null); }} />
            </label>
          </div>
          <dl className="grid grid-cols-2 gap-3 text-sm max-sm:grid-cols-1">
            <div><dt className="text-ink-faint">解析器结构版本</dt><dd className="m-0 mt-1">{context.definition.parser.schema_version}</dd></div>
            <div><dt className="text-ink-faint">实际解析器版本</dt><dd className="m-0 mt-1 break-all" title={context.definition.parser.runtime_versions.join("、")}>{context.definition.parser.runtime_versions.join("、") || "暂无"}</dd></div>
            <div><dt className="text-ink-faint">Embedding 模型</dt><dd className="m-0 mt-1 break-all" title={context.definition.embedding.model || "未登记"}>{context.definition.embedding.model || "未登记"}</dd></div>
            <div><dt className="text-ink-faint">向量维度</dt><dd className="m-0 mt-1">{context.definition.embedding.dimension ?? "—"} 维</dd></div>
          </dl>
          <div className="rounded-md border border-line bg-canvas p-3 text-sm">
            <strong className="text-ink">索引结构版本</strong>
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 max-sm:grid-cols-1">
              {Object.entries(context.definition.components).filter(([key]) => key !== "parser_schema_version").map(([key, value]) => <div key={key}><dt className="text-ink-faint">{COMPONENT_LABEL[key] || key}</dt><dd className="m-0 break-all" title={value}>{value}</dd></div>)}
            </dl>
          </div>
          <p className="m-0 text-sm text-ink-faint" title={context.definition.capabilities.filter((item) => !item.editable).map((item) => `${item.field}：${item.reason || item.source || "系统决定"}`).join("；")}>
            当前仅 Chunk 大小和重叠长度可编辑，创建时保存为该知识库的 Index Definition；其余配置由解析器注册表、向量索引设置或运行组件决定。
          </p>
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
          <details className="rounded-md border border-line p-3 text-sm">
            <summary className="cursor-pointer font-medium text-ink">查看纳入资料（{preview.document_inclusions.length}）</summary>
            <ul className="mb-0 mt-2 grid max-h-36 gap-1 overflow-y-auto pl-5 text-ink-muted">
              {preview.document_inclusions.map((item) => <li key={item.document_id} title={item.filename}>{item.filename}</li>)}
            </ul>
          </details>
          {preview.document_scope.excluded ? (
            <section className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm">
              <strong className="text-warning">排除原因</strong>
              <p className="mb-2 mt-1 text-ink-muted">解析失败 {preview.document_scope.parse_failed} 份 · 无可用 current revision {preview.document_scope.missing_current_revision} 份</p>
              <ul className="m-0 grid max-h-32 gap-1 overflow-y-auto pl-5 text-ink-muted">
                {preview.document_exclusions.map((item) => (
                  <li key={item.document_id}>{item.filename} · {item.reason === "parse_failed" ? `解析失败${item.parse_failure_code ? `（${item.parse_failure_code}）` : ""}` : "无可用 current revision"}</li>
                ))}
              </ul>
              <div className="mt-3 border-t border-warning/20 pt-3">
                <Checkbox showLabel label="我确认本版本不会包含以上资料" checked={excludedDocumentsAcknowledged} onCheckedChange={setExcludedDocumentsAcknowledged}/>
              </div>
            </section>
          ) : null}
          {preview.missing_source_documents?.length ? <section className="rounded-md border border-danger/30 bg-danger/10 p-3 text-sm text-danger-text"><strong>源文件丢失</strong><p className="mb-2 mt-1">请重新上传同一文件以恢复源文件，或删除失效资料后重新生成预览。</p><ul className="m-0 pl-5">{preview.missing_source_documents.map((item) => <li key={item.document_id}>{item.filename}</li>)}</ul></section> : null}
          <p className="m-0 text-sm text-ink-faint" title={preview.document_set_fingerprint}>文档集合指纹：{shortFingerprint(preview.document_set_fingerprint)}</p>
        </section>
      ) : null}

      {step === 3 && preview ? (
        <section className="grid gap-4 text-sm">
          <p className="m-0 text-base font-medium text-ink">预计处理 {preview.estimated_documents} 份资料</p>
          <p className="m-0 text-ink-muted">粗略约 {preview.estimated_chunks.toLocaleString("zh-CN")} 个 Chunks · 源文件共 {preview.estimated_embedding_units.toLocaleString("zh-CN")} 字节（不是 Token 或计费用量）</p>
          <p className="m-0 text-ink-muted">预览时全局构建容量：{preview.build_capacity.active_builds}/{preview.build_capacity.max_concurrent_builds} 使用中 · 单次最多 {preview.build_capacity.max_documents.toLocaleString("zh-CN")} 份资料；提交时会重新校验。</p>
          <p className="m-0 text-ink-muted">构建产出的是候选版本，不影响当前线上检索；三层发布验证通过后仍需手动激活。</p>
          <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-warning">
            本次候选版本按当前文档集合构建。回滚历史版本前仍需核对目标版本与当前内容的差异。
          </div>
          {preview.config_diff.length ? (
            <ul className="m-0 grid gap-2 p-0">
              {preview.config_diff.map((item) => <li key={item.field} className="list-none rounded border border-line p-2"><strong>{CONFIG_FIELD_LABEL[item.field] || item.field}</strong><span className="ml-2 break-all text-ink-faint">{display(item.active, item.field)} → {display(item.candidate, item.field)}</span></li>)}
            </ul>
          ) : <p className="m-0 text-ink-faint">配置无差异，本次属于有原因的修复性/主动重建。</p>}
        </section>
      ) : null}

      {step === 4 && preview ? (
        <section className="grid gap-3 text-sm">
          <div><span className="text-ink-faint">创建原因</span><p className="m-0 mt-1">{selectedReason?.label}</p></div>
          {preview.force_reason ? <div><span className="text-ink-faint">业务原因</span><p className="m-0 mt-1">{preview.force_reason}</p></div> : null}
          <div><span className="text-ink-faint">目标配置</span><p className="m-0 mt-1">Chunk {preview.definition.chunking.chunk_size} / 重叠 {preview.definition.chunking.chunk_overlap} · {preview.definition.embedding.model || "未登记"}</p></div>
          <div><span className="text-ink-faint">配置差异</span><p className="m-0 mt-1">{preview.config_diff.length} 项</p></div>
          <div><span className="text-ink-faint">配置指纹</span><p className="m-0 mt-1" title={preview.config_fingerprint || undefined}>{shortFingerprint(preview.config_fingerprint)}</p></div>
          <div><span className="text-ink-faint">文档集合指纹</span><p className="m-0 mt-1" title={preview.document_set_fingerprint}>{shortFingerprint(preview.document_set_fingerprint)}</p></div>
          <div><span className="text-ink-faint">发布指纹</span><p className="m-0 mt-1" title={preview.release_fingerprint || undefined}>{shortFingerprint(preview.release_fingerprint)}</p></div>
          <div><span className="text-ink-faint">处理范围</span><p className="m-0 mt-1">{preview.estimated_documents} 份资料；{preview.document_scope.excluded} 份排除</p></div>
          {preview.document_scope.excluded ? <div><span className="text-ink-faint">排除确认</span><p className="m-0 mt-1 text-warning">已确认本版本不包含 {preview.document_scope.excluded} 份资料</p></div> : null}
          <p className="m-0 rounded-md border border-line bg-canvas p-3 text-ink-muted">创建后进入 building（构建中）；构建完成仍需正式评测、三层验证和手动激活，不会自动影响线上版本。</p>
        </section>
      ) : null}

      <DialogActions>
        <Button variant="secondary" loading={busy || previewBusy} onClick={onClose}>取消</Button>
        {step > 0 ? <Button variant="secondary" loading={busy || previewBusy} onClick={() => setStep((value) => value - 1)}>上一步</Button> : null}
        {step === 0 ? <Button blockedReason={hardBlocks} onClick={() => setStep(1)}>下一步</Button> : null}
        {step === 1 ? <Button loading={busy || previewBusy} onClick={() => void generatePreview()}>生成预览</Button> : null}
        {step === 2 ? <Button blockedReason={[...(!preview?.creation_allowed ? preview?.blocked_reasons || [] : []), ...(preview?.document_scope.excluded && !excludedDocumentsAcknowledged ? ["请确认排除资料范围"] : [])]} onClick={() => setStep(3)}>下一步</Button> : null}
        {step === 3 ? <Button onClick={() => setStep(4)}>下一步</Button> : null}
        {step >= 2 && preview && (!preview.creation_allowed || needsRepreview) ? <Button variant="secondary" loading={previewBusy} onClick={() => { setStep(1); setError(""); setNeedsRepreview(false); }}>重新生成预览</Button> : null}
        {step === 4 && preview ? <Button loading={busy} blockedReason={[...(preview.creation_allowed ? [] : preview.blocked_reasons), ...(preview.document_scope.excluded && !excludedDocumentsAcknowledged ? ["请确认排除资料范围"] : []), ...(needsRepreview ? ["预览可能已过期，请重新生成"] : [])]} onClick={() => void submitPreview()}>创建并开始构建</Button> : null}
      </DialogActions>
    </Dialog>
  );
}
