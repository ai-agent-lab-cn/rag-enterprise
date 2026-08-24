#!/usr/bin/env python3
"""计算受控试运行 SLI，并生成不构成对外 SLO 的可机读证据。"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFINITIONS = {
    "availability_ratio": "就绪探测成功样本数 / 全部就绪探测样本数",
    "ready_latency_p95_ms": "就绪探测耗时的最近秩 P95；毫秒",
    "retrieval_failure_ratio": "窗口内 RAG failures 增量 / queries 增量",
    "index_failure_ratio": "窗口结束时失败索引任务数 / 已结束索引任务数",
    "oldest_queued_seconds": "窗口内最老 queued 索引任务等待秒数最大值",
    "database_capacity_ratio": "数据库占用字节 / 数据库容量预算字节",
    "uploads_capacity_ratio": "原始文件 PVC 已用字节 / 总字节",
    "backups_capacity_ratio": "备份 PVC 已用字节 / 总字节",
}


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * percentile))
    return round(ordered[rank - 1], 2)


def _counter_ratio(
    samples: list[dict[str, Any]], group: str, numerator: str, denominator: str
) -> float | None:
    first = samples[0].get(group)
    last = samples[-1].get(group)
    if not isinstance(first, dict) or not isinstance(last, dict):
        return None
    numerator_delta = int(last.get(numerator, 0)) - int(first.get(numerator, 0))
    denominator_delta = int(last.get(denominator, 0)) - int(first.get(denominator, 0))
    if numerator_delta < 0 or denominator_delta <= 0:
        return None
    return round(numerator_delta / denominator_delta, 6)


def _capacity_ratio(samples: list[dict[str, Any]], name: str) -> float | None:
    ratios: list[float] = []
    for sample in samples:
        capacity = sample.get("capacity", {}).get(name)
        if not isinstance(capacity, dict):
            continue
        used = int(capacity.get("used_bytes", -1))
        total = int(capacity.get("total_bytes", 0))
        if used >= 0 and total > 0:
            ratios.append(used / total)
    return round(max(ratios), 6) if ratios else None


def _current_ratio(
    samples: list[dict[str, Any]], group: str, numerator: str, denominator: str
) -> float | None:
    current = samples[-1].get(group)
    if not isinstance(current, dict):
        return None
    numerator_value = int(current.get(numerator, 0))
    denominator_value = int(current.get(denominator, 0))
    if numerator_value < 0 or denominator_value <= 0:
        return None
    return round(numerator_value / denominator_value, 6)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def evaluate(samples: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    minimum_samples = int(policy["minimum_samples"])
    thresholds = dict(policy["thresholds"])
    if policy.get("external_slo") is not False:
        raise ValueError("受控试运行策略必须显式声明 external_slo=false")
    if not samples:
        raise ValueError("没有可用于计算的试运行样本")

    ready = [sample.get("ready") is True for sample in samples]
    latencies = [float(sample["ready_latency_ms"]) for sample in samples if "ready_latency_ms" in sample]
    observations: dict[str, float | None] = {
        "availability_ratio": round(sum(ready) / len(samples), 6),
        "ready_latency_p95_ms": _percentile(latencies, 0.95) if latencies else None,
        "retrieval_failure_ratio": _counter_ratio(samples, "rag", "failures", "queries"),
        "index_failure_ratio": _current_ratio(samples, "indexing", "failures", "attempts"),
        "oldest_queued_seconds": max(
            (int(sample["oldest_queued_seconds"]) for sample in samples if "oldest_queued_seconds" in sample),
            default=None,
        ),
        "database_capacity_ratio": _capacity_ratio(samples, "database"),
        "uploads_capacity_ratio": _capacity_ratio(samples, "uploads"),
        "backups_capacity_ratio": _capacity_ratio(samples, "backups"),
    }
    checks: dict[str, dict[str, Any]] = {}
    for name, value in observations.items():
        suffix = "_min" if name == "availability_ratio" else "_max"
        threshold = thresholds[f"{name}{suffix}"]
        passed = value is not None and (value >= threshold if suffix == "_min" else value <= threshold)
        checks[name] = {"status": "pass" if passed else "fail", "value": value, "threshold": threshold}

    started_at = _parse_timestamp(samples[0].get("timestamp"))
    ended_at = _parse_timestamp(samples[-1].get("timestamp"))
    observed_seconds = (ended_at - started_at).total_seconds() if started_at and ended_at else None
    sample_check = len(samples) >= minimum_samples
    checks["sample_count"] = {
        "status": "pass" if sample_check else "fail",
        "value": len(samples),
        "threshold": minimum_samples,
    }
    window_check = observed_seconds is not None and observed_seconds >= int(policy["sample_window_seconds"])
    checks["sample_window_seconds"] = {
        "status": "pass" if window_check else "fail",
        "value": observed_seconds,
        "threshold": int(policy["sample_window_seconds"]),
    }
    verdict = "pass" if all(item["status"] == "pass" for item in checks.values()) else "fail"
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "external_slo": False,
        "statement": "仅用于单组织受控试运行的内部工程门禁，不构成对外 SLO。",
        "sample_window": {
            "expected_seconds": int(policy["sample_window_seconds"]),
            "sample_count": len(samples),
            "observed_seconds": observed_seconds,
            "started_at": samples[0].get("timestamp"),
            "ended_at": samples[-1].get("timestamp"),
        },
        "definitions": DEFINITIONS,
        "missing_data_rule": "缺少任一必需观测值时对应检查失败，不使用零值或上一窗口数据代替。",
        "observations": observations,
        "checks": checks,
        "verdict": verdict,
    }


def load_samples(path: Path) -> list[dict[str, Any]]:
    samples = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError(f"第 {line_number} 行不是 JSON 对象")
        samples.append(item)
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True, help="JSON Lines 格式的采样记录")
    parser.add_argument("--policy", type=Path, default=Path("config/controlled-pilot.json"))
    parser.add_argument("--output", type=Path, help="验收证据输出路径；默认输出到标准输出")
    args = parser.parse_args()
    report = evaluate(load_samples(args.samples), json.loads(args.policy.read_text(encoding="utf-8")))
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    raise SystemExit(0 if report["verdict"] == "pass" else 1)


if __name__ == "__main__":
    main()
