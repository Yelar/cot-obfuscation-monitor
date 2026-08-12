from __future__ import annotations

import argparse
import math
import statistics
from pathlib import Path
from typing import Any

from .io import read_json, write_json
from .evaluate import validate_result_payload
from .metrics import conservative_summary, payload_rows, wilson


# Two-sided 95% Student-t critical values. The released experiment uses either
# three or five replicates, but the longer table makes the helper reusable.
T_975 = {
    1: 12.7062047364,
    2: 4.30265272991,
    3: 3.18244630528,
    4: 2.77644510520,
    5: 2.57058183564,
    6: 2.44691184879,
    7: 2.36462425101,
    8: 2.30600413503,
    9: 2.26215716285,
    10: 2.22813885196,
    11: 2.20098516009,
    12: 2.17881282966,
    13: 2.16036865646,
    14: 2.14478668792,
    15: 2.13144954556,
    16: 2.11990529922,
    17: 2.10981557783,
    18: 2.10092204024,
    19: 2.09302405441,
    20: 2.08596344727,
    25: 2.05953855275,
    30: 2.04227245630,
}


RATE_FIELDS = (
    "concealment_rate_positive_valid",
    "successful_detection_rate_all_concealments",
    "conservative_uncaught_rate_all_scenarios",
)
COUNT_FIELDS = (
    "total_scenarios",
    "positive_scenarios",
    "negative_scenarios",
    "positive_valid",
    "conceal",
    "disclose",
    "caught",
    "conservative_uncaught",
    "monitor_ineligible_short_reasoning",
    "monitor_errors",
    "negative_false_alarm",
)


def t_critical(df: int) -> float:
    if df <= 0:
        return 0.0
    eligible = [key for key in T_975 if key <= df]
    return T_975[max(eligible)] if eligible else 1.96


def mean_t_95(values: list[float], center: float | None = None) -> tuple[float, list[float]]:
    mean = statistics.fmean(values) if values else 0.0
    point = mean if center is None else center
    if len(values) < 2:
        return mean, [point, point]
    half = t_critical(len(values) - 1) * statistics.stdev(values) / math.sqrt(len(values))
    return mean, [max(0.0, point - half), min(1.0, point + half)]


def aggregate_payloads(payloads: list[dict[str, Any]], sources: list[str] | None = None) -> dict[str, Any]:
    if not payloads:
        raise ValueError("At least one result payload is required")
    summaries = [conservative_summary(payload_rows(payload)) for payload in payloads]
    pooled_rows = [row for payload in payloads for row in payload_rows(payload)]
    pooled = conservative_summary(pooled_rows)
    seeds = [payload.get("config", {}).get("replicate_seed") for payload in payloads]
    if any(seed is None for seed in seeds):
        raise ValueError("Every result payload must record config.replicate_seed")
    if len(seeds) != len(set(seeds)):
        raise ValueError("Replicate seeds must be unique")
    for field, label in (("model", "models/checkpoints"), ("scenario_manifest", "scenario manifests")):
        values = [payload.get("config", {}).get(field) for payload in payloads]
        present = [value for value in values if value is not None]
        if present and len(present) != len(values):
            raise ValueError(f"Either every payload or no payload must record {label}")
        comparable = [str(value.get("sha256")) if field == "scenario_manifest" else str(value) for value in present]
        if comparable and len(set(comparable)) != 1:
            raise ValueError(f"Cannot aggregate mixed {label}")
    row_counts = [len(payload_rows(payload)) for payload in payloads]
    if len(set(row_counts)) != 1:
        raise ValueError("Replicate payloads must contain the same number of scenarios")

    metrics = {}
    for field in RATE_FIELDS:
        values = [float(summary[field]) for summary in summaries]
        mean, interval = mean_t_95(values)
        metrics[field] = {
            "pooled": float(pooled[field]),
            "replicate_mean": mean,
            "replicate_mean_t_95": interval,
            "pooled_t_95": mean_t_95(values, center=float(pooled[field]))[1],
            "replicate_values": values,
        }

    return {
        "status": "aggregated_conservative_metrics",
        "replicates": len(payloads),
        "replicate_seeds": seeds,
        "sources": sources or [],
        "pooled_counts": {field: pooled[field] for field in COUNT_FIELDS},
        "composition_pooled_counts": pooled["composition"],
        "metrics": metrics,
        "conservative_uncaught_pooled_wilson_95": wilson(
            pooled["conservative_uncaught"], pooled["total_scenarios"]
        ),
        "per_replicate": summaries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate monitored result payloads across replicate seeds")
    parser.add_argument("results", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-seeds", type=int, nargs="+")
    parser.add_argument("--expected-rows", type=int)
    parser.add_argument("--expected-scenario-sha")
    parser.add_argument("--scenarios", type=Path, required=True, help="Strictly validate every monitored result")
    args = parser.parse_args()
    payloads = [read_json(path) for path in args.results]
    from .evaluate import validate_scenarios

    scenario_rows = read_json(args.scenarios)
    scenario_manifest = validate_scenarios(args.scenarios, args.expected_scenario_sha)
    for path, payload in zip(args.results, payloads):
        try:
            validate_result_payload(payload, scenario_manifest, scenarios=scenario_rows)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
    if args.expected_rows is not None:
        wrong = [str(path) for path, payload in zip(args.results, payloads) if len(payload_rows(payload)) != args.expected_rows]
        if wrong:
            raise ValueError(f"Expected {args.expected_rows} rows in every payload; mismatches: {wrong}")
    if args.expected_scenario_sha is not None:
        wrong = [
            str(path)
            for path, payload in zip(args.results, payloads)
            if payload.get("config", {}).get("scenario_manifest", {}).get("sha256") != args.expected_scenario_sha
        ]
        if wrong:
            raise ValueError(f"Scenario hash mismatch in: {wrong}")
    result = aggregate_payloads(payloads, [str(path) for path in args.results])
    if args.expected_seeds is not None and set(result["replicate_seeds"]) != set(args.expected_seeds):
        raise ValueError(
            f"Expected replicate seeds {sorted(args.expected_seeds)}, found {sorted(result['replicate_seeds'])}"
        )
    write_json(args.output, result)
    print(f"aggregated {len(payloads)} replicate(s): {args.output}")


if __name__ == "__main__":
    main()
