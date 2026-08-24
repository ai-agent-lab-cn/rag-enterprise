#!/usr/bin/env bash
set -euo pipefail

: "${CURRENT_BACKEND_IMAGE:?CURRENT_BACKEND_IMAGE is required}"
: "${CURRENT_FRONTEND_IMAGE:?CURRENT_FRONTEND_IMAGE is required}"
: "${PREVIOUS_BACKEND_IMAGE:?PREVIOUS_BACKEND_IMAGE is required}"
: "${PREVIOUS_FRONTEND_IMAGE:?PREVIOUS_FRONTEND_IMAGE is required}"
: "${RELEASE_POSTGRES_IMAGE:?RELEASE_POSTGRES_IMAGE is required}"

compose_file="docker-compose.release.yml"
release_root="$(mktemp -d /tmp/rag-postgres-rollback.XXXXXX)"
current_project="rag-current-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
previous_project="rag-previous-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
evidence_path="${ROLLBACK_EVIDENCE_PATH:-$release_root/rollback-evidence.json}"
started_at="$(date +%s)"
postgres_password="$(openssl rand -hex 24)"
admin_password="$(openssl rand -hex 24)"
member_password="$(openssl rand -hex 24)"

validate_image() {
  local image="$1"
  if [[ "$image" =~ ^[^[:space:]]+@sha256:[a-f0-9]{64}$ ]]; then return 0; fi
  if [[ "${ALLOW_LOCAL_IMAGE_IDS:-false}" == "true" && "$image" =~ ^sha256:[a-f0-9]{64}$ ]]; then return 0; fi
  echo "镜像必须使用 registry@sha256:digest；本地仅可显式允许 sha256 image ID：$image" >&2
  exit 2
}
for image in "$CURRENT_BACKEND_IMAGE" "$CURRENT_FRONTEND_IMAGE" "$PREVIOUS_BACKEND_IMAGE" "$PREVIOUS_FRONTEND_IMAGE" "$RELEASE_POSTGRES_IMAGE"; do
  validate_image "$image"
done

diagnose() {
  for project in "$current_project" "$previous_project"; do
    docker compose --project-name "$project" --file "$compose_file" ps --all || true
    docker compose --project-name "$project" --file "$compose_file" logs --no-color || true
  done
}
cleanup() {
  docker compose --project-name "$current_project" --file "$compose_file" down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker compose --project-name "$previous_project" --file "$compose_file" down --volumes --remove-orphans >/dev/null 2>&1 || true
  if [[ "$release_root" == /tmp/rag-postgres-rollback.* ]]; then
    find "$release_root" -depth -delete >/dev/null 2>&1 || true
  fi
}
trap 'status=$?; if [[ "$status" -ne 0 ]]; then diagnose; fi; cleanup; exit "$status"' EXIT

mkdir -p "$release_root/current"/{postgres,uploads,backups} "$release_root/previous"/{postgres,uploads,backups}
mkdir -p "$(dirname "$evidence_path")"
cp knowledge/project-profile.md "$release_root/current/uploads/release-rollback.md"
docker run --rm --user 0 --volume "$release_root/current:/target" --entrypoint sh \
  "$CURRENT_BACKEND_IMAGE" -c 'chown -R 999:999 /target/uploads /target/backups'
docker run --rm --user 0 --volume "$release_root/previous:/target" --entrypoint sh \
  "$PREVIOUS_BACKEND_IMAGE" -c 'chown -R 999:999 /target/uploads /target/backups'

export POSTGRES_PASSWORD="$postgres_password"
export ACTIVE_POSTGRES_ROOT="$release_root/current/postgres"
export ACTIVE_UPLOADS_ROOT="$release_root/current/uploads"
export ACTIVE_BACKUPS_ROOT="$release_root/current/backups"
export RELEASE_BACKEND_IMAGE="$CURRENT_BACKEND_IMAGE"
export RELEASE_FRONTEND_IMAGE="$CURRENT_FRONTEND_IMAGE"
export RELEASE_PORT=5180

docker compose --project-name "$current_project" --file "$compose_file" up --detach --wait postgres
docker compose --project-name "$current_project" --file "$compose_file" --profile tools run --rm migrate
docker compose --project-name "$current_project" --file "$compose_file" up --detach --wait backend worker frontend

ADMIN_PASSWORD="$admin_password" MEMBER_PASSWORD="$member_password" uv run python - "$release_root" <<'PY'
import json, os, sys
from pathlib import Path
root = Path(sys.argv[1])
payloads = {
    "admin.json": {"username": "release-admin", "password": os.environ["ADMIN_PASSWORD"], "display_name": "发布验证管理员"},
    "member.json": {"username": "release-member", "password": os.environ["MEMBER_PASSWORD"], "display_name": "发布验证成员", "role": "member"},
}
for name, payload in payloads.items():
    path = root / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
