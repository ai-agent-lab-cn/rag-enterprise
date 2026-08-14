import { BookOpen, Database, LayoutDashboard, MessageSquareText, SearchCheck, type LucideIcon } from "lucide-react";

export type AppPage = "overview" | "knowledge-bases" | "chat" | "retrieval-evaluation" | "answer-evaluation";

interface AppNavigationProps { page: AppPage; onNavigate: (path: string) => void; }
const ITEMS: Array<{ page: AppPage; path: string; label: string; icon: LucideIcon; group?: string }> = [
  { page: "overview", path: "/overview", label: "项目概览", icon: LayoutDashboard, group: "工作空间" },
  { page: "knowledge-bases", path: "/knowledge-bases", label: "知识库管理", icon: Database, group: "知识管理" },
  { page: "chat", path: "/chat", label: "问答工作台", icon: MessageSquareText },
  { page: "answer-evaluation", path: "/evaluation/answers", label: "回答评测", icon: BookOpen, group: "质量评测" },
  { page: "retrieval-evaluation", path: "/evaluation/retrieval", label: "检索评测", icon: SearchCheck },
];
export function AppNavigation({ page, onNavigate }: AppNavigationProps) {
  return <nav className="app-navigation" aria-label="主导航">{ITEMS.map((item) => <div className="nav-entry" key={item.page}>{item.group ? <span className="nav-group">{item.group}</span> : null}<button type="button" className={page === item.page ? "is-active" : ""} aria-current={page === item.page ? "page" : undefined} onClick={() => onNavigate(item.path)}><item.icon className="nav-mark" aria-hidden="true" size={18}/><span>{item.label}</span></button></div>)}</nav>;
}
