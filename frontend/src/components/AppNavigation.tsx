export type AppPage = "overview" | "knowledge-bases" | "chat" | "retrieval-evaluation" | "answer-evaluation";

interface AppNavigationProps { page: AppPage; onNavigate: (path: string) => void; }
const ITEMS: Array<{ page: AppPage; path: string; label: string; mark: string }> = [
  { page: "overview", path: "/overview", label: "概览", mark: "概" },
  { page: "knowledge-bases", path: "/knowledge-bases", label: "知识库", mark: "知" },
  { page: "chat", path: "/chat", label: "问答", mark: "问" },
  { page: "answer-evaluation", path: "/evaluation/answers", label: "回答评测", mark: "评" },
];
export function AppNavigation({ page, onNavigate }: AppNavigationProps) {
  return <nav className="app-navigation" aria-label="主导航">{ITEMS.map((item) => <button key={item.page} type="button" className={page === item.page ? "is-active" : ""} aria-current={page === item.page ? "page" : undefined} onClick={() => onNavigate(item.path)}><span className="nav-mark" aria-hidden="true">{item.mark}</span><span>{item.label}</span></button>)}</nav>;
}
