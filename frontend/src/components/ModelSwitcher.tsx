import { useCallback, useEffect, useState } from "react";
import { Bot, Circle, RefreshCw } from "lucide-react";
import { api } from "../api";
import type {
  GenerationModelItem,
  GenerationModels,
  GenerationModelStatus,
  GenerationProvider,
} from "../types";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Dialog, DialogActions } from "./ui/Dialog";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Tooltip } from "./ui/Tooltip";

const STATUS_LABEL: Record<GenerationModelStatus, string> = {
  available: "运行中",
  unconfigured: "未配置",
  region_unsupported: "地区不可用",
  quota_exhausted: "额度不足",
  auth_failed: "凭据无效",
  rate_limited: "请求受限",
  timeout: "响应超时",
  model_not_found: "模型不可用",
  unavailable: "服务异常",
};

const STATUS_TONE: Record<GenerationModelStatus, "success" | "neutral" | "warning" | "danger"> = {
  available: "success",
  unconfigured: "neutral",
  region_unsupported: "warning",
  quota_exhausted: "warning",
  auth_failed: "danger",
  rate_limited: "warning",
  timeout: "warning",
  model_not_found: "danger",
  unavailable: "danger",
};

export function ModelSwitcher() {
  const [data, setData] = useState<GenerationModels | null>(null);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<GenerationProvider>("deepseek");
  const [busy, setBusy] = useState<"check" | "switch" | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const next = await api.listGenerationModels();
      setData(next);
      setSelected(next.active_provider);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "模型状态读取失败");
    }
  }, []);

  useEffect(() => {
    void load();
    const reload = () => void load();
    window.addEventListener("rag-generation-status-changed", reload);
    return () => window.removeEventListener("rag-generation-status-changed", reload);
  }, [load]);

  const active = data?.items.find((item) => item.active) ?? null;
  const selectedItem = data?.items.find((item) => item.provider === selected) ?? null;
  const openDialog = () => {
    setError("");
    setSelected(data?.active_provider ?? "deepseek");
    setOpen(true);
  };
  const check = async () => {
    setBusy("check");
    setError("");
    try {
      const checked = await api.checkGenerationModel(selected);
      setData((current) => current ? {
        ...current,
        items: current.items.map((item) => item.provider === checked.provider ? checked : item),
      } : current);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "模型检测失败");
      await load();
    } finally {
      setBusy(null);
    }
  };
  const activate = async () => {
    setBusy("switch");
    setError("");
    try {
      const next = await api.activateGenerationModel(selected);
      setData(next);
      setOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "模型切换失败");
      await load();
    } finally {
      setBusy(null);
    }
  };

  const compactLabel = active
    ? `${active.display_name} · ${active.model_name} · ${STATUS_LABEL[active.status]}`
    : error || "正在读取模型状态";

  return (
    <div className="shrink-0 p-1.5 max-[768px]:border-t-0 max-[768px]:p-1">
      <div className="hidden rounded-lg border border-line bg-canvas p-2 min-[1181px]:grid min-[1181px]:gap-1.5">
        <span className="text-[10px] font-medium text-ink-faint">当前模型</span>
        <strong className="overflow-hidden text-ellipsis whitespace-nowrap text-sm text-ink" title={active?.model_name}>
          {active?.model_name ?? (error ? "读取失败" : "读取中…")}
        </strong>
        {active ? (
          <Badge tone={STATUS_TONE[active.status]} shape="status" className="w-fit">
            <Circle size={6} fill="currentColor" /> {STATUS_LABEL[active.status]}
          </Badge>
        ) : null}
        <Button variant="secondary" size="sm" className="mt-0.5 w-full px-1" onClick={openDialog}>
          <RefreshCw size={13} /> 切换模型
        </Button>
      </div>

      <Tooltip content={compactLabel} side="right">
        <button
          type="button"
          className="relative mx-auto grid h-10 w-10 place-items-center rounded-md border-0 bg-transparent text-ink-muted hover:bg-brand-subtle hover:text-brand min-[1181px]:hidden"
          onClick={openDialog}
          aria-label={`切换模型：${compactLabel}`}
        >
          <Bot size={18} />
          {active ? (
            <span className={`absolute right-1 top-1 h-2 w-2 rounded-full ${active.status === "available" ? "bg-success" : active.status === "unconfigured" ? "bg-ink-faint" : "bg-warning"}`} />
          ) : null}
        </button>
      </Tooltip>

      <Dialog
        open={open}
        title="切换模型"
        description="检测成功后才会全局切换；失败不会影响当前模型。"
        size="md"
        onClose={() => { if (!busy) setOpen(false); }}
      >
        {error ? <ErrorBanner>{error}</ErrorBanner> : null}
        <div className="grid gap-2">
          {data?.items.map((item) => (
            <ModelOption
              key={item.provider}
              item={item}
              selected={selected === item.provider}
              onSelect={() => setSelected(item.provider)}
            />
          )) ?? <div className="h-32 animate-pulse rounded-md bg-canvas" role="status" aria-label="正在读取模型列表" />}
        </div>
        {selectedItem?.status_message && selectedItem.status !== "available" ? (
          <p className="mb-0 mt-3 text-sm text-ink-muted">{selectedItem.status_message}</p>
        ) : null}
        <DialogActions>
          <Button variant="secondary" onClick={() => setOpen(false)} blockedReason={busy ? "模型检测处理中" : undefined}>取消</Button>
          <Button variant="outline" onClick={() => void check()} loading={busy === "check"} blockedReason={!selectedItem?.configured ? "该模型未配置 API Key" : busy === "switch" ? "正在切换模型" : undefined}>
            重新检测
          </Button>
          <Button onClick={() => void activate()} loading={busy === "switch"} blockedReason={!selectedItem?.configured ? "该模型未配置 API Key" : busy === "check" ? "正在检测模型" : undefined}>
            检测并切换
          </Button>
        </DialogActions>
      </Dialog>
    </div>
  );
}

