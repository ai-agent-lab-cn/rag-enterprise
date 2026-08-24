#!/usr/bin/env bash
set -euo pipefail

namespace="rag-enterprise"
sample_count="${PILOT_SAMPLE_COUNT:-12}"
sample_interval="${PILOT_SAMPLE_INTERVAL_SECONDS:-28}"
output_root="${PILOT_OUTPUT_ROOT:-artifacts/controlled-pilot}"
policy="${PILOT_POLICY:-config/controlled-pilot.json}"
backend_port="${PILOT_BACKEND_PORT:-18000}"
capacity_pod="rag-pilot-capacity"
pilot_question="${PILOT_QUESTION:-RAG Enterprise 如何保证回答可追溯？}"

if [[ "$(kubectl config current-context 2>/dev/null || true)" != "docker-desktop" ]]; then
  echo "拒绝执行：当前 Kubernetes context 必须是 docker-desktop。" >&2
  exit 2
fi
if [[ -z "${ADMIN_TOKEN:-}" ]]; then
  echo "必须通过 ADMIN_TOKEN 提供受控试运行管理员会话；令牌不会写入证据。" >&2
  exit 2
fi
if ! [[ "$sample_count" =~ ^[0-9]+$ ]] || (( sample_count < 2 )); then
  echo "PILOT_SAMPLE_COUNT 必须是至少 2 的整数。" >&2
  exit 2
fi

mkdir -p "$output_root"
samples_file="${output_root}/samples.jsonl"
report_file="${output_root}/report.json"
: > "$samples_file"

cleanup() {
  if [[ -n "${port_forward_pid:-}" ]]; then
    kill "$port_forward_pid" >/dev/null 2>&1 || true
  fi
  kubectl delete pod/"$capacity_pod" -n "$namespace" --ignore-not-found --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT

kubectl delete pod/"$capacity_pod" -n "$namespace" --ignore-not-found >/dev/null 2>&1 || true
pilot_image="$(kubectl get deployment/rag-backend -n "$namespace" -o jsonpath='{.spec.template.spec.containers[0].image}')"
kubectl apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: ${capacity_pod}
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: rag-enterprise
    app.kubernetes.io/component: pilot-capacity
spec:
  restartPolicy: Never
  securityContext: {runAsNonRoot: true, runAsUser: 999, runAsGroup: 999, fsGroup: 999}
  containers:
    - name: capacity
      image: ${pilot_image}
      imagePullPolicy: Never
      command: ["sh", "-c", "trap : TERM INT; sleep infinity & wait"]
      securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: ["ALL"]}}
      volumeMounts:
        - {name: uploads, mountPath: /volumes/uploads, readOnly: true}
        - {name: backups, mountPath: /volumes/backups, readOnly: true}
  volumes:
    - name: uploads
      persistentVolumeClaim: {claimName: rag-uploads}
    - name: backups
      persistentVolumeClaim: {claimName: rag-backups}
YAML
kubectl wait --for=condition=Ready pod/"$capacity_pod" -n "$namespace" --timeout=120s >/dev/null

kubectl port-forward -n "$namespace" service/backend "${backend_port}:8000" >/dev/null 2>&1 &
port_forward_pid=$!
for _ in $(seq 1 30); do
  curl --silent --fail "http://127.0.0.1:${backend_port}/api/health/ready" >/dev/null && break
  sleep 1
done

storage_to_bytes() {
  uv run python - "$1" <<'PY'
import re, sys
value = sys.argv[1]
match = re.fullmatch(r"([0-9]+)([KMGTPE]i)?", value)
if not match:
    raise SystemExit(f"无法解析存储容量：{value}")
powers = {None: 0, "Ki": 1, "Mi": 2, "Gi": 3, "Ti": 4, "Pi": 5, "Ei": 6}
print(int(match.group(1)) * (1024 ** powers[match.group(2)]))
PY
}

database_total="$(storage_to_bytes "$(kubectl get pvc/rag-postgres-1 -n "$namespace" -o jsonpath='{.spec.resources.requests.storage}')")"
uploads_total="$(storage_to_bytes "$(kubectl get pvc/rag-uploads -n "$namespace" -o jsonpath='{.spec.resources.requests.storage}')")"
backups_total="$(storage_to_bytes "$(kubectl get pvc/rag-backups -n "$namespace" -o jsonpath='{.spec.resources.requests.storage}')")"

