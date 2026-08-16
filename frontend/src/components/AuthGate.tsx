import { useState, type FormEvent } from "react";
import { Bot, KeyRound, LoaderCircle, ShieldCheck } from "lucide-react";
import { api, setAccessToken } from "../api";
import type { User } from "../types";

interface AuthGateProps {
  bootstrapRequired: boolean;
  checking?: boolean;
  onAuthenticated: (user: User) => void;
}

export function AuthGate({ bootstrapRequired, checking = false, onAuthenticated }: AuthGateProps) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const result = bootstrapRequired
        ? await api.bootstrap(username.trim(), password, displayName.trim())
        : await api.login(username.trim(), password);
      setAccessToken(result.access_token);
      onAuthenticated(result.user);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "认证失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  };

  if (checking) {
    return <main className="auth-page"><div className="auth-loading" role="status"><span className="auth-spinner" aria-hidden="true"><LoaderCircle/></span><span>正在确认系统状态…</span></div></main>;
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand"><span className="brand-symbol"><Bot size={19}/></span><span><strong>RAG 系统</strong><small>Enterprise</small></span></div>
        <div className="auth-icon" aria-hidden="true">{bootstrapRequired ? <ShieldCheck/> : <KeyRound/>}</div>
        <p className="auth-kicker">V4 · 安全访问</p>
        <h1 id="auth-title">{bootstrapRequired ? "创建首位管理员" : "登录 RAG 工作台"}</h1>
        <p className="auth-description">{bootstrapRequired ? "初始化只能完成一次。管理员可管理成员、知识库和评测权限。" : "使用管理员或已获授权的成员账号继续访问知识库。"}</p>
        <form className="auth-form" onSubmit={submit}>
          {bootstrapRequired ? <label>显示名称<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} minLength={1} maxLength={80} autoComplete="name" required placeholder="例如：项目管理员"/></label> : null}
          <label>用户名<input value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} maxLength={64} pattern="[A-Za-z0-9._-]+" autoCapitalize="none" autoComplete="username" required placeholder="3–64 位字母、数字或 . _ -"/></label>
          <label>密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={bootstrapRequired ? 12 : 1} maxLength={128} autoComplete={bootstrapRequired ? "new-password" : "current-password"} required placeholder={bootstrapRequired ? "至少 12 位" : "输入密码"}/></label>
          {error ? <div className="auth-error" role="alert">{error}</div> : null}
          <button type="submit" disabled={submitting}>{submitting ? <><span className="auth-spinner" aria-hidden="true"><LoaderCircle size={16}/></span>处理中…</> : bootstrapRequired ? "创建管理员并进入" : "登录"}</button>
        </form>
        <p className="auth-note">会话令牌只保存在当前页面内存中，刷新页面后需要重新登录。</p>
      </section>
    </main>
  );
}