function ModelOption({ item, selected, onSelect }: { item: GenerationModelItem; selected: boolean; onSelect: () => void }) {
  const balanceLabel = item.balance_status === "available" && item.balance_amount !== null
    ? `${item.balance_currency === "CNY" ? "¥" : `${item.balance_currency ?? ""} `}${item.balance_amount.toFixed(2)}`
    : item.balance_status === "unsupported"
      ? "需前往 AI Studio 查看"
      : item.balance_status === "error"
        ? "余额读取失败"
        : "检测后更新";
  return (
    <label className={`grid cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-md border p-3 ${selected ? "border-brand bg-brand-subtle" : "border-line bg-surface hover:bg-canvas"}`}>
      <input type="radio" name="generation-provider" value={item.provider} checked={selected} onChange={onSelect} />
      <span className="min-w-0">
        <strong className="block text-md text-ink">{item.display_name}</strong>
        <small className="block overflow-hidden text-ellipsis whitespace-nowrap text-ink-faint" title={item.model_name}>{item.model_name}</small>
        {item.checked_at ? <small className="block text-ink-faint">检测于 {new Date(item.checked_at).toLocaleString("zh-CN")}</small> : null}
        <span className="mt-2 block">
          <small className="flex items-center justify-between gap-2 text-ink-muted">
            <span>剩余额度</span>
            <span>{balanceLabel}</span>
          </small>
          {item.balance_percent !== null ? (
            <span
              className="mt-1 block h-1.5 overflow-hidden rounded-full bg-line"
              role="progressbar"
              aria-label={`${item.display_name} 剩余额度`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(item.balance_percent)}
            >
              <span
                className={`block h-full rounded-full ${item.balance_percent <= 20 ? "bg-danger" : item.balance_percent <= 50 ? "bg-warning" : "bg-success"}`}
                style={{ width: `${item.balance_percent}%` }}
              />
            </span>
          ) : null}
        </span>
      </span>
      <div className="grid justify-items-end gap-1">
        {item.active ? <Badge tone="brand" shape="type">当前</Badge> : null}
        <Badge tone={STATUS_TONE[item.status]} shape="status">{STATUS_LABEL[item.status]}</Badge>
      </div>
    </label>
  );
}