for sample_number in $(seq 1 "$sample_count"); do
  curl_result="$(curl --silent --output /dev/null --write-out '%{http_code} %{time_total}' "http://127.0.0.1:${backend_port}/api/health/ready" || true)"
  http_code="${curl_result%% *}"
  latency_seconds="${curl_result#* }"
  PILOT_QUESTION="$pilot_question" uv run python - <<'PY' | curl --silent --show-error --max-time 120 \
    --header "Authorization: Bearer ${ADMIN_TOKEN}" --header "Content-Type: application/json" \
    --data-binary @- "http://127.0.0.1:${backend_port}/api/knowledge-bases/kb_default/query" >/dev/null || true
import json, os
print(json.dumps({"question": os.environ["PILOT_QUESTION"], "retrieve_k": 5, "rerank_k": 3}, ensure_ascii=False))
PY
  metrics_file="$(mktemp)"
  curl --silent --fail --header "Authorization: Bearer ${ADMIN_TOKEN}" \
    "http://127.0.0.1:${backend_port}/api/system/metrics" > "$metrics_file"
  index_values="$(kubectl exec -n "$namespace" rag-postgres-1 -- psql -U postgres -d rag_enterprise -Atc \
    "SELECT count(*) FILTER (WHERE status IN ('succeeded','failed')), count(*) FILTER (WHERE status='failed'), COALESCE(EXTRACT(EPOCH FROM now() - (min(available_at) FILTER (WHERE status='queued')))::bigint, 0) FROM index_jobs;")"
  database_used="$(kubectl exec -n "$namespace" rag-postgres-1 -- psql -U postgres -d rag_enterprise -Atc "SELECT pg_database_size('rag_enterprise');")"
  uploads_used="$(kubectl exec -n "$namespace" "$capacity_pod" -- du -sb /volumes/uploads | awk '{print $1}')"
  backups_used="$(kubectl exec -n "$namespace" "$capacity_pod" -- du -sb /volumes/backups | awk '{print $1}')"
  TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)" HTTP_CODE="$http_code" LATENCY_SECONDS="$latency_seconds" \
    INDEX_VALUES="$index_values" DATABASE_USED="$database_used" DATABASE_TOTAL="$database_total" \
    UPLOADS_USED="$uploads_used" UPLOADS_TOTAL="$uploads_total" BACKUPS_USED="$backups_used" \
    BACKUPS_TOTAL="$backups_total" METRICS_FILE="$metrics_file" uv run python - <<'PY' >> "$samples_file"
import json, os
metrics = json.load(open(os.environ["METRICS_FILE"], encoding="utf-8"))
attempts, failures, oldest = (int(value) for value in os.environ["INDEX_VALUES"].split("|"))
print(json.dumps({
    "timestamp": os.environ["TIMESTAMP"],
    "ready": os.environ["HTTP_CODE"] == "200",
    "ready_latency_ms": round(float(os.environ["LATENCY_SECONDS"]) * 1000, 2),
    "rag": {"queries": metrics["rag"]["queries"], "failures": metrics["rag"]["retrieval_failures"]},
    "indexing": {"attempts": attempts, "failures": failures},
    "oldest_queued_seconds": oldest,
    "capacity": {
        "database": {"used_bytes": int(os.environ["DATABASE_USED"]), "total_bytes": int(os.environ["DATABASE_TOTAL"])},
        "uploads": {"used_bytes": int(os.environ["UPLOADS_USED"]), "total_bytes": int(os.environ["UPLOADS_TOTAL"])},
        "backups": {"used_bytes": int(os.environ["BACKUPS_USED"]), "total_bytes": int(os.environ["BACKUPS_TOTAL"])},
    },
}, ensure_ascii=False))
PY
  rm -f "$metrics_file"
  if (( sample_number < sample_count )); then sleep "$sample_interval"; fi
done

uv run python scripts/controlled_pilot.py --samples "$samples_file" --policy "$policy" --output "$report_file"
echo "受控试运行证据：${report_file}"
