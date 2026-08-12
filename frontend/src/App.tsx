import { useEffect, useState } from "react";
import { AnswerEvaluationPage } from "./components/AnswerEvaluationPage";
import { AppNavigation, type AppPage } from "./components/AppNavigation";
import { ChatPage } from "./components/ChatPage";
import { EvaluationPage } from "./components/EvaluationPage";
import { KnowledgeBaseDetailPage } from "./components/KnowledgeBaseDetailPage";
import { KnowledgeBasesPage } from "./components/KnowledgeBasesPage";
import { OverviewPage } from "./components/OverviewPage";
import "./styles.css";

function pageFromPath(path: string): AppPage {
  // 路由状态只由 URL 派生，保证刷新、前进/后退和可分享链接行为一致。
  if (path.startsWith("/knowledge-bases")) return "knowledge-bases";
  if (path === "/evaluation/retrieval") return "retrieval-evaluation";
  if (path === "/evaluation/answers") return "answer-evaluation";
  if (path.startsWith("/chat")) return "chat";
  return "overview";
}

export default function App() {
  const [location, setLocation] = useState(() => window.location.pathname + window.location.search);
  const pathname = location.split("?")[0];
  const page = pageFromPath(pathname);
  useEffect(() => { const sync = () => setLocation(window.location.pathname + window.location.search); window.addEventListener("popstate", sync); return () => window.removeEventListener("popstate", sync); }, []);
  const navigate = (path: string) => { if (path !== window.location.pathname + window.location.search) window.history.pushState({}, "", path); setLocation(window.location.pathname + window.location.search); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const detailMatch = pathname.match(/^\/knowledge-bases\/([^/]+)$/);
  const conversationMatch = pathname.match(/^\/chat\/(conv_[a-f0-9]{16})$/);
  let content;
  if (detailMatch) content = <KnowledgeBaseDetailPage id={detailMatch[1]} onOpen={navigate}/>;
  else if (pathname === "/knowledge-bases") content = <KnowledgeBasesPage onOpen={navigate}/>;
  else if (pathname === "/evaluation/retrieval") content = <EvaluationPage/>;
  else if (pathname === "/evaluation/answers") content = <AnswerEvaluationPage/>;
  else if (pathname.startsWith("/chat")) content = <ChatPage conversationId={conversationMatch?.[1]} onOpen={navigate}/>;
  else content = <OverviewPage onOpen={navigate}/>;
  return <main className="app-shell"><header className="topbar"><button className="brand" onClick={() => navigate("/overview")} aria-label="RongRAG 概览"><span className="brand-symbol">R</span><strong>RongRAG</strong></button><AppNavigation page={page} onNavigate={navigate}/><div className="system-state"><span/> 本地检索引擎</div></header>{content}</main>;
}
