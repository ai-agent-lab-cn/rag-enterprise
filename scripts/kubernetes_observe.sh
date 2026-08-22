#!/usr/bin/env bash
set -euo pipefail

namespace="rag-enterprise"
current_context="$(kubectl config current-context 2>/dev/null || true)"
if [[ "$current_context" != "docker-desktop" ]]; then
  echo "拒绝读取：当前 Kubernetes context 为 '${current_context:-未配置}'，必须是 docker-desktop。" >&2
  exit 2
fi

echo "== 工作负载可用性 =="
kubectl get deployments,pods -n "$namespace"

echo "== 索引任务状态与积压 =="
kubectl exec -n "$namespace" rag-postgres-1 -- psql -U postgres -d rag_enterprise -Atc \
  "SELECT status, count(*), COALESCE(EXTRACT(EPOCH FROM now() - min(available_at))::bigint, 0) AS oldest_seconds FROM index_jobs GROUP BY status ORDER BY status;"

echo "== 数据库容量（字节） =="
kubectl exec -n "$namespace" rag-postgres-1 -- psql -U postgres -d rag_enterprise -Atc \
  "SELECT pg_database_size('rag_enterprise');"

echo "== 原始文件卷容量 =="
kubectl exec -n "$namespace" deployment/rag-backend -- df -Pk /app/data/uploads

if [[ -n "${ADMIN_TOKEN:-}" ]]; then
  echo "== API 请求、检索与索引进程指标 =="
  kubectl port-forward -n "$namespace" service/backend 18000:8000 >/dev/null 2>&1 &
  port_forward_pid=$!
  trap 'kill "$port_forward_pid" 2>/dev/null || true' EXIT
  sleep 1
  python -c 'import json, os, urllib.request; request=urllib.request.Request("http://127.0.0.1:18000/api/system/metrics", headers={"Authorization": "Bearer " + os.environ["ADMIN_TOKEN"]}); print(json.dumps(json.load(urllib.request.urlopen(request, timeout=5)), ensure_ascii=False, indent=2))'
else
  echo "未提供 ADMIN_TOKEN；跳过管理员进程指标。"
fi
