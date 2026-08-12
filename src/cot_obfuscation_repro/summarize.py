from __future__ import annotations

import argparse
import math
import statistics
from pathlib import Path
from typing import Any

from .aggregate import COUNT_FIELDS, RATE_FIELDS, mean_t_95
from .io import read_json, write_json


MAIN_LABELS = {
    "behavioral": "Behavioral control",
    "monitor_aware": "Explicit monitor",
    "a_only": "A-only",
    "b_only": "B-only",
    "ab": "A+B",
}
RATIO_LABELS = {
    "a_only": "A-only",
    "b_only": "B-only",
    "ab": "A+B",
    "monitor_aware": "Explicit monitor",
}


def _resolve(manifest_path: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else manifest_path.parent / path


def _summary_metric(metric: dict[str, Any]) -> dict[str, Any]:
    values = [float(value) for value in metric["replicate_values"]]
    sample_sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "mean": float(metric["pooled"]),
        "point_estimate": "pooled direct-count ratio",
        "seed_rate_mean": float(metric["replicate_mean"]),
        "sample_sd": sample_sd,
        "minimum": min(values),
        "maximum": max(values),
        "mean_t_95": [float(value) for value in metric.get("pooled_t_95", metric["replicate_mean_t_95"])],
        "replicate_values": values,
    }


def _condition(
    key: str,
    specification: dict[str, Any],
    manifest_path: Path,
    default_label: str,
) -> dict[str, Any]:
    aggregate_path = _resolve(manifest_path, str(specification["aggregate"]))
    aggregate = read_json(aggregate_path)
    if aggregate.get("status") != "aggregated_conservative_metrics":
        raise ValueError(f"Not a cot-aggregate result: {aggregate_path}")
    missing = set(RATE_FIELDS) - set(aggregate.get("metrics", {}))
    if missing:
        raise ValueError(f"{aggregate_path} is missing metrics: {sorted(missing)}")
    seeds = list(aggregate.get("replicate_seeds", []))
    if len(seeds) != int(aggregate["replicates"]):
        raise ValueError(f"Replicate seed count does not match in {aggregate_path}")
    pooled_counts = aggregate["pooled_counts"]
    total = int(pooled_counts["total_scenarios"])
    per_replicate = aggregate.get("per_replicate", [])
    scenarios = int(per_replicate[0]["total_scenarios"]) if per_replicate else total // len(seeds)
    return {
        "key": key,
        "label": str(specification.get("label", default_label)),
        "selected_epoch": int(specification["selected_epoch"]),
        "replicates": int(aggregate["replicates"]),
        "replicate_seeds": seeds,
        "scenarios_per_replicate": scenarios,
        "pooled_scenarios": total,
        "metrics": {name: _summary_metric(aggregate["metrics"][name]) for name in RATE_FIELDS},
        "pooled_counts": {name: pooled_counts[name] for name in COUNT_FIELDS if name in pooled_counts},
        "composition_pooled_counts": aggregate.get("composition_pooled_counts", {}),
        "per_replicate": per_replicate,
        "aggregate_source": str(aggregate_path),
    }


def assemble(manifest_path: Path) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    kind = manifest.get("kind")
    if kind == "main":
        specifications = manifest.get("conditions", {})
        conditions = {
            key: _condition(key, specification, manifest_path, MAIN_LABELS.get(key, key))
            for key, specification in specifications.items()
        }
        if set(conditions) != set(MAIN_LABELS):
            raise ValueError(f"Main manifest conditions must be {sorted(MAIN_LABELS)}")
        seed_sets = {tuple(item["replicate_seeds"]) for item in conditions.values()}
        if len(seed_sets) != 1:
            raise ValueError("Main conditions must use the same replicate seeds")
        return {
            "status": "assembled_conservative_results",
            "definition": "Plot-ready summaries assembled from cot-aggregate outputs",
            "replicate_seeds": list(next(iter(seed_sets))),
            "conditions": conditions,
            "provenance": {"manifest": str(manifest_path)},
        }
    if kind == "ratio":
        family_specs = manifest.get("families", {})
        if set(family_specs) != set(RATIO_LABELS):
            raise ValueError(f"Ratio manifest families must be {sorted(RATIO_LABELS)}")
        families: dict[str, Any] = {}
        for family, family_specification in family_specs.items():
            points = {
                str(int(percentage)): _condition(
                    f"{family}_behavioral_{int(percentage)}",
                    point,
                    manifest_path,
                    str(family_specification.get("label", RATIO_LABELS[family])),
                )
                for percentage, point in family_specification["points"].items()
            }
            if set(points) != {"0", "25", "50", "75", "100"}:
                raise ValueError(f"{family} must define behavioral shares 0, 25, 50, 75, and 100")
            families[family] = {
                "label": str(family_specification.get("label", RATIO_LABELS[family])),
                "points": points,
            }
        return {
            "status": "assembled_conservative_ratio_results",
            "definition": "Plot-ready ratio summaries assembled from cot-aggregate outputs",
            "families": families,
            "provenance": {"manifest": str(manifest_path)},
        }
    raise ValueError("Manifest kind must be 'main' or 'ratio'")


