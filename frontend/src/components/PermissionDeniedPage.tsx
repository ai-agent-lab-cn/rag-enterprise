import { ShieldX } from "lucide-react";
import { Button } from "./ui/Button";

export function PermissionDeniedPage({ onBack }: { onBack: () => void }) {
  // 这个按钮以前既没有 class 也没有样式，渲染出来就是浏览器默认的灰色方块。
  return <section className="admin-page"><div className="admin-state" role="alert"><ShieldX aria-hidden="true"/><h1>无权访问管理页面</h1><p>此入口仅向管理员开放。普通成员仍可使用已授权的知识库与问答功能。</p><Button onClick={onBack}>返回项目概览</Button></div></section>;
}