PY

curl --fail --silent --show-error --header 'Content-Type: application/json' \
  --data-binary "@$release_root/admin.json" http://127.0.0.1:5180/api/auth/bootstrap > "$release_root/admin-response.json"
admin_token="$(uv run python -c 'import json,sys; print(json.load(open(sys.argv[1]))["access_token"])' "$release_root/admin-response.json")"
curl --fail --silent --show-error --header 'Content-Type: application/json' \
  --header "Authorization: Bearer $admin_token" --data-binary "@$release_root/member.json" \
  http://127.0.0.1:5180/api/members > "$release_root/member-response.json"
member_id="$(uv run python -c 'import json,sys; print(json.load(open(sys.argv[1]))["user_id"])' "$release_root/member-response.json")"
curl --fail --silent --show-error --request PUT --header "Authorization: Bearer $admin_token" \
  "http://127.0.0.1:5180/api/knowledge-bases/kb_default/members/$member_id" >/dev/null

fixture_sha="$(uv run python -c 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("knowledge/project-profile.md").read_bytes()).hexdigest())')"
fixture_bytes="$(wc -c < knowledge/project-profile.md | tr -d ' ')"
docker compose --project-name "$current_project" --file "$compose_file" exec -T postgres psql -U rag -d rag_enterprise -v ON_ERROR_STOP=1 \
  -v fixture_sha="$fixture_sha" -v fixture_bytes="$fixture_bytes" <<'SQL' >/dev/null
INSERT INTO data_sources(data_source_id, knowledge_base_id, source_type, name, configuration, created_at, updated_at)
VALUES ('src_release_rollback', 'kb_default', 'file', 'release-rollback.md', '{"source_path":"release-rollback.md"}', now(), now());
INSERT INTO documents(document_id, knowledge_base_id, data_source_id, filename, created_at, updated_at)
VALUES ('doc_release_rollback', 'kb_default', 'src_release_rollback', 'release-rollback.md', now(), now());
INSERT INTO document_versions(document_version_id, knowledge_base_id, document_id, version_number, content_sha256, source_file_bytes, source_path, status, created_at, indexed_at)
VALUES ('ver_release_rollback', 'kb_default', 'doc_release_rollback', 1, :'fixture_sha', :'fixture_bytes', 'release-rollback.md', 'ready', now(), now());
UPDATE documents SET current_version_id='ver_release_rollback' WHERE knowledge_base_id='kb_default' AND document_id='doc_release_rollback';
INSERT INTO chunks(chunk_id, document_version_id, knowledge_base_id, chunk_index, content, metadata, embedding, created_at)
VALUES ('chk_release_rollback', 'ver_release_rollback', 'kb_default', 0, 'release rollback evidence', '{}', '[0.1,0.2]', now());
INSERT INTO index_jobs(index_job_id, knowledge_base_id, data_source_id, document_version_id, idempotency_key, status, attempt_count, finished_at)
VALUES ('job_release_rollback', 'kb_default', 'src_release_rollback', 'ver_release_rollback', 'release-rollback-evidence', 'succeeded', 1, now());
SQL

docker compose --project-name "$current_project" --file "$compose_file" exec -T backend \
  python -m scripts.production_inventory > "$release_root/current-inventory.json"
docker compose --project-name "$current_project" --file "$compose_file" exec -T backend \
  python -m scripts.postgres_backup backup --uploads-root /app/data/uploads --output /app/data/backups/rollback.tar.gz >/dev/null
docker compose --project-name "$current_project" --file "$compose_file" exec -T backend \
  python -m scripts.postgres_backup verify --backup /app/data/backups/rollback.tar.gz >/dev/null
docker compose --project-name "$current_project" --file "$compose_file" down --remove-orphans

cp "$release_root/current/backups/rollback.tar.gz" "$release_root/previous/backups/rollback.tar.gz"
export ACTIVE_POSTGRES_ROOT="$release_root/previous/postgres"
export ACTIVE_UPLOADS_ROOT="$release_root/previous/uploads"
export ACTIVE_BACKUPS_ROOT="$release_root/previous/backups"
export RELEASE_BACKEND_IMAGE="$PREVIOUS_BACKEND_IMAGE"
export RELEASE_FRONTEND_IMAGE="$PREVIOUS_FRONTEND_IMAGE"
export RELEASE_PORT=5181

docker compose --project-name "$previous_project" --file "$compose_file" up --detach --wait postgres
docker compose --project-name "$previous_project" --file "$compose_file" run --rm --no-deps backend \
  python -m scripts.postgres_backup restore --backup /app/data/backups/rollback.tar.gz --uploads-target /app/data/uploads >/dev/null