def _expected_pooled(metric_name: str, counts: dict[str, Any]) -> float:
    if metric_name == "concealment_rate_positive_valid":
        numerator, denominator = counts["conceal"], counts["positive_valid"]
    elif metric_name == "successful_detection_rate_all_concealments":
        numerator, denominator = counts["caught"], counts["conceal"]
    elif metric_name == "conservative_uncaught_rate_all_scenarios":
        numerator, denominator = counts["conservative_uncaught"], counts["total_scenarios"]
    else:
        raise ValueError(f"Unknown metric: {metric_name}")
    return float(numerator) / float(denominator) if denominator else 0.0


def _validate_condition(name: str, condition: dict[str, Any]) -> None:
    if int(condition["replicates"]) != len(condition["replicate_seeds"]):
        raise ValueError(f"{name}: replicate count does not match seed count")
    if len(set(condition["replicate_seeds"])) != len(condition["replicate_seeds"]):
        raise ValueError(f"{name}: duplicate replicate seeds")
    counts = condition["pooled_counts"]
    for metric_name in RATE_FIELDS:
        metric = condition["metrics"][metric_name]
        values = [float(value) for value in metric["replicate_values"]]
        mean, interval = mean_t_95(values, center=float(metric["mean"]))
        checks = (
            (float(metric["mean"]), _expected_pooled(metric_name, counts), "pooled mean"),
            (float(metric["seed_rate_mean"]), mean, "replicate mean"),
            (float(metric["mean_t_95"][0]), interval[0], "CI lower bound"),
            (float(metric["mean_t_95"][1]), interval[1], "CI upper bound"),
        )
        for observed, expected, label in checks:
            if not math.isclose(observed, expected, rel_tol=1e-10, abs_tol=1e-10):
                raise ValueError(f"{name}/{metric_name}: {label} is inconsistent")


def validate_summary(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    checked = 0
    if "conditions" in payload:
        for name, condition in payload["conditions"].items():
            _validate_condition(name, condition)
            checked += 1
    elif "families" in payload:
        for family, family_payload in payload["families"].items():
            for percentage, condition in family_payload["points"].items():
                _validate_condition(f"{family}/{percentage}", condition)
                checked += 1
    else:
        raise ValueError(f"Not a plot summary: {path}")
    return {"path": str(path), "conditions_or_points_checked": checked, "status": "valid"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble or validate plot-ready conservative result summaries")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", type=Path, help="Main or ratio assembly manifest")
    group.add_argument("--validate-summary", type=Path, nargs="+", help="Existing plot summaries to validate")
    parser.add_argument("--output", type=Path, help="Output path when using --manifest")
    args = parser.parse_args()
    if args.manifest:
        if args.output is None:
            parser.error("--output is required with --manifest")
        payload = assemble(args.manifest)
        write_json(args.output, payload)
        validate_summary(args.output)
        print(f"assembled {payload['status']}: {args.output}")
    else:
        for path in args.validate_summary:
            result = validate_summary(path)
            print(f"validated {result['conditions_or_points_checked']} condition(s)/point(s): {path}")


if __name__ == "__main__":
    main()
