import { Dialog as RadixDialog } from "radix-ui";
import { X } from "lucide-react";
import type { ReactNode, RefObject } from "react";
import { cn } from "./cn";

/**
 * 统一弹层。
 *
 * **这是四个 P0 组件里唯一值得引入依赖的一个。** 原来的 `Modal.tsx` 只有 27 行，
 * 有 `role="dialog"`、`aria-modal`、ESC 和点遮罩关闭——看起来齐全，但**没有任何焦点
 * 管理**：焦点不进弹层、Tab 能跑到背后的页面、关闭后焦点丢失。键盘用户打开一个确认框
 * 之后就出不来了。
 *
 * 焦点陷阱要写对很难（Tab 循环、Shift+Tab、初始焦点、还原焦点、inert 背景、滚动锁定），
 * 这正是 Radix 的价值所在；相比之下 Select 换成 Radix 的收益是零，所以那边没换。
 *
 * API 保持与旧 `Modal` 接近（`title`/`description`/`onClose`/`children`），
 * 让 16 处调用点的迁移是纯替换。区别是多一个受控的 `open`。
 */
export interface DialogProps {
  open: boolean;
  title: string;
  description?: string;
  children: ReactNode;
  onClose: () => void;
  /** sm 确认类、md 表单类、lg 表格类——表格四列在 md 下会被截断。 */
  size?: "sm" | "md" | "lg";
  /** 受控弹层没有 Radix Trigger 时，显式恢复到打开弹层的控件。 */
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export function Dialog({ open, title, description, children, onClose, size = "sm", returnFocusRef }: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-50 bg-ink/35" />
        <RadixDialog.Content
          onCloseAutoFocus={(event) => {
            if (!returnFocusRef?.current) return;
            event.preventDefault();
            returnFocusRef.current.focus();
          }}
          className={cn(
            "fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2",
            "w-[calc(100vw-32px)] rounded-lg bg-surface shadow-modal",
            "focus-visible:outline-none",
            // 受控高度 + 内部滚动：此前弹层没有任何 max-height，内容超过视口时上下会被
            // 裁掉且**滚不到**——长内容弹框只能各自在 children 里再套一层滚动容器，
            // 于是滚动条位置每处都不一样。这里统一由弹层自己承担。
            "flex max-h-[calc(100dvh-64px)] flex-col",
            size === "sm" ? "max-w-[420px]" : size === "md" ? "max-w-[640px]" : "max-w-[900px]",
            // lg 在窄屏下改为全屏：900px 的弹层在手机上本来就等于满屏，留 16px 边距只是
            // 把可用高度又切掉一截。断点写 768 而不是 767——Tailwind 的 max-[768px]
            // 编译成 `< 768`，与 CSS 的 `max-width: 767px`（≤767）等价（CLAUDE.md 第七条）。
            size === "lg" && [
              "max-[768px]:left-0 max-[768px]:top-0 max-[768px]:h-dvh max-[768px]:w-screen",
              "max-[768px]:max-h-none max-[768px]:max-w-none max-[768px]:translate-x-0",
              "max-[768px]:translate-y-0 max-[768px]:rounded-none",
            ],
          )}
        >
          <header className="flex shrink-0 items-start justify-between gap-4 border-b border-divider px-5 py-4">
            <div className="grid min-w-0 gap-1">
              <RadixDialog.Title className="text-lg font-semibold text-ink">{title}</RadixDialog.Title>
              {description ? (
                <RadixDialog.Description className="text-base text-ink-faint">
                  {description}
                </RadixDialog.Description>
              ) : (
                // Radix 会对缺失的 Description 发出控制台警告；显式声明为无描述。
                <RadixDialog.Description className="sr-only">{title}</RadixDialog.Description>
              )}
            </div>
            <RadixDialog.Close
              // border-0 同 Button：preflight 未启用，UA 的默认按钮边框还在。
              className="shrink-0 rounded-sm border-0 bg-transparent p-1 text-ink-faint hover:bg-canvas hover:text-ink"
              aria-label="关闭弹框"
            >
              <X size={16} />
            </RadixDialog.Close>
          </header>
          {/* min-h-0 是 flex 子项能收缩的前提，缺了它 overflow-y-auto 不会生效。 */}
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

/** 弹层底部的操作区：取消在左、主操作在右，全站一致。 */
export function DialogActions({ children }: { children: ReactNode }) {
  return <footer className="mt-4 flex items-center justify-end gap-2">{children}</footer>;
}
