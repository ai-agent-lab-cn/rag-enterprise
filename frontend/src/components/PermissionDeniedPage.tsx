import { ShieldX } from "lucide-react";
import { Button } from "./ui/Button";

export function PermissionDeniedPage({ onBack }: { onBack: () => void }) {
  // 这个按钮以前既没有 class 也没有样式，渲染出来就是浏览器默认的灰色方块。
  return (
    <section className="w-auto max-w-[1440px] mx-auto p-[26px_24px_52px] max-[768px]:p-[20px_14px_36px] min-[1025px]:p-[20px_20px_40px]">
      <div
        role="alert"
        className="min-h-[320px] grid place-items-center content-center rounded-xl border border-dashed border-line-firm bg-surface text-center text-[#8a93a7] [&>svg]:h-9 [&>svg]:w-9 [&>svg]:text-[#8b80dc] [&>h1]:mt-[15px] [&>h1]:mb-0 [&>h1]:text-ink [&>p]:max-w-[460px] [&>p]:mt-2 [&>p]:mb-[18px] [&>p]:leading-[1.7]"
      >
        <ShieldX aria-hidden="true" />
        <h1>无权访问管理页面</h1>
        <p>此入口仅向管理员开放。普通成员仍可使用已授权的知识库与问答功能。</p>
        <Button onClick={onBack}>返回项目概览</Button>
      </div>
    </section>
  );
}
