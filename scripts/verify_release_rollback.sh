#!/usr/bin/env bash
set -euo pipefail

: "${CURRENT_BACKEND_IMAGE:?CURRENT_BACKEND_IMAGE is required}"
: "${CURRENT_FRONTEND_IMAGE:?CURRENT_FRONTEND_IMAGE is required}"
: "${PREVIOUS_BACKEND_IMAGE:?PREVIOUS_BACKEND_IMAGE is required}"
: "${PREVIOUS_FRONTEND_IMAGE:?PREVIOUS_FRONTEND_IMAGE is required}"

release_root="$(mktemp -d /tmp/rongrag-release.XXXXXX)"
compose_project="rongrag-release-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
compose_file="docker-compose.release.yml"
evidence_path="${ROLLBACK_EVIDENCE_PATH:-$release_root/rollback-evidence.json}"
started_at="$(date +%s)"

cleanup() {
  docker compose --project-name "$compose_project" --file "$compose_file" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
diagnose() {
  docker compose --project-name "$compose_project" --file "$compose_file" ps --all || true
  docker compose --project-name "$compose_project" --file "$compose_file" logs --no-color || true
}
trap 'status=$?; if [ "$status" -ne 0 ]; then diagnose; fi; cleanup; exit "$status"' EXIT

mkdir -p "$release_root/current"/{chroma,uploads,knowledge_bases,conversations,auth,audit}
mkdir -p "$(dirname "$evidence_path")"
docker run --rm --user 0 --volume "$release_root/current:/data" --entrypoint sh \
  "$CURRENT_BACKEND_IMAGE" -c 'chown -R app:app /data'

export ACTIVE_DATA_ROOT="$release_root/current"
export RELEASE_BACKEND_IMAGE="$CURRENT_BACKEND_IMAGE"
export RELEASE_FRONTEND_IMAGE="$CURRENT_FRONTEND_IMAGE"
export RELEASE_PORT="5180"
docker compose --project-name "$compose_project" --file "$compose_file" up --detach --wait --wait-timeout 300

curl --fail --silent --show-error http://127.0.0.1:5180/api/health >/tmp/rongrag-current-health.json
curl --fail --silent --show-error \
  --header 'Content-Type: application/json' \
  --data '{"username":"release-admin","password":"release-check-only-password","display_name":"发布验证管理员"}' \
  http://127.0.0.1:5180/api/auth/bootstrap >/tmp/rongrag-current-bootstrap.json
access_token="$(uv run python -c 'import json,sys; print(json.load(open(sys.argv[1]))["access_token"])' /tmp/rongrag-current-bootstrap.json)"
curl --fail --silent --show-error \
  --header 'Content-Type: application/json' \
  --header "Authorization: Bearer $access_token" \
  --data '{"name":"release-rollback-evidence","description":"isolated compatibility record"}' \
  http://127.0.0.1:5180/api/knowledge-bases >/tmp/rongrag-current-knowledge-base.json

docker compose --project-name "$compose_project" --file "$compose_file" down --remove-orphans
uv run python scripts/backup_restore.py backup --data-root "$release_root/current" --output "$release_root/backup.tar.gz"
uv run python scripts/backup_restore.py verify --backup "$release_root/backup.tar.gz"
uv run python scripts/backup_restore.py restore --backup "$release_root/backup.tar.gz" --target "$release_root/restored"
docker run --rm --user 0 --volume "$release_root/restored:/data" --entrypoint sh \
  "$PREVIOUS_BACKEND_IMAGE" -c 'chown -R app:app /data'

export ACTIVE_DATA_ROOT="$release_root/restored"
export RELEASE_BACKEND_IMAGE="$PREVIOUS_BACKEND_IMAGE"
export RELEASE_FRONTEND_IMAGE="$PREVIOUS_FRONTEND_IMAGE"
export RELEASE_PORT="5181"
docker compose --project-name "$compose_project" --file "$compose_file" up --detach --wait --wait-timeout 300
curl --fail --silent --show-error http://127.0.0.1:5181/api/health >/tmp/rongrag-previous-health.json
curl --fail --silent --show-error http://127.0.0.1:5181/api/knowledge-bases >/tmp/rongrag-previous-knowledge-bases.json
uv run python -c 'import json,sys; assert any(item["name"] == "release-rollback-evidence" for item in json.load(open(sys.argv[1])))' /tmp/rongrag-previous-knowledge-bases.json

finished_at="$(date +%s)"
backup_sha256="$(uv run python -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$release_root/backup.tar.gz")"
uv run python - "$evidence_path" "$started_at" "$finished_at" "$backup_sha256" <<'PY'
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

output, started, finished, backup_sha = sys.argv[1:]
payload = {
    "verified_at": datetime.now(UTC).isoformat(),
    "current": {
        "backend_image": os.environ["CURRENT_BACKEND_IMAGE"],
        "frontend_image": os.environ["CURRENT_FRONTEND_IMAGE"],
    },
    "previous": {
        "backend_image": os.environ["PREVIOUS_BACKEND_IMAGE"],
        "frontend_image": os.environ["PREVIOUS_FRONTEND_IMAGE"],
    },
    "backup_sha256": backup_sha,
    "restore_target": "isolated-temporary-directory",
    "checks": ["current_health", "backup_integrity", "isolated_restore", "previous_health", "previous_data_read"],
    "result": "passed",
    "elapsed_seconds": int(finished) - int(started),
}
Path(output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

cat "$evidence_path"
