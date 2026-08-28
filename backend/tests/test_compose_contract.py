from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_worker_uses_process_liveness_healthcheck() -> None:
    payload = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    healthcheck = payload["services"]["worker"]["healthcheck"]
    assert healthcheck["test"] == ["CMD", "python", "-c", "import os; os.kill(1, 0)"]
    assert "disable" not in healthcheck
