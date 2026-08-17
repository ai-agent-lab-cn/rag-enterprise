import { useEffect, useState } from "react";
import { Bot, Circle, LogOut } from "lucide-react";
import { api, hasAccessToken, setAccessToken } from "./api";
import { AnswerEvaluationPage } from "./components/AnswerEvaluationPage";
import { AuditPage } from "./components/AuditPage";
import { AppNavigation, type AppPage } from "./components/AppNavigation";
import { AuthGate } from "./components/AuthGate";
import { ChatPage } from "./components/ChatPage";
import { EvaluationPage } from "./components/EvaluationPage";
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
  if (path === "/evaluation/retrieval") return "retrieval-evaluation";
  if (path === "/evaluation/answers") return "answer-evaluation";
  if (path.startsWith("/chat")) return "chat";
  if (path === "/system") return "system";
  if (path === "/settings/members") return "members";
  if (path === "/settings/audit") return "audit";
  return "overview";
}

export default function App() {
  const isDemo = import.meta.env.VITE_DEPLOYMENT_MODE === "demo";
  const [auth, setAuth] = useState<{ checking: boolean; bootstrapRequired: boolean; user: User | null }>({ checking: true, bootstrapRequired: false, user: null });
  const [location, setLocation] = useState(() => window.location.pathname + window.location.search);
  const pathname = location.split("?")[0];
  const page = pageFromPath(pathname);
  useEffect(() => { const sync = () => setLocation(window.location.pathname + window.location.search); window.addEventListener("popstate", sync); return () => window.removeEventListener("popstate", sync); }, []);
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
        if (active) setAuth({ checking: false, bootstrapRequired: status.required, user: null });
      } catch {
        if (active) setAuth({ checking: false, bootstrapRequired: false, user: null });
      }
    };
    const expire = () => setAuth({ checking: false, bootstrapRequired: false, user: null });
    window.addEventListener("rag-auth-expired", expire);
    void check();
    return () => { active = false; window.removeEventListener("rag-auth-expired", expire); };
  }, []);
  const navigate = (path: string) => { if (path !== window.location.pathname + window.location.search) window.history.pushState({}, "", path); setLocation(window.location.pathname + window.location.search); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const logout = async () => {
    try { await api.logout(); } catch { /* 本地仍清除令牌，避免失效会话继续留在界面。 */ }
    setAccessToken(null);
    setAuth({ checking: false, bootstrapRequired: false, user: null });
  };
  if (auth.checking || !auth.user) return <AuthGate checking={auth.checking} bootstrapRequired={auth.bootstrapRequired} onAuthenticated={(user) => setAuth({ checking: false, bootstrapRequired: false, user })}/>;
  const detailMatch = pathname.match(/^\/knowledge-bases\/([^/]+)$/);
  const conversationMatch = pathname.match(/^\/chat\/(conv_[a-f0-9]{16})$/);
  const adminPath = pathname === "/system" || pathname.startsWith("/settings/");
  let content;
  if (adminPath && auth.user.role !== "admin") content = <PermissionDeniedPage onBack={() => navigate("/overview")}/>;
  else if (pathname === "/system") content = <SystemPage/>;
  else if (pathname === "/settings/members") content = <MembersPage currentUser={auth.user}/>;
  else if (pathname === "/settings/audit") content = <AuditPage/>;
  else if (detailMatch) content = <KnowledgeBaseDetailPage id={detailMatch[1]} onOpen={navigate}/>;
  else if (pathname === "/knowledge-bases") content = <KnowledgeBasesPage onOpen={navigate}/>;
  else if (pathname === "/evaluation/retrieval") content = <EvaluationPage/>;
  else if (pathname === "/evaluation/answers") content = <AnswerEvaluationPage/>;
  else if (pathname.startsWith("/chat")) content = <ChatPage conversationId={conversationMatch?.[1]} onOpen={navigate}/>;
  else content = <OverviewPage onOpen={navigate}/>;
  const pageLabel: Record<AppPage, string> = { overview: "项目概览", "knowledge-bases": "知识库管理", chat: "对话助手", "answer-evaluation": "回答评测", "retrieval-evaluation": "检索评测", system: "系统状态", members: "成员与权限", audit: "审计记录" };
  return <main className="app-shell"><aside className="app-sidebar"><button className="brand" onClick={() => navigate("/overview")} aria-label="RongRAG 概览"><span className="brand-symbol"><Bot size={19}/></span><span><strong>RAG 系统</strong><small>Enterprise</small></span></button><AppNavigation page={page} onNavigate={navigate} isAdmin={auth.user.role === "admin"}/><div className="sidebar-foot"><Circle size={8} fill="currentColor"/> 服务运行正常</div></aside><div className="app-main"><header className="topbar"><div className="breadcrumb">{pageLabel[page]}{page === "chat" ? <span className="online-badge"><Circle size={7} fill="currentColor"/> 在线</span> : null}</div><div className="topbar-actions"><div className="system-state" title={isDemo ? "免费演示环境会休眠，重启或重新部署后数据可能重置" : undefined}><span className="state-dot"/><span className="environment-label environment-label-desktop">{isDemo ? "演示环境 · 数据可能重置" : "本地环境"}</span><span className="environment-label environment-label-mobile">{isDemo ? "Demo · 数据会重置" : "本地"}</span></div><span className="current-user" title={`${auth.user.display_name} · ${auth.user.role === "admin" ? "管理员" : "成员"}`}>{auth.user.display_name}</span><button className="logout-button" onClick={() => void logout()} aria-label="退出登录" title="退出登录"><LogOut size={16}/></button></div></header>{content}</div></main>;
}
