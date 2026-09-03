import { useEffect, useState, type FormEvent } from "react";
import { Plus, Shield, UserRound } from "lucide-react";
import { api } from "../api";
import type { KnowledgeBase, User } from "../types";
import { TopbarPortal } from "./TopbarPortal";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Column, DataTable } from "./ui/DataTable";
import { Dialog, DialogActions } from "./ui/Dialog";
import { ErrorBanner } from "./ui/ErrorBanner";
import { Input } from "./ui/Input";
import { RowAction, RowActions } from "./ui/RowActions";
import { Select } from "./ui/Select";
import { Toolbar } from "./ui/Toolbar";
import { useConfirm } from "./ui/useConfirm";
import { useToast } from "./ui/Toast";

export function MembersPage({ currentUser }: { currentUser: User }) {
  const [members, setMembers] = useState<User[] | null>(null);
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [grants, setGrants] = useState<Record<string, Set<string>>>({});
  const [selectedBase, setSelectedBase] = useState("");
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState("");
  const { confirm, dialog: confirmDialog } = useConfirm();
  const toast = useToast();
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
    const displayName = String(form.get("display_name"));
    setBusy("create");
    setError("");
    try {
      await api.createMember(String(form.get("username")), displayName, String(form.get("password")), String(form.get("role")) as User["role"]);
      setCreating(false);
      toast.success(`已创建成员「${displayName}」`);
      await load();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "成员创建失败";
      setError(message);
      toast.error(message);
    } finally {
      setBusy("");
    }
  };
  const confirmRoleChange = (member: User) => {
    const nextRole: User["role"] = member.role === "admin" ? "member" : "admin";
    confirm({
      title: "变更成员角色",
      consequence: `即将把「${member.display_name}」设为${nextRole === "admin" ? "管理员" : "普通成员"}，会话可能随之失效，需要重新登录。`,
      confirmLabel: "确认变更",
      onConfirm: async () => {
        setBusy(member.user_id);
        try {
          await api.updateMember(member.user_id, { role: nextRole });
          toast.success(`已将「${member.display_name}」设为${nextRole === "admin" ? "管理员" : "普通成员"}`);
          await load();
        } catch (reason) {
          toast.error(reason instanceof Error ? reason.message : "成员更新失败");
          throw reason;
        } finally {
          setBusy("");
        }
      },
    });
  };
  const confirmToggleActive = (member: User) => {
    const nextActive = !member.active;
    confirm({
      title: `${nextActive ? "启用" : "停用"}成员`,
      consequence: nextActive
        ? `即将启用「${member.display_name}」，其账号可重新登录并访问已授权的知识库。`
        : `即将停用「${member.display_name}」，其会话会立即失效且无法再登录。`,
      confirmLabel: "确认变更",
      tone: nextActive ? "default" : "destructive",
      onConfirm: async () => {
        setBusy(member.user_id);
        try {
          await api.updateMember(member.user_id, { active: nextActive });
          toast.success(`已${nextActive ? "启用" : "停用"}「${member.display_name}」`);
          await load();
        } catch (reason) {
          toast.error(reason instanceof Error ? reason.message : "成员更新失败");
          throw reason;
        } finally {
          setBusy("");
        }
      },
    });
  };
  const confirmRevoke = (member: User) => {
    confirm({
      title: "撤销知识库权限",
      consequence: `即将撤销「${member.display_name}」对该知识库的访问权限，撤销后其无法再检索或查看该知识库中的资料。`,
      confirmLabel: "确认撤销",
      tone: "destructive",
      onConfirm: async () => {
        try {
          await api.revokeKnowledgeBaseMember(selectedBase, member.user_id);
          const users = await api.listKnowledgeBaseMembers(selectedBase);
          setGrants((value) => ({
            ...value,
            [selectedBase]: new Set(users.map((user) => user.user_id)),
          }));
          toast.success(`已撤销「${member.display_name}」的知识库权限`);
        } catch (reason) {
          toast.error(reason instanceof Error ? reason.message : "授权更新失败");
          throw reason;
        }
      },
    });
  };
  const toggleGrant = async (member: User) => {
    if (!selectedBase) return;
    const granted = grants[selectedBase]?.has(member.user_id);
    if (granted) {
      confirmRevoke(member);
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
      setError("");
      toast.success(`已授权「${member.display_name}」访问该知识库`);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "授权更新失败";
      setError(message);
      toast.error(message);
    } finally {
      setBusy("");
    }
  };
  const rowActions = (member: User): RowAction[] => {
    const isSelf = member.user_id === currentUser.user_id;
    const blockedReason = isSelf ? "不能修改自己的账号" : undefined;
    return [
      {
        label: member.role === "admin" ? "设为成员" : "设为管理员",
        blockedReason,
        onSelect: () => confirmRoleChange(member),
      },
      {
        label: member.active ? "停用" : "启用",
        tone: member.active ? "destructive" : "default",
        blockedReason,
        onSelect: () => confirmToggleActive(member),
      },
    ];
  };
  const columns: Column<User>[] = [
    {
      key: "member", header: "成员", width: "220px", truncate: false,
      render: (member) => (
        <span className="flex min-w-0 items-center gap-2">
          <span className="w-[34px] h-[34px] grid flex-none place-items-center rounded-[9px] text-brand bg-brand-subtle">
            <UserRound size={16} />
          </span>
          <span className="min-w-0">
            <strong className="block truncate font-medium text-ink">
              {member.display_name}
              {member.user_id === currentUser.user_id ? "（当前账号）" : ""}
            </strong>
            <small className="block truncate text-ink-faint">@{member.username}</small>
          </span>
        </span>
      ),
    },
    {
      key: "role", header: "角色", width: "110px",
      render: (member) => (
        <Badge shape="type" tone={member.role === "admin" ? "brand" : "neutral"}>
          <Shield size={13} />
          {member.role === "admin" ? "管理员" : "普通成员"}
        </Badge>
      ),
    },
    {
      key: "status", header: "状态", width: "90px",
      render: (member) => (
        <Badge shape="status" tone={member.active ? "success" : "neutral"}>
          {member.active ? "已启用" : "已停用"}
        </Badge>
      ),
    },
    {
      key: "access", header: "知识库权限", width: "140px", truncate: false,
      render: (member) => {
        // 管理员天然有全部权限，这里给一个永远点不动的开关只是噪音——
        // 不可用的控件不如不给控件。原因由工具栏那句说明承担。
        if (member.role === "admin") {
          return <span className="text-sm text-ink-faint">全部知识库</span>;
        }
        const granted = grants[selectedBase]?.has(member.user_id);
        return (
          <Button
            variant="outline"
            size="sm"
            className={granted ? "w-fit border-brand/35 bg-brand-subtle text-brand" : "w-fit"}
            blockedReason={selectedBase ? undefined : "请先选择知识库"}
            loading={busy === `grant-${member.user_id}`}
            aria-pressed={Boolean(granted)}
            onClick={() => void toggleGrant(member)}
          >
            {granted ? "已授权" : "未授权"}
          </Button>
        );
      },
    },
    {
      key: "actions", header: "操作", width: "180px", align: "right", truncate: false,
      render: (member) => <RowActions rowLabel={member.display_name} actions={rowActions(member)} />,
    },
  ];
  return (
    <section className="mx-auto max-w-[1440px] p-[26px_24px_52px] max-[768px]:p-[20px_14px_36px] min-[1025px]:p-[20px_20px_40px]" aria-label="成员与权限">
      <TopbarPortal>
        <Button onClick={() => setCreating(true)}>
          <Plus size={16} />
          新建成员
        </Button>
      </TopbarPortal>
      {/* 弹层打开时错误必须显示在弹层内，否则它躺在 Radix 加了 aria-hidden 的背景里。 */}
      {error && !creating ? (
        <ErrorBanner>{error}</ErrorBanner>
      ) : null}
      <Toolbar
        filters={
          <label className="flex items-center gap-2 text-md">
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
        }
        actions={<p className="text-sm text-ink-muted">管理员可访问全部知识库；下方开关只管理普通成员。</p>}
      />
      <DataTable
        rows={members}
        columns={columns}
        rowKey={(member) => member.user_id}
        label="成员列表"
        emptyState={{ kind: "empty", title: "还没有成员", description: "创建首位成员后可配置知识库权限。" }}
      />
      {creating ? (
        <Dialog open title="新建成员" description="创建后可为普通成员分配知识库权限。" onClose={() => { if (busy !== "create") setCreating(false); }}>
          <form className="grid gap-[9px] pt-[20px] px-[22px]" onSubmit={submitCreate}>
            {error ? <ErrorBanner>{error}</ErrorBanner> : null}
            <label className="text-[#4e576c] text-[13px] font-semibold">
              显示名称
              <Input className="py-[10px]" name="display_name" required maxLength={80} />
            </label>
            <label className="text-[#4e576c] text-[13px] font-semibold">
              用户名
              <Input className="py-[10px]" name="username" required minLength={3} maxLength={64} pattern="[A-Za-z0-9._\-]+" />
            </label>
            <label className="text-[#4e576c] text-[13px] font-semibold">
              初始密码
              <Input className="py-[10px]" name="password" type="password" required minLength={12} maxLength={128} />
            </label>
            <label className="text-[#4e576c] text-[13px] font-semibold">
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
      {confirmDialog}
    </section>
  );
}
