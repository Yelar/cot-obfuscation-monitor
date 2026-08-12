from __future__ import annotations

import pytest

from cot_obfuscation_repro.audit import corpus_audit
from cot_obfuscation_repro.io import ROOT, read_json
from cot_obfuscation_repro.prepare import verify_bundled


def test_frozen_bundled_data() -> None:
    result = verify_bundled()
    assert result["full_1000"]["rows"] == 1000
    assert result["selector_100"]["rows"] == 100
    assert result["a_only"]["rows"] == 1000
    assert result["b_only"]["rows"] == 1000
    assert result["a_plus_b"]["rows"] == 1000
    assert all(not item["path"].startswith("/") for item in result.values())


def test_compositional_corpus_hardening_and_balance() -> None:
    result = corpus_audit()
    assert result["a_monitor_family_hits"] == 0
    assert result["b_monitor_family_hits"] == 0
    assert result["b_a_adjacent_hits"] == 0
    assert result["a_plus_b_component_counts"] == {"A": 500, "B": 500}


def test_frozen_main_results_match_reported_values() -> None:
    data = read_json(ROOT / "data/results/five_condition_corrected.json")["conditions"]
    expected = {
        "behavioral": (0.8992, 0.8470, 0.0688),
        "monitor_aware": (0.6624, 0.7361, 0.0874),
        "a_only": (0.4672, 0.7808, 0.0512),
        "b_only": (0.6232, 0.7535, 0.0768),
        "ab": (0.4016, 0.8187, 0.0364),
    }
    metric_names = (
        "concealment_rate_positive_valid",
        "successful_detection_rate_all_concealments",
        "conservative_uncaught_rate_all_scenarios",
    )
    for condition, values in expected.items():
        observed = tuple(data[condition]["metrics"][name]["mean"] for name in metric_names)
        assert observed == pytest.approx(values, abs=5e-5)


def test_selected_epochs_match_public_result_manifests() -> None:
    main = read_json(ROOT / "data/results/five_condition_corrected.json")["conditions"]
    main_manifest = read_json(ROOT / "config/main_results_manifest.example.json")["conditions"]
    assert {key: value["selected_epoch"] for key, value in main.items()} == {
        key: value["selected_epoch"] for key, value in main_manifest.items()
    }

    ratio = read_json(ROOT / "data/results/behavioral_ratio_corrected.json")["families"]
    ratio_manifest = read_json(ROOT / "config/ratio_results_manifest.example.json")["families"]
    for family, value in ratio.items():
        assert {key: point["selected_epoch"] for key, point in value["points"].items()} == {
            key: point["selected_epoch"] for key, point in ratio_manifest[family]["points"].items()
        }


def test_ratio_manifest_pins_every_historical_intermediate_arm() -> None:
    config = read_json(ROOT / "config/ratio_ablation.json")
    expected = config["expected_dataset_sha256"]
    assert set(expected) == {
        f"{family}_behavioral_{ratio}"
        for family in ("a_only", "b_only", "ab", "monitor_aware")
        for ratio in (25, 50, 75)
    }
    assert all(len(value) == 64 for value in expected.values())
