import { Bug, ClipboardCheck, Database, LayoutDashboard, MessageSquareText, Plug, SearchCheck, Settings, ShieldCheck, Users, type LucideIcon } from "lucide-react";
import { ListItemButton } from "./ui/ListItemButton";

export type AppPage = "overview" | "knowledge-bases" | "data-sources" | "chat" | "evaluation-center" | "bad-cases" | "acceptance" | "system" | "members" | "audit";

interface AppNavigationProps { page: AppPage; onNavigate: (path: string) => void; isAdmin: boolean; }
const ITEMS: Array<{ page: AppPage; path: string; label: string; icon: LucideIcon; group?: string }> = [
  { page: "overview", path: "/overview", label: "概览", icon: LayoutDashboard },
  { page: "chat", path: "/chat", label: "问答工作台", icon: MessageSquareText, group: "应用" },
  { page: "knowledge-bases", path: "/knowledge-bases", label: "知识库管理", icon: Database, group: "知识库管理" },
  { page: "data-sources", path: "/data-sources", label: "数据源管理", icon: Plug },
  { page: "evaluation-center", path: "/evaluation", label: "评测中心", icon: SearchCheck, group: "测评评估" },
  { page: "bad-cases", path: "/evaluation/bad-cases", label: "Bad Case", icon: Bug },
  { page: "acceptance", path: "/evaluation/acceptance", label: "链路验收", icon: ClipboardCheck },
];
const ADMIN_ITEMS: typeof ITEMS = [
  { page: "system", path: "/system", label: "系统状态", icon: Settings, group: "管理配置" },
  { page: "members", path: "/settings/members", label: "成员与权限", icon: Users },
  { page: "audit", path: "/settings/audit", label: "审计记录", icon: ShieldCheck },
];
export function AppNavigation({ page, onNavigate, isAdmin }: AppNavigationProps) {
  const items = isAdmin ? [...ITEMS, ...ADMIN_ITEMS] : ITEMS;
  return (
    <nav
      className="flex-1 block pt-1 px-1.5 pb-2.5 overflow-y-auto min-[768px]:max-[1025px]:[padding:14px_9px] max-[768px]:flex max-[768px]:gap-0.5 max-[768px]:[padding:5px_6px] max-[768px]:overflow-x-auto max-[768px]:overflow-y-hidden max-[768px]:[scrollbar-width:none] max-[768px]:[&::-webkit-scrollbar]:hidden"
      aria-label="主导航"
    >
      {items.map((item, index) => {
        const active = page === item.page;
        return (
          <div className="grid min-[1025px]:mb-0.5 max-[768px]:flex-[0_0_48px]" key={item.page}>
            {item.group ? (
              <span
                className={
                  "hidden min-[1181px]:block min-[1181px]:mx-[7px] min-[1181px]:mb-[3px] min-[1181px]:text-[10px] min-[1181px]:font-semibold min-[1181px]:text-[#9ba2b5] " +
                  (index === 1 ? "min-[1181px]:mt-2" : "min-[1181px]:mt-2.5")
                }
              >
                {item.group}
              </span>
            ) : null}
            <ListItemButton
              active={active}
              aria-current="page"
              aria-label={item.label}
              onClick={() => onNavigate(item.path)}
              className={
                "w-full h-[38px] flex items-center gap-1.5 rounded-[7px] px-0 py-0 text-left text-base " +
                "transition-[color,background-color,border-color,transform] duration-[160ms] ease " +
                "min-[1025px]:px-[7px] max-[1181px]:justify-center max-[768px]:h-11 max-[768px]:min-w-[48px] " +
                (active
                  ? "text-[#4e40cd] bg-[#eeebff] font-semibold"
                  : "text-[#555e73] hover:text-[#4137b9] hover:bg-[#f6f4ff]")
              }
            >
              <item.icon
                className={
                  "min-[821px]:max-[1025px]:w-5 w-[18px] text-center " + (active ? "text-[#594bd6]" : "text-[#777f96]")
                }
                aria-hidden="true"
                size={18}
              />
              <span className="hidden min-[1181px]:inline">{item.label}</span>
            </ListItemButton>
          </div>
        );
      })}
    </nav>
  );
}
