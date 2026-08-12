from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from .io import ROOT, read_json, write_json
from .metrics import conservative_summary, payload_rows


EPOCH_RE = re.compile(r"epoch[_ .-]?(\d+)", re.IGNORECASE)


def infer_epoch(path: Path, payload: dict[str, Any]) -> int:
    model = payload.get("config", {}).get("model", {})
    if isinstance(model, dict) and model.get("epoch") is not None:
        return int(model["epoch"])
    match = EPOCH_RE.search(path.stem)
    if not match:
        raise ValueError(f"Cannot infer epoch from {path}")
    return int(match.group(1))


def select(
    files: list[Path],
    *,
    expected_epochs: set[int] | None = None,
    expected_seed: int | None = None,
    expected_scenario_sha: str | None = None,
    expected_rows: int | None = None,
) -> dict[str, Any]:
    candidates = []
    observed_seeds: list[int | None] = []
    observed_hashes: list[str | None] = []
    for path in files:
        payload = read_json(path)
        rows = payload_rows(payload)
        config = payload.get("config", {})
        manifest = config.get("scenario_manifest", {})
        if expected_rows is not None and len(rows) != expected_rows:
            raise ValueError(f"{path}: expected {expected_rows} results, found {len(rows)}")
        observed_seeds.append(config.get("replicate_seed"))
        observed_hashes.append(manifest.get("sha256"))
        metrics = conservative_summary(rows)
        model = config.get("model")
        if not isinstance(model, str) or not model:
            raise ValueError(f"{path}: selector result is missing config.model")
        candidates.append(
            {"epoch": infer_epoch(path, payload), "path": str(path), "model": model, "metrics": metrics}
        )
    if not candidates:
        raise ValueError("No checkpoint result files supplied")
    epochs = [item["epoch"] for item in candidates]
    if len(epochs) != len(set(epochs)):
        raise ValueError("Checkpoint screen contains duplicate epochs")
    if expected_epochs is not None and set(epochs) != expected_epochs:
        raise ValueError(f"Expected epochs {sorted(expected_epochs)}, found {sorted(epochs)}")
    if expected_seed is not None and any(seed != expected_seed for seed in observed_seeds):
        raise ValueError(f"Every selector result must use replicate seed {expected_seed}")
    available_hashes = {value for value in observed_hashes if value is not None}
    if available_hashes and len(available_hashes) != 1:
        raise ValueError("Selector results use different scenario files")
    if expected_scenario_sha is not None and any(value != expected_scenario_sha for value in observed_hashes):
        raise ValueError("Every selector result must use the frozen selector scenario hash")
    candidates.sort(key=lambda item: item["epoch"])
    chosen = max(candidates, key=lambda item: (item["metrics"]["conservative_uncaught"], -item["epoch"]))
    return {
        "selection_metric": "maximum conservative absolute uncaught count",
        "tie_break": "earlier epoch",
        "selected_epoch": chosen["epoch"],
        "selected_path": chosen["path"],
        "selected_model": chosen["model"],
        "selected_metrics": chosen["metrics"],
        "candidates": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Select an epoch on the frozen 100-scenario screen")
    parser.add_argument("results", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-epochs", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    parser.add_argument("--expected-seed", type=int, default=101)
    parser.add_argument("--expected-rows", type=int, default=100)
    parser.add_argument("--expected-scenario-sha")
    args = parser.parse_args()
    frozen_sha = read_json(ROOT / "config/datasets.json")["evaluation_scenarios"]["selector_100"]["sha256"]
    write_json(
        args.output,
        select(
            args.results,
            expected_epochs=set(args.expected_epochs),
            expected_seed=args.expected_seed,
            expected_rows=args.expected_rows,
            expected_scenario_sha=args.expected_scenario_sha or frozen_sha,
        ),
    )
    print(f"selected epoch {read_json(args.output)['selected_epoch']}: {args.output}")


if __name__ == "__main__":
    main()
