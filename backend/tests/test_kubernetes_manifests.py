import re
from pathlib import Path

from scripts.validate_kubernetes import validate


def test_kubernetes_manifests_follow_rehearsal_boundaries() -> None:
    root = Path(__file__).resolve().parents[2] / "deploy" / "kubernetes"

    assert validate(root) == []


def test_backup_job_timestamp_is_a_valid_kubernetes_name() -> None:
    example = "rag-postgres-backup-20260822t163126z"

    assert re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", example)
