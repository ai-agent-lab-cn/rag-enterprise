export type AppPage = "chat" | "evaluation";

interface AppNavigationProps {
  page: AppPage;
  onNavigate: (page: AppPage) => void;
}

const ITEMS: Array<{ page: AppPage; label: string; hint: string }> = [
  { page: "chat", label: "问答工作台", hint: "问答" },
  { page: "evaluation", label: "检索评测", hint: "评测" },
];

export function AppNavigation({ page, onNavigate }: AppNavigationProps) {
  return (
    <nav className="app-navigation" aria-label="主导航">
      {ITEMS.map((item) => (
        <button
          key={item.page}
          type="button"
          className={page === item.page ? "is-active" : ""}
          aria-current={page === item.page ? "page" : undefined}
          onClick={() => onNavigate(item.page)}
        >
          <span className="nav-mark" aria-hidden="true">{item.hint.slice(0, 1)}</span>
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
}
