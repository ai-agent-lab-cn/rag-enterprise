import { useState, type FormEvent } from "react";
import { Bot, Eye, EyeOff, KeyRound, LoaderCircle, ShieldCheck } from "lucide-react";
import { api, setAccessToken } from "../api";
import type { User } from "../types";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";

interface AuthGateProps {
  bootstrapRequired: boolean;
  checking?: boolean;
  onAuthenticated: (user: User) => void;
}

export function AuthGate({ bootstrapRequired, checking = false, onAuthenticated }: AuthGateProps) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
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

  // 拆成两条 utility：bg-canvas 是纯色兜底（background-color），bg-[image:...] 强制归到
  // background-image。合写成 `bg-[radial-gradient(...),#fafbfe]` 会被 Tailwind 整体解析成
  // 一个非法的 background-color 值，浏览器直接丢弃整条声明——径向光晕和兜底色都会消失。
  const authPage =
    "min-h-screen grid place-items-center p-[32px_20px] bg-canvas bg-[image:radial-gradient(circle_at_50%_0,#eeebff_0,rgba(238,235,255,0)_42%)] max-[768px]:p-[18px_14px]";

  if (checking) {
    return (
      <main className={authPage}>
        <div className="inline-flex items-center gap-2.5 text-[13px] text-[#697187]" role="status">
          <span className="inline-flex [animation:spin_0.7s_linear_infinite]" aria-hidden="true">
            <LoaderCircle />
          </span>
          <span>正在确认系统状态…</span>
        </div>
      </main>
    );
  }

  return (
    <main className={authPage}>
      <section
        className="w-[min(100%,430px)] rounded-2xl border border-line bg-surface p-7 shadow-[0_16px_40px_rgba(29,41,81,.1)] max-[768px]:p-[22px_18px]"
        aria-labelledby="auth-title"
      >
        <div className="flex items-center gap-[11px] mb-[30px] max-[768px]:mb-6">
          <span className="grid h-7 w-7 place-items-center rounded-[7px] bg-[linear-gradient(145deg,#6353e9,#4936d0)] text-[14px] font-bold text-white shadow-[0_5px_14px_rgba(87,68,221,.22)] min-[1025px]:rounded-[6px] max-[561px]:h-[27px] max-[561px]:w-[27px]">
            <Bot size={19} />
          </span>
          <span className="grid gap-px">
            <strong className="text-lg">RAG 系统</strong>
            <small className="text-[9px] font-medium tracking-[.08em] text-[#8b92a6] uppercase">Enterprise</small>
          </span>
        </div>
        <div className="grid h-11 w-11 place-items-center rounded-xl bg-brand-subtle text-brand [&>svg]:w-[21px]" aria-hidden="true">
          {bootstrapRequired ? <ShieldCheck /> : <KeyRound />}
        </div>
        <p className="mt-[18px] mb-[7px] text-sm font-semibold tracking-[.06em] text-brand">V4 · 安全访问</p>
        <h1 id="auth-title" className="m-0 text-[24px] leading-8 max-[768px]:text-[22px]">
          {bootstrapRequired ? "创建首位管理员" : "登录 RAG 工作台"}
        </h1>
        <p className="mt-2 mb-[22px] text-md leading-[1.7] text-[#70798f]">
          {bootstrapRequired ? "初始化只能完成一次。管理员可管理成员、知识库和评测权限。" : "使用管理员或已获授权的成员账号继续访问知识库。"}
        </p>
        <form className="grid gap-[15px]" onSubmit={submit}>
          {bootstrapRequired ? (
            <label className="grid gap-[7px] text-md font-medium text-ink-muted">
              显示名称
              <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} minLength={1} maxLength={80} autoComplete="name" required placeholder="例如：项目管理员" />
            </label>
          ) : null}
          {/* pattern 里的 `-` 必须转义：Chrome 125+ 用 `v` flag 解析它，字符类里未转义的
              `-` 是语法错误，整个 pattern 会被静默丢弃。见 CLAUDE.md 第七条。 */}
          <label className="grid gap-[7px] text-md font-medium text-ink-muted">
            用户名
            <Input value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} maxLength={64} pattern="[A-Za-z0-9._\-]+" autoCapitalize="none" autoComplete="username" required placeholder="3–64 位字母、数字或 . _ -" />
          </label>
          <label htmlFor="auth-password" className="text-md font-medium text-ink-muted">密码</label>
          <div className="relative -mt-2">
            <Input id="auth-password" className="pr-10" type={passwordVisible ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} minLength={bootstrapRequired ? 12 : 1} maxLength={128} autoComplete={bootstrapRequired ? "new-password" : "current-password"} required placeholder={bootstrapRequired ? "至少 12 位" : "输入密码"} />
            <Button variant="ghost" size="icon" className="absolute right-0.5 top-0.5 text-ink-faint hover:text-brand" aria-label={passwordVisible ? "隐藏密码" : "显示密码"} aria-pressed={passwordVisible} onClick={() => setPasswordVisible((visible) => !visible)}>
              {passwordVisible ? <EyeOff size={16}/> : <Eye size={16}/>}
            </Button>
          </div>
          {error ? <div className="rounded-md border border-[#f0cccc] bg-[#fff0f0] p-[10px_12px] text-base leading-[1.5] text-[#b83232]" role="alert">{error}</div> : null}
          <Button type="submit" className="mt-0.5 w-full" loading={submitting}>{submitting ? <><span className="inline-flex [animation:spin_0.7s_linear_infinite]" aria-hidden="true"><LoaderCircle size={16}/></span>处理中…</> : bootstrapRequired ? "创建管理员并进入" : "登录"}</Button>
        </form>
        <p className="mt-[18px] mb-0 text-center text-sm leading-[1.6] text-[#929aac]">会话令牌只保存在当前页面内存中，刷新页面后需要重新登录。</p>
      </section>
    </main>
  );
}
