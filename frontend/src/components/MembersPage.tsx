import { useEffect, useState, type FormEvent } from "react";
import { Plus, Shield, UserRound } from "lucide-react";
import { api } from "../api";
import type { KnowledgeBase, User } from "../types";
import { TopbarPortal } from "./TopbarPortal";
import { Button } from "./ui/Button";
import { Dialog, DialogActions } from "./ui/Dialog";
import { Input } from "./ui/Input";
import { Select } from "./ui/Select";

type ConfirmAction = {
  member: User;
  kind: "toggle" | "role" | "revoke";
} | null;

export function MembersPage({ currentUser }: { currentUser: User }) {
  const [members, setMembers] = useState<User[] | null>(null);
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [grants, setGrants] = useState<Record<string, Set<string>>>({});
  const [selectedBase, setSelectedBase] = useState("");
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmAction>(null);
  const [busy, setBusy] = useState("");
  const load = async () => {
    try {
      const [nextMembers, nextBases] = await Promise.all([api.listMembers(), api.listKnowledgeBases()]);
      setMembers(nextMembers);
      setBases(nextBases);
      setSelectedBase((value) => value || nextBases[0]?.knowledge_base_id || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "成员读取失败");
    }
  };
  useEffect(() => {
    let active = true;
    Promise.all([api.listMembers(), api.listKnowledgeBases()])
      .then(([nextMembers, nextBases]) => {
        if (!active) return;
        setMembers(nextMembers);
        setBases(nextBases);
        setSelectedBase(nextBases[0]?.knowledge_base_id || "");
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "成员读取失败");
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!selectedBase) return;
    api
      .listKnowledgeBaseMembers(selectedBase)
      .then((users) =>
        setGrants((value) => ({
          ...value,
          [selectedBase]: new Set(users.map((user) => user.user_id)),
        })),
      )
      .catch((reason) => setError(reason instanceof Error ? reason.message : "授权读取失败"));
  }, [selectedBase]);
  const submitCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy("create");
    setError("");
    try {
      await api.createMember(String(form.get("username")), String(form.get("display_name")), String(form.get("password")), String(form.get("role")) as User["role"]);
      setCreating(false);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "成员创建失败");
    } finally {
      setBusy("");
    }
  };
  const applyConfirm = async () => {
    if (!confirm) return;
    const { member, kind } = confirm;
    setBusy(member.user_id);
    try {
      if (kind === "revoke") {
        await api.revokeKnowledgeBaseMember(selectedBase, member.user_id);
        const users = await api.listKnowledgeBaseMembers(selectedBase);
        setGrants((value) => ({
          ...value,
          [selectedBase]: new Set(users.map((user) => user.user_id)),
        }));
      } else {
        await api.updateMember(member.user_id, kind === "toggle" ? { active: !member.active } : { role: member.role === "admin" ? "member" : "admin" });
        await load();
      }
      setConfirm(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "成员更新失败");
    } finally {
      setBusy("");
    }
  };
  const toggleGrant = async (member: User) => {
    if (!selectedBase) return;
    const granted = grants[selectedBase]?.has(member.user_id);
    if (granted) {
      setConfirm({ member, kind: "revoke" });
      return;
    }
    setBusy(`grant-${member.user_id}`);
    try {
      await api.grantKnowledgeBaseMember(selectedBase, member.user_id);
      const users = await api.listKnowledgeBaseMembers(selectedBase);
      setGrants((value) => ({
        ...value,
        [selectedBase]: new Set(users.map((user) => user.user_id)),
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "授权更新失败");
    } finally {
      setBusy("");
    }
  };
  return (
    <section className="admin-page" aria-label="成员与权限">
      <TopbarPortal>
        <Button onClick={() => setCreating(true)}>
          <Plus size={16} />
          新建成员
        </Button>
      </TopbarPortal>
      {/* 弹层打开时错误必须显示在弹层内，否则它躺在 Radix 加了 aria-hidden 的背景里。 */}
      {error && !creating && !confirm ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}
      <section className="permission-toolbar">
        <label>
          授权知识库
          <Select
            size="sm"
            className="w-56"
            blockedReason={bases.length ? undefined : "还没有知识库"}
            value={selectedBase}
            onChange={(event) => setSelectedBase(event.target.value)}
          >
            {bases.map((base) => (
              <option key={base.knowledge_base_id} value={base.knowledge_base_id}>
                {base.name}
              </option>
            ))}
          </Select>
        </label>
        <p>管理员可访问全部知识库；下方开关只管理普通成员。</p>
      </section>
      {members === null && !error ? (
        <div className="admin-loading" role="status">
          正在读取成员…
        </div>
      ) : null}
      {members?.length === 0 ? (
        <div className="admin-state">
          <UserRound />
          <h2>还没有成员</h2>
          <p>创建首位成员后可配置知识库权限。</p>
        </div>
      ) : null}
      {members?.length ? (
        <div className="member-table" role="table" aria-label="成员列表">
          <div className="member-row member-head" role="row">
            <span>成员</span>
            <span>角色</span>
            <span>状态</span>
            <span>知识库权限</span>
            <span>操作</span>
          </div>
          {members.map((member) => {
            const isSelf = member.user_id === currentUser.user_id;
            const granted = member.role === "admin" || grants[selectedBase]?.has(member.user_id);
            return (
              <div className="member-row" role="row" key={member.user_id}>
                <div className="member-identity">
                  <span className="member-avatar">
                    <UserRound size={16} />
                  </span>
                  <span>
                    <strong>
                      {member.display_name}
                      {isSelf ? "（当前账号）" : ""}
                    </strong>
                    <small>@{member.username}</small>
                  </span>
                </div>
                <span className="role-badge">
                  <Shield size={13} />
                  {member.role === "admin" ? "管理员" : "普通成员"}
                </span>
                <span className={`status-pill ${member.active ? "is-success" : "is-muted"}`}>{member.active ? "已启用" : "已停用"}</span>
                {/* 管理员天然有全部权限，这里给一个永远点不动的开关只是噪音——
                    不可用的控件不如不给控件。原因由工具栏那句说明承担。 */}
                {member.role === "admin" ? (
                  <span className="text-xs text-ink-faint">全部知识库</span>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    reasonHidden
                    // w-fit：它直接是 .member-row 这个 grid 的子项，不加会被拉满整列。
                    className={granted ? "w-fit border-brand/35 bg-brand-subtle text-brand" : "w-fit"}
                    blockedReason={selectedBase ? undefined : "请先选择知识库"}
                    loading={busy === `grant-${member.user_id}`}
                    aria-pressed={Boolean(granted)}
                    onClick={() => void toggleGrant(member)}
                  >
                    {granted ? "已授权" : "未授权"}
                  </Button>
                )}
                <div className="row-actions">
                  {/* reasonHidden：同一行的名字后面已经标了「（当前账号）」，
                      再逐行补一句会把 72px 的行撑高。 */}
                  <Button variant="ghost" size="sm" reasonHidden blockedReason={isSelf ? "不能修改自己的账号" : undefined} loading={busy === member.user_id} onClick={() => setConfirm({ member, kind: "role" })}>
                    {member.role === "admin" ? "设为成员" : "设为管理员"}
                  </Button>
                  <Button variant="ghost" size="sm" reasonHidden className={member.active ? "text-danger-text hover:bg-danger-subtle" : undefined} blockedReason={isSelf ? "不能修改自己的账号" : undefined} loading={busy === member.user_id} onClick={() => setConfirm({ member, kind: "toggle" })}>
                    {member.active ? "停用" : "启用"}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      ) : null}
      {creating ? (
        <Dialog open title="新建成员" description="创建后可为普通成员分配知识库权限。" onClose={() => { if (busy !== "create") setCreating(false); }}>
          <form className="modal-form" onSubmit={submitCreate}>
            {error ? <div className="error-banner" role="alert">{error}</div> : null}
            <label>
              显示名称
              <Input name="display_name" required maxLength={80} />
            </label>
            <label>
              用户名
              <Input name="username" required minLength={3} maxLength={64} pattern="[A-Za-z0-9._\-]+" />
            </label>
            <label>
              初始密码
              <Input name="password" type="password" required minLength={12} maxLength={128} />
            </label>
            <label>
              角色
              <Select name="role" defaultValue="member">
                <option value="member">普通成员</option>
                <option value="admin">管理员</option>
              </Select>
            </label>
            <DialogActions>
              <Button variant="secondary" loading={busy === "create"} onClick={() => setCreating(false)}>取消</Button>
              <Button type="submit" loading={busy === "create"}>确认创建</Button>
            </DialogActions>
          </form>
        </Dialog>
      ) : null}
      {confirm ? (
        <Dialog open title={confirm.kind === "revoke" ? "撤销知识库权限" : confirm.kind === "toggle" ? `${confirm.member.active ? "停用" : "启用"}成员` : "变更成员角色"} description={`即将修改 ${confirm.member.display_name} 的访问权限，会话可能随之失效。`} onClose={() => { if (!busy) setConfirm(null); }}>
          {error ? <div className="error-banner" role="alert">{error}</div> : null}
          <DialogActions>
            <Button variant="secondary" loading={busy !== ""} onClick={() => setConfirm(null)}>取消</Button>
            <Button
              variant={confirm.kind === "revoke" || (confirm.kind === "toggle" && confirm.member.active) ? "destructive" : "primary"}
              autoFocus
              loading={busy !== ""}
              onClick={() => void applyConfirm()}
            >
              确认变更
            </Button>
          </DialogActions>
        </Dialog>
      ) : null}
    </section>
  );
}
