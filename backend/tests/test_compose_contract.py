from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_worker_does_not_inherit_backend_http_healthcheck() -> None:
    payload = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert payload["services"]["worker"]["healthcheck"] == {"disable": True}
