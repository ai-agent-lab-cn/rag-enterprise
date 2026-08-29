import { Database, LayoutDashboard, MessageSquareText, Plug, SearchCheck, Settings, ShieldCheck, Users, type LucideIcon } from "lucide-react";

export type AppPage = "overview" | "knowledge-bases" | "data-sources" | "chat" | "evaluation-center" | "system" | "members" | "audit";

interface AppNavigationProps { page: AppPage; onNavigate: (path: string) => void; isAdmin: boolean; }
const ITEMS: Array<{ page: AppPage; path: string; label: string; icon: LucideIcon; group?: string }> = [
  { page: "overview", path: "/overview", label: "概览", icon: LayoutDashboard },
  { page: "chat", path: "/chat", label: "问答工作台", icon: MessageSquareText, group: "应用" },
  { page: "knowledge-bases", path: "/knowledge-bases", label: "知识库管理", icon: Database, group: "知识库管理" },
  { page: "data-sources", path: "/data-sources", label: "数据源管理", icon: Plug },
  { page: "evaluation-center", path: "/evaluation", label: "评测中心", icon: SearchCheck, group: "测评评估" },
];
const ADMIN_ITEMS: typeof ITEMS = [
  { page: "system", path: "/system", label: "系统状态", icon: Settings, group: "管理配置" },
  { page: "members", path: "/settings/members", label: "成员与权限", icon: Users },
  { page: "audit", path: "/settings/audit", label: "审计记录", icon: ShieldCheck },
];
export function AppNavigation({ page, onNavigate, isAdmin }: AppNavigationProps) {
  const items = isAdmin ? [...ITEMS, ...ADMIN_ITEMS] : ITEMS;
  return <nav className="app-navigation" aria-label="主导航">{items.map((item) => <div className="nav-entry" key={item.page}>{item.group ? <span className="nav-group">{item.group}</span> : null}<button type="button" aria-label={item.label} className={page === item.page ? "is-active" : ""} aria-current={page === item.page ? "page" : undefined} onClick={() => onNavigate(item.path)}><item.icon className="nav-mark" aria-hidden="true" size={18}/><span>{item.label}</span></button></div>)}</nav>;
}
