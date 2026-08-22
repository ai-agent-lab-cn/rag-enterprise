#!/usr/bin/env bash
set -euo pipefail

expected_context="docker-desktop"
namespace="rag-enterprise"
operator_version="1.30.0"
manifest_root="deploy/kubernetes"

require_local_context() {
  current_context="$(kubectl config current-context 2>/dev/null || true)"
  if [[ "$current_context" != "$expected_context" ]]; then
    echo "拒绝操作：当前 Kubernetes context 为 '${current_context:-未配置}'，必须是 ${expected_context}。" >&2
    exit 2
  fi
}

case "${1:-}" in
  validate)
    python -m scripts.validate_kubernetes
    ;;
  install-operator)
    require_local_context
    kubectl apply --server-side -f "https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v${operator_version}/cnpg-${operator_version}.yaml"
    kubectl rollout status deployment/cnpg-controller-manager -n cnpg-system --timeout=180s
    ;;
  build)
    require_local_context
    docker build --tag rag-enterprise-backend:issue-84 --file backend/Dockerfile .
    docker build --tag rag-enterprise-frontend:issue-84 frontend
    node_name="$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')"
    docker save rag-enterprise-backend:issue-84 rag-enterprise-frontend:issue-84 | \
      docker exec --interactive "$node_name" ctr --namespace k8s.io images import -
    ;;
  deploy)
    require_local_context
    kubectl apply -f "${manifest_root}/namespace.yaml"
    if [[ -f "${manifest_root}/secret.yaml" ]]; then
      kubectl apply -f "${manifest_root}/secret.yaml"
    elif ! kubectl get secret/rag-enterprise-secrets -n "$namespace" >/dev/null 2>&1; then
      echo "请先创建 rag-enterprise-secrets，或复制 secret.example.yaml 为 secret.yaml。" >&2
      exit 2
    fi
    kubectl apply -f "${manifest_root}/configmap.yaml"
    kubectl apply -f "${manifest_root}/storage.yaml"
    kubectl apply -f "${manifest_root}/postgres.yaml"
    kubectl apply -f "${manifest_root}/network-policy.yaml"
    kubectl wait --for=condition=Ready cluster/rag-postgres -n "$namespace" --timeout=600s
    kubectl delete job/rag-database-migrate -n "$namespace" --ignore-not-found
    kubectl apply -f "${manifest_root}/workloads.yaml"
    kubectl wait --for=condition=complete job/rag-database-migrate -n "$namespace" --timeout=180s
    kubectl rollout status deployment/rag-backend -n "$namespace" --timeout=600s
    kubectl rollout status deployment/rag-worker -n "$namespace" --timeout=600s
    kubectl rollout status deployment/rag-frontend -n "$namespace" --timeout=180s
    ;;
  status)
    require_local_context
    kubectl get cluster,pods,services,pvc,jobs -n "$namespace"
    ;;
  backup)
    require_local_context
    backup_id="$(date -u +%Y%m%dt%H%M%Sz)"
    sed "s/BACKUP_ID/${backup_id}/g" "${manifest_root}/backup-job.example.yaml" | kubectl create -f -
    ;;
  restore-drill)
    require_local_context
    backup_job_name="$(kubectl get jobs -n "$namespace" -l app.kubernetes.io/component=backup --sort-by=.metadata.creationTimestamp -o name | tail -1)"
    if [[ -z "$backup_job_name" ]]; then
      echo "没有可用于恢复演练的备份 Job。" >&2
      exit 2
    fi
    backup_id="${backup_job_name#job.batch/rag-postgres-backup-}"
    restore_database="rag_restore_drill"
    cleanup_restore() {
      kubectl exec -n "$namespace" rag-postgres-1 -- dropdb -U postgres --if-exists --force "$restore_database" >/dev/null 2>&1 || true
      kubectl delete secret/rag-restore-drill-secrets -n "$namespace" --ignore-not-found >/dev/null 2>&1 || true
    }
    trap cleanup_restore EXIT
    cleanup_restore
    kubectl exec -n "$namespace" rag-postgres-1 -- createdb -U postgres -O rag "$restore_database"
    kubectl exec -n "$namespace" rag-postgres-1 -- psql -U postgres -d "$restore_database" -v ON_ERROR_STOP=1 \
      -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null
    database_password="$(kubectl get secret/rag-enterprise-secrets -n "$namespace" -o jsonpath='{.data.password}' | base64 --decode)"
    restore_url="postgresql://rag:${database_password}@rag-postgres-rw:5432/${restore_database}"
    kubectl create secret generic rag-restore-drill-secrets -n "$namespace" \
      --from-literal=DATABASE_URL="$restore_url" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    sed "s/BACKUP_ID/${backup_id}/g" "${manifest_root}/restore-job.example.yaml" | kubectl create -f -
    restore_job_name="job/rag-postgres-restore-${backup_id}"
    kubectl wait --for=condition=complete "$restore_job_name" -n "$namespace" --timeout=300s
    kubectl logs -n "$namespace" "$restore_job_name"
    source_counts="$(kubectl exec -n "$namespace" rag-postgres-1 -- psql -U postgres -d rag_enterprise -Atc "SELECT count(*) FROM knowledge_bases; SELECT count(*) FROM documents; SELECT count(*) FROM index_jobs;")"
    restored_counts="$(kubectl exec -n "$namespace" rag-postgres-1 -- psql -U postgres -d "$restore_database" -Atc "SELECT count(*) FROM knowledge_bases; SELECT count(*) FROM documents; SELECT count(*) FROM index_jobs;")"
    if [[ "$source_counts" != "$restored_counts" ]]; then
      echo "隔离恢复后的核心数据计数不一致。" >&2
      exit 1
    fi
    echo "隔离恢复与核心数据计数校验通过。"
    ;;
  drill)
    require_local_context
    if [[ "${2:-}" != "--confirm-local-restart" ]]; then
      echo "演练会依次重启本地 API、Worker 和单实例数据库；请追加 --confirm-local-restart。" >&2
      exit 2
    fi
    before_counts="$(kubectl exec -n "$namespace" rag-postgres-1 -- psql -U postgres -d rag_enterprise -Atc "SELECT count(*) FROM documents; SELECT count(*) FROM index_jobs;")"
    kubectl delete pod -n "$namespace" -l app.kubernetes.io/component=backend
    kubectl rollout status deployment/rag-backend -n "$namespace" --timeout=600s
    kubectl delete pod -n "$namespace" -l app.kubernetes.io/component=worker
    kubectl rollout status deployment/rag-worker -n "$namespace" --timeout=600s
    kubectl delete pod -n "$namespace" rag-postgres-1
    kubectl wait --for=condition=Ready cluster/rag-postgres -n "$namespace" --timeout=600s
    kubectl rollout status deployment/rag-backend -n "$namespace" --timeout=600s
    after_counts="$(kubectl exec -n "$namespace" rag-postgres-1 -- psql -U postgres -d rag_enterprise -Atc "SELECT count(*) FROM documents; SELECT count(*) FROM index_jobs;")"
    if [[ "$before_counts" != "$after_counts" ]]; then
      echo "重启前后数据计数不一致。" >&2
      exit 1
    fi
    kubectl exec -n "$namespace" rag-postgres-1 -- psql -U postgres -d rag_enterprise -Atc "SELECT status, count(*) FROM index_jobs GROUP BY status ORDER BY status;"
    echo "本地重启与数据一致性演练通过。"
    ;;
  *)
    echo "用法：$0 {validate|install-operator|build|deploy|status|backup|restore-drill|drill --confirm-local-restart}" >&2
    exit 2
    ;;
esac
