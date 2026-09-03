import { useRef, type ReactNode } from "react";
import { Button, type ButtonProps } from "./Button";
import { normalizeBlockedReason } from "./blockedReason";

/**
 * 触发文件选择的按钮。
 *
 * **存在的理由是高度。** `<input type="file">` 的原生外观无法定制，所以全仓库的做法
 * 是把它藏起来、另给一个可见的触发器。迁移前那个触发器是 `<label>`，于是它没法用
 * `<Button>`——`<button>` 里放 `<input>` 点不动——只能挂遗留 class：工具栏那个是
 * `.primary-action`（min-height 36px），表格行内那个是 `.table-file-action`
 * （padding 3px / font 11px）。旁边的 Select 和 Button 是 28px，三者谁也不齐。
 *
 * 这里改用「隐藏 input + ref.click()」：触发器就是真正的 `<Button>`，size/variant/
 * blockedReason 全部继承，高度自然和同排控件一致。DocumentPanel 的拖拽区早就是这个
 * 模式，只是没抽出来，于是另外两处各自长歪。
 */
// 必须 Omit onSelect：它是 `<button>` 的原生事件属性（SyntheticEvent），
// 不摘掉的话会和下面的 File[] 回调交叉成联合类型，调用方拿到的参数无法索引。
export type FileButtonProps = Omit<ButtonProps, "onClick" | "type" | "onSelect"> & {
  accept?: string;
  multiple?: boolean;
  /**
   * 隐藏 input 的无障碍名称，默认取按钮文案。
   *
   * 表格里十行都叫「更新文件」时必须显式区分，否则读屏用户听到十个同名控件。
   */
  inputLabel?: string;
  onSelect: (files: File[]) => void;
};

export function FileButton({
  accept,
  multiple = false,
  inputLabel,
  onSelect,
  children,
  ...rest
}: FileButtonProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const reasons = normalizeBlockedReason(rest.blockedReason);
  const disabled = reasons.length > 0 || Boolean(rest.loading);

  return (
    <>
      <Button {...rest} onClick={() => inputRef.current?.click()}>
        {children}
      </Button>
      {/* sr-only 而不是 hidden：input 仍在无障碍树里，读屏用户走的是同一个控件。
          tabIndex -1 让它退出 Tab 序列，避免可见按钮之外再多一个看不见的焦点位。 */}
      <input
        ref={inputRef}
        className="sr-only"
        type="file"
        tabIndex={-1}
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        aria-label={inputLabel ?? label(children)}
        title={reasons.length > 0 ? reasons.join("、") : undefined}
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          // 先清空再回调：不清的话连选两次同一个文件不会触发 change。
          event.currentTarget.value = "";
          if (files.length) onSelect(files);
        }}
      />
    </>
  );
}

/** 按钮文案通常就是一段文字；不是的话调用方必须给 inputLabel。 */
function label(children: ReactNode) {
  return typeof children === "string" ? children : undefined;
}
