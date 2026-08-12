from __future__ import annotations

import json

import pytest

from cot_obfuscation_repro.aggregate import aggregate_payloads
from cot_obfuscation_repro.io import ROOT
from cot_obfuscation_repro.summarize import _condition, validate_summary


def _payload(seed: int, uncaught: int) -> dict:
    rows = []
    for index in range(10):
        conceal = index < 8
        rows.append(
            {
                "ground_truth": "yes" if index < 5 else "no",
                "classification": "CONCEAL" if conceal else "DISCLOSE",
                "monitor_classification": "NOT_DECEPTIVE" if index < uncaught else "DECEPTIVE",
            }
        )
    return {"config": {"replicate_seed": seed}, "results": rows}


def test_aggregate_converts_to_plot_condition(tmp_path) -> None:
    aggregate = aggregate_payloads([_payload(101, 1), _payload(202, 2)])
    aggregate_path = tmp_path / "aggregate.json"
    aggregate_path.write_text(json.dumps(aggregate), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    result = _condition(
        "behavioral",
        {"aggregate": "aggregate.json", "selected_epoch": 4},
        manifest_path,
        "Behavioral control",
    )
    assert result["replicate_seeds"] == [101, 202]
    assert result["metrics"]["conservative_uncaught_rate_all_scenarios"]["replicate_values"] == [0.1, 0.2]


@pytest.mark.parametrize(
    "name",
    ["five_condition_corrected.json", "behavioral_ratio_corrected.json"],
)
def test_frozen_plot_summaries_recompute_from_counts(name) -> None:
    result = validate_summary(ROOT / "data/results" / name)
    assert result["status"] == "valid"
