import { ShieldX } from "lucide-react";

export function PermissionDeniedPage({ onBack }: { onBack: () => void }) {
  return <section className="admin-page"><div className="admin-state" role="alert"><ShieldX aria-hidden="true"/><h1>无权访问管理页面</h1><p>此入口仅向管理员开放。普通成员仍可使用已授权的知识库与问答功能。</p><button onClick={onBack}>返回项目概览</button></div></section>;
}
