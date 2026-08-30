import { useEffect, useState } from "react";
import { Circle, Cpu, LogOut } from "lucide-react";
import { api, hasAccessToken, setAccessToken } from "./api";
import { AuditPage } from "./components/AuditPage";
import { AppNavigation, type AppPage } from "./components/AppNavigation";
import { AuthGate } from "./components/AuthGate";
import { ChatPage } from "./components/ChatPage";
import { EvaluationCenterPage } from "./components/EvaluationCenterPage";
import { DataSourcesPage } from "./components/DataSourcesPage";
import { KnowledgeBaseDetailPage } from "./components/KnowledgeBaseDetailPage";
import { KnowledgeBasesPage } from "./components/KnowledgeBasesPage";
import { MembersPage } from "./components/MembersPage";
import { OverviewPage } from "./components/OverviewPage";
import { PermissionDeniedPage } from "./components/PermissionDeniedPage";
import { SystemPage } from "./components/SystemPage";
import type { User } from "./types";
import "./styles.css";

function pageFromPath(path: string): AppPage {
  // 路由状态只由 URL 派生，保证刷新、前进/后退和可分享链接行为一致。
  if (path.startsWith("/knowledge-bases")) return "knowledge-bases";
  if (path === "/data-sources") return "data-sources";
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
    if (path !== window.location.pathname + window.location.search) window.history.pushState({}, "", path);
    setLocation(window.location.pathname + window.location.search);
    if (window.location.pathname !== "/knowledge-bases") setShowKnowledgeBaseCreate(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
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
  if (auth.checking || !auth.user) return <AuthGate checking={auth.checking} bootstrapRequired={auth.bootstrapRequired} onAuthenticated={(user) => setAuth({ checking: false, bootstrapRequired: false, user })} />;
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
  else if (pathname.startsWith("/evaluation")) content = <EvaluationCenterPage isAdmin={auth.user.role === "admin"} initialTab={pathname === "/evaluation/retrieval" ? "retrieval" : pathname === "/evaluation/answers" ? "answer" : "overview"} />;
  else if (pathname.startsWith("/chat")) content = <ChatPage conversationId={conversationMatch?.[1]} onOpen={navigate} />;
  else content = <OverviewPage onOpen={navigate} onLogout={() => void logout()} user={auth.user} />;
  const pageLabel: Record<AppPage, string> = {
    overview: "项目概览",
    "knowledge-bases": "知识库管理",
    "data-sources": "数据源管理",
    chat: "对话助手",
    "evaluation-center": "评测中心",
    system: "系统状态",
    members: "成员与权限",
    audit: "审计记录",
  };
  return (
    <main className={`app-shell page-${page}`}>
      <aside className="app-sidebar">
        <button className="brand" onClick={() => navigate("/overview")} aria-label="RongRAG 概览">
          <span className="brand-symbol">
            <Cpu size={19} />
          </span>
          <span>
            <strong>RAG 系统</strong>
          </span>
        </button>
        <AppNavigation page={page} onNavigate={navigate} isAdmin={auth.user.role === "admin"} />
      </aside>
      <div className="app-main">
        {page !== "overview" ? (
          <header className="topbar">
            <div className="topbar-primary">
              <h1 className="breadcrumb">
                {pageLabel[page]}
                {page === "chat" ? (
                  <span className="online-badge">
                    <Circle size={7} fill="currentColor" /> 在线
                  </span>
                ) : null}
              </h1>
              <div className="topbar-context" id="topbar-context" />
              {pathname === "/knowledge-bases" ? (
                <button className="page-action" type="button" onClick={() => setShowKnowledgeBaseCreate(true)}>
                  ＋ 新建知识库
                </button>
              ) : null}
            </div>
            <div className="topbar-actions">
              <span className="current-user" title={`${auth.user.display_name} · ${auth.user.role === "admin" ? "管理员" : "成员"}`}>
                {auth.user.display_name}
              </span>
              <button className="logout-button" onClick={() => void logout()} aria-label="退出登录" title="退出登录">
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
