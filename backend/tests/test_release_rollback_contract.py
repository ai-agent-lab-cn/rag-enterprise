from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_release_compose_uses_postgres_worker_and_isolated_storage() -> None:
    payload = yaml.safe_load((ROOT / "docker-compose.release.yml").read_text(encoding="utf-8"))
    services = payload["services"]

    assert {"postgres", "migrate", "backend", "worker", "frontend"}.issubset(services)
    assert "DATABASE_URL" in services["backend"]["environment"]
    assert "DATABASE_URL" in services["worker"]["environment"]
    assert any("ACTIVE_POSTGRES_ROOT" in item for item in services["postgres"]["volumes"])
    assert all("chroma" not in str(service) for service in services.values())


def test_rollback_script_requires_digests_and_postgres_evidence() -> None:
    script = (ROOT / "scripts/verify_release_rollback.sh").read_text(encoding="utf-8")

    assert "@sha256:" in script
    assert "scripts.postgres_backup" in script
    assert "scripts.production_inventory" in script
    assert "isolated-postgresql-and-uploads" in script
    assert "backup_restore.py" not in script
    assert '"secrets_included": False' in script
    assert 'name != "sessions"' in script


def test_release_workflow_records_postgres_and_uses_commit_for_first_baseline() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "POSTGRES_ROLLBACK_BASE_COMMIT" in workflow
    assert '--arg postgres "$RELEASE_POSTGRES_IMAGE"' in workflow
    assert '--notes-start-tag "${{ steps.version.outputs.previous_sha }}"' in workflow
