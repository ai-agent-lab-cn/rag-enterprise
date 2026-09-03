import { useEffect, useState, type ReactNode } from "react";
import { Activity, Database, HardDriveDownload, Server } from "lucide-react";
import { api } from "../api";
import type { HealthStatus, ReadinessStatus, SystemMetrics } from "../types";
import { TopbarPortal } from "./TopbarPortal";
import { Badge } from "./ui/Badge";
import { ErrorBanner } from "./ui/ErrorBanner";

/**
 * 系统状态的指标卡**不复用 `ui/MetricCard`**：向量集合/生成能力两张卡靠
 * `<small title={...}>` 承载完整模型名的悬浮提示（型号名经常超长，需要单行截断 +
 * hover 才能看全），而 `MetricCard` 的 `note` 只接受纯字符串、渲染时不截断也不接受
 * `title`，套用会直接丢失这个悬浮交互——违反本次迁移「不改交互流程」的边界。
 * 图标底色改为中性（不再是 --brand-soft 底色）以外的其余视觉与 MetricCard 一致。
 */
function MetricArticle({ icon, label, value, note, noteTitle }: { icon: ReactNode; label: string; value: ReactNode; note: ReactNode; noteTitle?: string }) {
  return (
    <article className="grid min-w-0 min-h-[126px] min-[1025px]:min-h-[108px] grid-cols-[auto_minmax(0,1fr)] content-center gap-[6px_10px] rounded-xl border border-line bg-surface p-[18px] min-[1025px]:p-[15px] [&>svg]:row-span-2 [&>svg]:h-9 [&>svg]:w-9 [&>svg]:rounded-[9px] [&>svg]:bg-brand-subtle [&>svg]:p-2 [&>svg]:text-brand">
      {icon}
      <span className="overflow-hidden text-ellipsis whitespace-nowrap text-sm text-[#7c8599]">{label}</span>
      <strong className="text-xl">{value}</strong>
      <small title={noteTitle} className="col-start-2 line-clamp-2 whitespace-normal overflow-hidden text-ellipsis text-sm leading-[1.35] text-[#7c8599] [overflow-wrap:anywhere]">{note}</small>
    </article>
  );
}

export function SystemPage() {
  const [data, setData] = useState<{
    health: HealthStatus;
    ready: ReadinessStatus;
    metrics: SystemMetrics;
  } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    Promise.all([api.health(), api.readiness(), api.systemMetrics()])
      .then(([health, ready, metrics]) => {
        if (active) setData({ health, ready, metrics });
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "系统状态读取失败");
      });
    return () => {
      active = false;
    };
  }, []);
  return (
    <section className="w-auto max-w-[1440px] mx-auto p-[26px_24px_52px] max-[768px]:p-[20px_14px_36px] min-[1025px]:p-[20px_20px_40px]" aria-label="系统状态">
      <TopbarPortal>
        {data ? (
          <Badge shape="status" tone={data.ready.status === "ready" ? "success" : "danger"}>
            {data.ready.status === "ready" ? "服务已就绪" : "服务未就绪"}
          </Badge>
        ) : null}
      </TopbarPortal>
      {error ? (
        <ErrorBanner>{error}</ErrorBanner>
      ) : null}
      {!data && !error ? (
        <div className="grid min-h-[260px] place-items-center rounded-xl border border-dashed border-line bg-surface text-[#7b8498]" role="status">
          正在读取系统状态…
        </div>
      ) : null}
      {data ? (
        <>
          <div className="grid grid-cols-4 gap-3 min-[768px]:max-[1001px]:grid-cols-2 max-[768px]:grid-cols-1">
            <MetricArticle icon={<Server />} label="API 状态" value={data.health.status === "ok" ? "正常" : data.health.status} note={`当前服务版本 ${data.health.version}`} />
            <MetricArticle icon={<Database />} label="向量集合" value={data.health.collection_ready ? "可用" : "不可用"} note={data.health.models.embedding} noteTitle={data.health.models.embedding} />
            <MetricArticle icon={<Activity />} label="生成能力" value={data.health.generation_ready ? "已配置" : "仅检索"} note={data.health.models.generation} noteTitle={data.health.models.generation} />
            <MetricArticle icon={<HardDriveDownload />} label="恢复边界" value="隔离恢复" note="不覆盖现有数据" />
          </div>
          <div className="grid grid-cols-2 gap-3.5 min-[1025px]:gap-[11px] mt-3.5 min-[1025px]:mt-[11px] max-[768px]:grid-cols-1">
            <section className="min-w-0 rounded-xl border border-line bg-surface p-5 min-[1025px]:p-4">
              <h2 className="m-0 mb-4 text-[16px]">就绪检查</h2>
              <div className="grid gap-0 m-0">
                {Object.entries(data.ready.checks).map(([name, value]) => (
                  <div key={name} className="min-w-0 flex justify-between gap-4 border-t border-divider py-3 first:border-t-0">
                    <span className="text-base text-[#697287]">{name.replaceAll("_", " ")}</span>
                    <b className={value === "ok" ? "text-success" : "text-danger-text"}>{value === "ok" ? "通过" : "失败"}</b>
                  </div>
                ))}
              </div>
            </section>
            <section className="min-w-0 rounded-xl border border-line bg-surface p-5 min-[1025px]:p-4">
              <h2 className="m-0 mb-4 text-[16px]">模型配置</h2>
              <dl className="grid gap-0 m-0">
                {Object.entries(data.health.models).map(([name, value]) => (
                  <div key={name} className="min-w-0 flex justify-between gap-4 border-t border-divider py-3 first:border-t-0">
                    <dt className="text-base text-[#697287]">{name}</dt>
                    <dd className="max-w-[70%] overflow-hidden text-ellipsis whitespace-nowrap m-0 text-base" title={value}>{value}</dd>
                  </div>
                ))}
              </dl>
            </section>
            <section className="min-w-0 rounded-xl border border-line bg-surface p-5 min-[1025px]:p-4 col-span-2 max-[768px]:col-auto">
              <h2 className="m-0 mb-4 text-[16px]">运行指标</h2>
              <p className="mt-[-8px] mb-4 text-base text-[#70798f]">
                生成于 {new Date(data.metrics.generated_at).toLocaleString("zh-CN")}
                ；进程重启后重新统计，不代表正式流量 SLO。
              </p>
              <div className="grid grid-cols-3 gap-3 max-[768px]:grid-cols-1">
                <section className="min-w-0 rounded-md bg-[#f8f9fc] p-3">
                  <h3 className="m-0 mb-2 text-base">请求</h3>
                  <pre className="max-h-[220px] overflow-auto m-0 text-sm text-[#4d566c] whitespace-pre-wrap">{JSON.stringify(data.metrics.requests, null, 2)}</pre>
                </section>
                <section className="min-w-0 rounded-md bg-[#f8f9fc] p-3">
                  <h3 className="m-0 mb-2 text-base">RAG</h3>
                  <pre className="max-h-[220px] overflow-auto m-0 text-sm text-[#4d566c] whitespace-pre-wrap">{JSON.stringify(data.metrics.rag, null, 2)}</pre>
                </section>
                <section className="min-w-0 rounded-md bg-[#f8f9fc] p-3">
                  <h3 className="m-0 mb-2 text-base">索引</h3>
                  <pre className="max-h-[220px] overflow-auto m-0 text-sm text-[#4d566c] whitespace-pre-wrap">{JSON.stringify(data.metrics.indexing, null, 2)}</pre>
                </section>
              </div>
            </section>
          </div>
        </>
      ) : null}
    </section>
  );
}
