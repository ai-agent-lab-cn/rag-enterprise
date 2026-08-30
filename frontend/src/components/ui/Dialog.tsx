import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
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
}

export function Dialog({ open, title, description, children, onClose, size = "sm" }: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-50 bg-ink/35" />
        <RadixDialog.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2",
            "w-[calc(100vw-32px)] rounded-lg bg-surface shadow-modal",
            "focus-visible:outline-none",
            size === "sm" ? "max-w-[420px]" : size === "md" ? "max-w-[640px]" : "max-w-[900px]",
          )}
        >
          <header className="flex items-start justify-between gap-4 border-b border-divider px-5 py-4">
            <div className="grid gap-1">
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
              className="rounded-sm border-0 bg-transparent p-1 text-ink-faint hover:bg-canvas hover:text-ink"
              aria-label="关闭弹框"
            >
              <X size={16} />
            </RadixDialog.Close>
          </header>
          <div className="px-5 py-4">{children}</div>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

/** 弹层底部的操作区：取消在左、主操作在右，全站一致。 */
export function DialogActions({ children }: { children: ReactNode }) {
  return <footer className="mt-4 flex items-center justify-end gap-2">{children}</footer>;
}
