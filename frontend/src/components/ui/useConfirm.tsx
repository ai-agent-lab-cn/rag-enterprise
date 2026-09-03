import { useCallback, useState } from "react";
import { Button } from "./Button";
import { Dialog, DialogActions } from "./Dialog";

/**
 * 统一确认弹层。
 *
 * **`consequence` 是必填的。** 现在有 5 处各写各的确认弹层，文案质量参差：知识库删除
 * 写清了「会连带删除全部资料、索引与会话」，成员停用只说「会话可能随之失效」。
 * 做成必填字段，抄的时候不可能漏。
 *
 * **确认按钮不 autoFocus。** DocumentPanel 现在给它加了，弹层一开回车就删——
 * 破坏性操作不该是一个回车的距离。
 */
export type ConfirmRequest = {
  title: string;
  /** 后果描述。必填：用户要知道点下去会发生什么，而不只是「确认吗」。 */
  consequence: string;
  confirmLabel: string;
  tone?: "default" | "destructive";
  /**
   * reject 时本 hook 保证：弹层保持打开（用户能重试或取消）、按钮解锁、
   * rejection 不外泄成未处理异常，并在弹层内展示错误消息（`role="alert"`）。
   * 调用方仍可以在 `onConfirm` 内部另行用 `useToast` 补充提示，但弹层内的反馈
   * 不再依赖调用方——用户此刻的视线在弹层上，不该只有右下角 toast 一处反馈。
   */
  onConfirm: () => Promise<void>;
};

export function useConfirm() {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const confirm = useCallback((next: ConfirmRequest) => {
    setError("");
    setRequest(next);
  }, []);

  const close = useCallback(() => {
    if (!busy) setRequest(null);
  }, [busy]);

  const run = useCallback(async () => {
    if (!request) return;
    setError("");
    setBusy(true);
    try {
      await request.onConfirm();
      setRequest(null);
    } catch (reason) {
      // 失败时保留弹层（用户能重试或取消）并把 rejection 咽掉，不外泄成未处理异常，
      // 同时在弹层内展示错误——用户此刻的视线在这里，不能只靠右下角的 toast。
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }, [request]);

  const dialog = request ? (
    <Dialog open title={request.title} onClose={close}>
      <p className="text-md text-ink-muted">{request.consequence}</p>
      {error ? (
        <p role="alert" className="mt-2 rounded-md border border-danger/25 bg-danger-subtle px-3 py-2 text-md text-danger-text">
          {error}
        </p>
      ) : null}
      <DialogActions>
        <Button variant="secondary" loading={busy} onClick={close}>
          取消
        </Button>
        <Button
          variant={request.tone === "destructive" ? "destructive" : "primary"}
          loading={busy}
          onClick={() => void run()}
        >
          {request.confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  ) : null;

  return { confirm, dialog };
}
