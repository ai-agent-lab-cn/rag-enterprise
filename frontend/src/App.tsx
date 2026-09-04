import { useEffect, useState } from "react";
import { Circle, Cpu, LogOut } from "lucide-react";
import { api, hasAccessToken, setAccessToken } from "./api";
import { AuditPage } from "./components/AuditPage";
import { AppNavigation, type AppPage } from "./components/AppNavigation";
import { AuthGate } from "./components/AuthGate";
import { ChatPage } from "./components/ChatPage";
import { AcceptancePage } from "./components/AcceptancePage";
import { BadCasePage } from "./components/BadCasePage";
import { EvaluationCenterPage } from "./components/EvaluationCenterPage";
import { DataSourcesPage } from "./components/DataSourcesPage";
import { KnowledgeBaseDetailPage } from "./components/KnowledgeBaseDetailPage";
import { KnowledgeBasesPage } from "./components/KnowledgeBasesPage";
import { MembersPage } from "./components/MembersPage";
import { ModelSwitcher } from "./components/ModelSwitcher";
import { OverviewPage } from "./components/OverviewPage";
import { PermissionDeniedPage } from "./components/PermissionDeniedPage";
import { SystemPage } from "./components/SystemPage";
import { Button } from "./components/ui/Button";
import { ToastProvider } from "./components/ui/Toast";
import type { User } from "./types";
import "./tailwind.css";

function pageFromPath(path: string): AppPage {
  // 路由状态只由 URL 派生，保证刷新、前进/后退和可分享链接行为一致。
  if (path.startsWith("/knowledge-bases")) return "knowledge-bases";
  if (path === "/data-sources") return "data-sources";
  if (path.startsWith("/evaluation/bad-cases")) return "bad-cases";
  if (path.startsWith("/evaluation/acceptance")) return "acceptance";
  if (path.startsWith("/evaluation")) return "evaluation-center";
  if (path.startsWith("/chat")) return "chat";
  if (path === "/system") return "system";
  if (path === "/settings/members") return "members";
  if (path === "/settings/audit") return "audit";
  return "overview";
}

