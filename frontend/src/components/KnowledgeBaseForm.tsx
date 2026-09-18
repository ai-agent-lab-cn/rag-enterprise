import type { FormEvent } from "react";
import type { CategoryTemplate } from "../types";
import { Button } from "./ui/Button";
import { Checkbox } from "./ui/Checkbox";
import { DialogActions } from "./ui/Dialog";
import { Input, Textarea } from "./ui/Input";

export interface KnowledgeBaseFormProps {
  name: string;
  description: string;
  busy: boolean;
  submitText: string;
  applyTemplate?: boolean;
  template?: CategoryTemplate | null;
  onApplyTemplate?: (value: boolean) => void;
  onName: (value: string) => void;
  onDescription: (value: string) => void;
  onCancel: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

/**
 * 知识库基础信息表单。
 *
 * 创建与编辑共用名称、描述和校验约束；默认分类模板只属于创建流程，
 * 通过 onApplyTemplate 是否存在来决定是否展示，避免编辑时误改分类结构。
 */
export function KnowledgeBaseForm({
  name,
  description,
  busy,
  submitText,
  applyTemplate,
  template,
  onApplyTemplate,
  onName,
  onDescription,
  onCancel,
  onSubmit,
}: KnowledgeBaseFormProps) {
  const activeItems = template?.items.filter((item) => item.active) ?? [];
  const templateSummary = template === null
    ? "正在读取默认分类模板…"
    : activeItems.length
      ? `将复制 ${activeItems.length} 个有效分类：${activeItems.slice(0, 4).map((item) => item.name).join("、")}${activeItems.length > 4 ? "等" : ""}`
      : "当前模板无有效分类，新知识库的分类列表将为空";

  return (
    <form className="grid gap-[9px] px-[22px] pt-[20px]" onSubmit={onSubmit}>
      <label className="text-[#4e576c] text-[13px] font-semibold" htmlFor="knowledge-base-name">
        知识库名称
      </label>
      <Input
        className="py-[10px]"
        autoFocus
        id="knowledge-base-name"
        value={name}
        onChange={(event) => onName(event.target.value)}
        required
      />
      <label className="text-[#4e576c] text-[13px] font-semibold" htmlFor="knowledge-base-description">
        描述 <span className="text-[#939bad] text-[11px] font-normal">选填</span>
      </label>
      <Textarea
        id="knowledge-base-description"
        value={description}
        onChange={(event) => onDescription(event.target.value)}
        rows={4}
      />
      {onApplyTemplate ? (
        <div className="grid gap-[4px] border-t border-divider pt-[10px]">
          <Checkbox
            showLabel
            label="应用默认分类模板"
            checked={Boolean(applyTemplate)}
            onCheckedChange={onApplyTemplate}
          />
          <small className="text-[#7b8395] text-[11px]">{templateSummary}</small>
          <small className="text-[#7b8395] text-[11px]">资料可以暂时没有分类，系统不会替它创建占位分类。</small>
        </div>
      ) : null}
      <DialogActions>
        <Button variant="secondary" loading={busy} onClick={onCancel}>取消</Button>
        <Button type="submit" loading={busy}>{submitText}</Button>
      </DialogActions>
    </form>
  );
}
