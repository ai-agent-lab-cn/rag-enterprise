import { useEffect, useState } from "react";
import { Activity, Database, HardDriveDownload, Server } from "lucide-react";
import { api } from "../api";
import type { HealthStatus, ReadinessStatus, SystemMetrics } from "../types";
import { TopbarPortal } from "./TopbarPortal";

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
    <section className="admin-page" aria-label="系统状态">
      <TopbarPortal>{data ? <span className={`status-pill ${data.ready.status === "ready" ? "is-success" : "is-danger"}`}>{data.ready.status === "ready" ? "服务已就绪" : "服务未就绪"}</span> : null}</TopbarPortal>
      {error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}
      {!data && !error ? (
        <div className="admin-loading" role="status">
          正在读取系统状态…
        </div>
      ) : null}
      {data ? (
        <>
          <div className="admin-metrics">
            <article>
              <Server />
              <span>API 状态</span>
              <strong>{data.health.status === "ok" ? "正常" : data.health.status}</strong>
              <small>当前服务版本 {data.health.version}</small>
            </article>
            <article>
              <Database />
              <span>向量集合</span>
              <strong>{data.health.collection_ready ? "可用" : "不可用"}</strong>
              <small>{data.health.models.embedding}</small>
            </article>
            <article>
              <Activity />
              <span>生成能力</span>
              <strong>{data.health.generation_ready ? "已配置" : "仅检索"}</strong>
              <small>{data.health.models.generation}</small>
            </article>
            <article>
              <HardDriveDownload />
              <span>恢复边界</span>
              <strong>隔离恢复</strong>
              <small>不覆盖现有数据</small>
            </article>
          </div>
          <div className="admin-grid">
            <section className="admin-panel">
              <h2>就绪检查</h2>
              <div className="check-list">
                {Object.entries(data.ready.checks).map(([name, value]) => (
                  <div key={name}>
                    <span>{name.replaceAll("_", " ")}</span>
                    <b className={value === "ok" ? "status-pass" : "status-fail"}>{value === "ok" ? "通过" : "失败"}</b>
                  </div>
                ))}
              </div>
            </section>
            <section className="admin-panel">
              <h2>模型配置</h2>
              <dl className="model-list">
                {Object.entries(data.health.models).map(([name, value]) => (
                  <div key={name}>
                    <dt>{name}</dt>
                    <dd title={value}>{value}</dd>
                  </div>
                ))}
              </dl>
            </section>
            <section className="admin-panel admin-panel-wide">
              <h2>运行指标</h2>
              <p className="panel-note">
                生成于 {new Date(data.metrics.generated_at).toLocaleString("zh-CN")}
                ；进程重启后重新统计，不代表正式流量 SLO。
              </p>
              <div className="metric-json">
                <section>
                  <h3>请求</h3>
                  <pre>{JSON.stringify(data.metrics.requests, null, 2)}</pre>
                </section>
                <section>
                  <h3>RAG</h3>
                  <pre>{JSON.stringify(data.metrics.rag, null, 2)}</pre>
                </section>
                <section>
                  <h3>索引</h3>
                  <pre>{JSON.stringify(data.metrics.indexing, null, 2)}</pre>
                </section>
              </div>
            </section>
          </div>
        </>
      ) : null}
    </section>
  );
}