export default function App() {
  const [auth, setAuth] = useState<{
    checking: boolean;
    bootstrapRequired: boolean;
    user: User | null;
  }>({ checking: true, bootstrapRequired: false, user: null });
  const [location, setLocation] = useState(() => window.location.pathname + window.location.search);
  const [showKnowledgeBaseCreate, setShowKnowledgeBaseCreate] = useState(false);
  const pathname = location.split("?")[0];
  const page = pageFromPath(pathname);
  useEffect(() => {
    const sync = () => {
      setLocation(window.location.pathname + window.location.search);
      if (window.location.pathname !== "/knowledge-bases") setShowKnowledgeBaseCreate(false);
    };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        if (hasAccessToken()) {
          const user = await api.me();
          if (active) setAuth({ checking: false, bootstrapRequired: false, user });
          return;
        }
        const status = await api.getBootstrapStatus();
        if (active)
          setAuth({
            checking: false,
            bootstrapRequired: status.required,
            user: null,
          });
      } catch {
        if (active) setAuth({ checking: false, bootstrapRequired: false, user: null });
      }
    };
    const expire = () => setAuth({ checking: false, bootstrapRequired: false, user: null });
    window.addEventListener("rag-auth-expired", expire);
    void check();
    return () => {
      active = false;
      window.removeEventListener("rag-auth-expired", expire);
    };
  }, []);
  const navigate = (path: string) => {
    if (path !== window.location.pathname + window.location.search + window.location.hash) {
      window.history.pushState({}, "", path);
    }
    setLocation(window.location.pathname + window.location.search);
    if (window.location.pathname !== "/knowledge-bases") setShowKnowledgeBaseCreate(false);
    // 带锚点的目标滚到对应小节，否则回到顶部。锚点所在的 Section 可能还在加载数据，
    // 所以推迟一帧再找它——直接 scrollIntoView 会因为元素尚未挂载而落空。
    const hash = window.location.hash;
    if (hash) {
      requestAnimationFrame(() => document.querySelector(hash)?.scrollIntoView({ behavior: "smooth" }));
    } else {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  };
  const logout = async () => {
    try {
      await api.logout();
    } catch {
      /* 本地仍清除令牌，避免失效会话继续留在界面。 */
    }
    setAccessToken(null);
    setAuth({ checking: false, bootstrapRequired: false, user: null });
  };
  let body;
  if (auth.checking || !auth.user) {
    body = <AuthGate checking={auth.checking} bootstrapRequired={auth.bootstrapRequired} onAuthenticated={(user) => setAuth({ checking: false, bootstrapRequired: false, user })} />;
  } else {
    const detailMatch = pathname.match(/^\/knowledge-bases\/([^/]+)$/);
    const conversationMatch = pathname.match(/^\/chat\/(conv_[a-f0-9]{16})$/);
    const adminPath = pathname === "/system" || pathname.startsWith("/settings/");
    let content;
    if (adminPath && auth.user.role !== "admin") content = <PermissionDeniedPage onBack={() => navigate("/overview")} />;
    else if (pathname === "/system") content = <SystemPage />;
    else if (pathname === "/settings/members") content = <MembersPage currentUser={auth.user} />;
    else if (pathname === "/settings/audit") content = <AuditPage />;
    else if (detailMatch) content = <KnowledgeBaseDetailPage id={detailMatch[1]} onOpen={navigate} />;
    else if (pathname === "/knowledge-bases") content = <KnowledgeBasesPage isAdmin={auth.user.role === "admin"} onOpen={navigate} showCreate={showKnowledgeBaseCreate} onCloseCreate={() => setShowKnowledgeBaseCreate(false)} />;
    else if (pathname === "/data-sources") content = <DataSourcesPage onOpen={navigate} />;
    else if (pathname.startsWith("/evaluation/bad-cases")) content = <BadCasePage isAdmin={auth.user.role === "admin"} />;
    else if (pathname.startsWith("/evaluation/acceptance")) content = <AcceptancePage isAdmin={auth.user.role === "admin"} />;
    else if (pathname.startsWith("/evaluation")) content = <EvaluationCenterPage />;
    else if (pathname.startsWith("/chat")) content = <ChatPage conversationId={conversationMatch?.[1]} onOpen={navigate} />;
    else content = <OverviewPage onOpen={navigate} onLogout={() => void logout()} user={auth.user} />;
    const pageLabel: Record<AppPage, string> = {
      overview: "项目概览",
      "knowledge-bases": "知识库管理",
      "data-sources": "数据源管理",
      chat: "对话助手",
      "evaluation-center": "评测中心",
      "bad-cases": "Bad Case",
      acceptance: "链路验收",
      system: "系统状态",
      members: "成员与权限",
      audit: "审计记录",
    };
    body = (
    <main className={`grid min-h-screen grid-cols-[138px_minmax(0,1fr)] max-[1025px]:grid-cols-[74px_minmax(0,1fr)] max-[561px]:block page-${page}`}>
      <aside className="sticky top-0 z-20 flex h-screen flex-col border-r border-line bg-[rgba(255,255,255,.98)] max-[768px]:h-14 max-[768px]:flex-row max-[768px]:items-center max-[768px]:border-r-0 max-[768px]:border-b max-[768px]:border-b-line">
        <button
          type="button"
          className="flex h-14 items-center gap-[7px] border-0 border-b border-b-divider bg-transparent px-0 py-0 text-left text-[#171c2f] min-[1025px]:px-[9px] max-[1181px]:justify-center max-[561px]:border-b-0 min-[1025px]:border-b-0 min-[768px]:max-[1025px]:h-[60px] max-[768px]:h-[55px] max-[768px]:w-[54px] max-[768px]:flex-none"
          onClick={() => navigate("/overview")}
          aria-label="RongRAG 概览"
        >
          <span className="grid h-7 w-7 place-items-center rounded-[7px] bg-[linear-gradient(145deg,#6353e9,#4936d0)] text-[14px] font-bold text-white shadow-[0_5px_14px_rgba(87,68,221,.22)] min-[1025px]:rounded-[6px] max-[561px]:h-[27px] max-[561px]:w-[27px]">
            <Cpu size={19} />
          </span>
          <span className="grid gap-px max-[1181px]:hidden">
            <strong className="text-lg min-[1025px]:whitespace-nowrap">RAG 系统</strong>
          </span>
        </button>
        <AppNavigation page={page} onNavigate={navigate} isAdmin={auth.user.role === "admin"} />
        {auth.user.role === "admin" ? <ModelSwitcher /> : null}
      </aside>
      <div className="min-w-0">
        {page !== "overview" ? (
          <header className="sticky top-0 z-10 flex h-14 items-center justify-between border-b border-line bg-[rgba(255,255,255,.98)] px-5 shadow-[0_1px_0_rgba(23,29,49,.02)] min-[768px]:max-[1025px]:h-[60px] max-[768px]:h-[52px] max-[1025px]:px-6">
            <div className="flex items-center gap-2.5">
              <h1 className="m-0 flex items-center gap-2.5 text-lg font-bold text-ink max-[1025px]:text-[16px]">
                {pageLabel[page]}
                {page === "chat" ? (
                  <span className="m-0 inline-flex items-center gap-[5px] rounded-full bg-[#eef9f2] px-2 py-[5px] text-sm font-semibold text-[#2f9560]">
                    <Circle size={7} fill="currentColor" /> 在线
                  </span>
                ) : null}
              </h1>
              <div className="flex items-center gap-2.5 empty:hidden" id="topbar-context" />
              {pathname === "/knowledge-bases" ? (
                <Button onClick={() => setShowKnowledgeBaseCreate(true)}>＋ 新建知识库</Button>
              ) : null}
            </div>
            <div className="flex items-center gap-2 max-[1025px]:gap-2.5">
              <span className="max-w-[140px] overflow-hidden text-ellipsis whitespace-nowrap text-base text-ink-muted max-[768px]:hidden" title={`${auth.user.display_name} · ${auth.user.role === "admin" ? "管理员" : "成员"}`}>
                {auth.user.display_name}
              </span>
              <button
                type="button"
                className="grid h-8 w-8 place-items-center p-0 rounded-md border border-line bg-surface text-[#747d92] transition-[color,border-color,background-color,transform] duration-150 ease-in-out hover:border-[#ccc5f1] hover:bg-[#faf9ff] hover:text-brand active:scale-[.98] max-[1025px]:h-[34px] max-[1025px]:w-[34px]"
                onClick={() => void logout()}
                aria-label="退出登录"
                title="退出登录"
              >
                <LogOut size={16} />
              </button>
            </div>
          </header>
        ) : null}
        {content}
      </div>
    </main>
    );
  }
  return <ToastProvider>{body}</ToastProvider>;
}