docker compose --project-name "$previous_project" --file "$compose_file" up --detach --wait backend worker frontend
curl --fail --silent --show-error http://127.0.0.1:5181/api/health/ready > "$release_root/previous-health.json"

curl --fail --silent --show-error --header 'Content-Type: application/json' \
  --data-binary "@$release_root/admin.json" http://127.0.0.1:5181/api/auth/login > "$release_root/previous-admin-login.json"
previous_admin_token="$(uv run python -c 'import json,sys; print(json.load(open(sys.argv[1]))["access_token"])' "$release_root/previous-admin-login.json")"
curl --fail --silent --show-error --header 'Content-Type: application/json' \
  --data-binary "@$release_root/member.json" http://127.0.0.1:5181/api/auth/login > "$release_root/previous-member-login.json"
previous_member_token="$(uv run python -c 'import json,sys; print(json.load(open(sys.argv[1]))["access_token"])' "$release_root/previous-member-login.json")"
curl --fail --silent --show-error --header "Authorization: Bearer $previous_admin_token" \
  http://127.0.0.1:5181/api/knowledge-bases > "$release_root/previous-admin-bases.json"
curl --fail --silent --show-error --header "Authorization: Bearer $previous_member_token" \
  http://127.0.0.1:5181/api/knowledge-bases > "$release_root/previous-member-bases.json"
docker compose --project-name "$previous_project" --file "$compose_file" exec -T backend \
  python -m scripts.production_inventory > "$release_root/previous-inventory.json"

CURRENT_INVENTORY="$release_root/current-inventory.json" PREVIOUS_INVENTORY="$release_root/previous-inventory.json" \
  ADMIN_BASES="$release_root/previous-admin-bases.json" MEMBER_BASES="$release_root/previous-member-bases.json" \
  uv run python - <<'PY'
import json, os
current = json.load(open(os.environ["CURRENT_INVENTORY"], encoding="utf-8"))
previous = json.load(open(os.environ["PREVIOUS_INVENTORY"], encoding="utf-8"))
assert current["verdict"] == previous["verdict"] == "pass"
durable_counts = {name for name in current["counts"] if name != "sessions"}
assert all(current["counts"][name] == previous["counts"][name] for name in durable_counts)
required = {"users": 2, "knowledge_bases": 1, "memberships": 1, "documents": 1,
            "document_versions": 1, "chunks": 1, "vectors": 1, "index_jobs": 1}
assert all(previous["counts"][name] == value for name, value in required.items())
for path in (os.environ["ADMIN_BASES"], os.environ["MEMBER_BASES"]):
    assert any(item["knowledge_base_id"] == "kb_default" for item in json.load(open(path, encoding="utf-8")))
PY

finished_at="$(date +%s)"
backup_sha256="$(uv run python -c 'import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$release_root/previous/backups/rollback.tar.gz")"
CURRENT_INVENTORY="$release_root/current-inventory.json" PREVIOUS_INVENTORY="$release_root/previous-inventory.json" \
  BACKUP_SHA256="$backup_sha256" STARTED_AT="$started_at" FINISHED_AT="$finished_at" \
  uv run python - "$evidence_path" <<'PY'
import json, os, sys
from datetime import UTC, datetime
from pathlib import Path
current = json.load(open(os.environ["CURRENT_INVENTORY"], encoding="utf-8"))
previous = json.load(open(os.environ["PREVIOUS_INVENTORY"], encoding="utf-8"))
payload = {
    "schema_version": 2,
    "verified_at": datetime.now(UTC).isoformat(),
    "images": {
        "postgres": os.environ["RELEASE_POSTGRES_IMAGE"],
        "current_backend": os.environ["CURRENT_BACKEND_IMAGE"],
        "current_frontend": os.environ["CURRENT_FRONTEND_IMAGE"],
        "previous_backend": os.environ["PREVIOUS_BACKEND_IMAGE"],
        "previous_frontend": os.environ["PREVIOUS_FRONTEND_IMAGE"],
    },
    "backup_sha256": os.environ["BACKUP_SHA256"],
    "restore_target": "isolated-postgresql-and-uploads",
    "counts": previous["counts"],
    "checks": ["immutable_images", "current_health", "postgres_backup_integrity", "isolated_restore",
               "previous_schema_compatibility", "previous_health", "accounts_and_permissions",
               "document_versions_vectors_and_jobs", "worker_start"],
    "secrets_included": False,
    "result": "passed",
    "elapsed_seconds": int(os.environ["FINISHED_AT"]) - int(os.environ["STARTED_AT"]),
}
durable_counts = {name for name in current["counts"] if name != "sessions"}
assert all(current["counts"][name] == previous["counts"][name] for name in durable_counts)
Path(sys.argv[1]).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

cat "$evidence_path"
