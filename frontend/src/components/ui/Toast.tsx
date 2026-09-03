import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "./cn";

/**
 * 写操作反馈。
 *
 * **自建而不引第三方。** 需求是「一句话 + 自动消失」；第三方 toast 库带来的是动画系统、
 * 位置策略、队列管理、promise 集成，全部用不上。
 *
 * **成功与失败的 ARIA 角色不同，这不是细节。** 成功用 `role="status"`（礼貌播报，
 * 不打断当前朗读），失败用 `role="alert"`（立刻打断）。失败还不自动消失——错误信息
 * 自动消失等于没说过，用户可能正在别处看，回头什么都没有。
 */
type ToastItem = { id: number; message: string; tone: "success" | "error" };

const ToastContext = createContext<{ success: (message: string) => void; error: (message: string) => void } | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const remove = useCallback((id: number) => setItems((current) => current.filter((item) => item.id !== id)), []);

  // 收集未触发的定时器。Provider 卸载后它们仍会跑，对着已卸载的组件调 setItems——
  // React 18+ 不再警告，所以这个泄漏不会自己暴露出来，只能靠显式清理。
  const timers = useRef(new Set<number>());

  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of pending) window.clearTimeout(timer);
      pending.clear();
    };
  }, []);

  const push = useCallback(
    (message: string, tone: ToastItem["tone"]) => {
      // Date.now() 在同一毫秒内可能重复，用递增计数器保证 key 唯一。
      const id = nextId++;
      setItems((current) => [...current, { id, message, tone }]);
      if (tone === "success") {
        const timer = window.setTimeout(() => {
          timers.current.delete(timer);
          remove(id);
        }, 4000);
        timers.current.add(timer);
      }
    },
    [remove],
  );

  const value = useMemo(
    () => ({
      success: (message: string) => push(message, "success"),
      error: (message: string) => push(message, "error"),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/*
        aria-live 挂在这个常驻容器上，而不是只挂在按需渲染的 item 上。
        aria-hidden 库的 hideOthers() 在 Dialog 打开的那一刻对 DOM 做一次性快照
        （node_modules/aria-hidden/dist/es2015/index.js:133），之后才插入的元素
        不会被追认。容器在没有任何 toast 时也一直存在于 DOM 中，所以无论 hideOthers()
        何时被调用都能命中它，#root 就不会被整体 aria-hidden。

        **2026-09-03 复核完所有调用分支，常驻容器方案在全部分支下都成立。**
        radix-ui@1.6.7 下 import "aria-hidden" 的只有四个包：react-dialog、
        react-popover、react-menu、react-select。当前常驻 Toast 容器仍需兼容这些
        overlay 组件，避免后续重新引入时出现可访问性回归。
        react-dialog 的 hideOthers 只出现在一处——DialogContentModal 的
        `useEffect(() => { if (content) return hideOthers(content) }, [])`
        （dist/index.mjs:145），依赖数组是空的，所以每次打开只调一次、关闭时执行
        返回的 undo。非 modal 走 DialogContentNonModal，完全不调。
        ui/Dialog 的 Root 不传 modal（默认 true），因此恒走 modal 那一支。
        结论：不存在「hideOthers 被调用时容器不在 DOM 里」的分支。
      */}
      <div aria-live="polite" className="fixed bottom-5 right-5 z-50 grid gap-2">
        {items.map((item) => (
          <div
            key={item.id}
            role={item.tone === "success" ? "status" : "alert"}
            aria-live={item.tone === "success" ? "polite" : "assertive"}
            className={cn(
              "flex max-w-96 items-start gap-3 rounded-md border px-3 py-2 text-md shadow-pop",
              item.tone === "success"
                ? "border-success/25 bg-success-subtle text-success"
                : "border-danger/25 bg-danger-subtle text-danger-text",
            )}
          >
            <span className="flex-1">{item.message}</span>
            <button
              type="button"
              aria-label="关闭提示"
              onClick={() => remove(item.id)}
              // preflight 未启用，UA 的默认按钮边框还在。
              className="border-0 bg-transparent p-0.5 text-current opacity-70 hover:opacity-100"
            >
              <X size={13} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

let nextId = 1;

export function useToast() {
  const value = useContext(ToastContext);
  // 静默无效意味着「写操作成功了但没提示」这种 bug 只能靠人眼发现。
  if (!value) throw new Error("useToast 必须在 ToastProvider 之内使用");
  return value;
}
