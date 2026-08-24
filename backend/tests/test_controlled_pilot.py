from __future__ import annotations

from copy import deepcopy

from scripts.controlled_pilot import evaluate


def policy() -> dict:
    return {
        "external_slo": False,
        "sample_window_seconds": 300,
        "minimum_samples": 2,
        "thresholds": {
            "availability_ratio_min": 0.99,
            "ready_latency_p95_ms_max": 1000,
            "retrieval_failure_ratio_max": 0.05,
            "index_failure_ratio_max": 0.05,
            "oldest_queued_seconds_max": 300,
            "database_capacity_ratio_max": 0.8,
            "uploads_capacity_ratio_max": 0.8,
            "backups_capacity_ratio_max": 0.8,
        },
    }


def samples() -> list[dict]:
    capacity = {
        "database": {"used_bytes": 10, "total_bytes": 100},
        "uploads": {"used_bytes": 20, "total_bytes": 100},
        "backups": {"used_bytes": 30, "total_bytes": 100},
    }
    return [
        {
            "timestamp": "2026-08-24T00:00:00Z",
            "ready": True,
            "ready_latency_ms": 10,
            "rag": {"queries": 100, "failures": 2},
            "indexing": {"attempts": 20, "failures": 1},
            "oldest_queued_seconds": 20,
            "capacity": capacity,
        },
        {
            "timestamp": "2026-08-24T00:05:00Z",
            "ready": True,
            "ready_latency_ms": 20,
            "rag": {"queries": 120, "failures": 2},
            "indexing": {"attempts": 40, "failures": 1},
            "oldest_queued_seconds": 10,
            "capacity": capacity,
        },
    ]


def test_controlled_pilot_passes_complete_internal_sample() -> None:
    report = evaluate(samples(), policy())

    assert report["verdict"] == "pass"
    assert report["external_slo"] is False
    assert report["observations"]["availability_ratio"] == 1
    assert report["observations"]["retrieval_failure_ratio"] == 0
    assert report["checks"]["sample_count"]["status"] == "pass"
    assert report["checks"]["sample_window_seconds"]["status"] == "pass"


def test_controlled_pilot_fails_closed_for_missing_or_bad_observations() -> None:
    incomplete = deepcopy(samples())
    incomplete[-1]["ready"] = False
    incomplete[-1].pop("capacity")
    incomplete[-1]["rag"] = {"queries": 120, "failures": 10}

    report = evaluate(incomplete, policy())

    assert report["verdict"] == "fail"
    assert report["checks"]["availability_ratio"]["status"] == "fail"
    assert report["checks"]["retrieval_failure_ratio"]["status"] == "fail"
    assert report["missing_data_rule"].startswith("缺少任一")
