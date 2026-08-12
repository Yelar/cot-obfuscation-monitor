from __future__ import annotations

import re

import pytest

from cot_obfuscation_repro.io import ROOT, read_json


def _launcher(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assert_training_flags(script: str, config: dict) -> None:
    expected = {
        "learning_rate": config["learning_rate"],
        "batch_size": config["batch_size"],
        "rank": config["rank"],
        "num_epochs": config["epochs"],
        "seed": config["seed"],
    }
    for flag, value in expected.items():
        match = re.search(rf"--{flag}\s+([^\s\\]+)", script)
        assert match is not None
        assert float(match.group(1)) == pytest.approx(float(value))


def test_main_training_launchers_match_protocol_config() -> None:
    protocol = read_json(ROOT / "config/main_experiment.json")
    stage1 = _launcher("scripts/train_stage1.sh")
    stage2 = _launcher("scripts/train_stage2.sh")
    _assert_training_flags(stage1, protocol["training"]["stage1"])
    _assert_training_flags(stage2, protocol["training"]["stage2"])
    assert f"--base_model {protocol['base_model']}" in stage1
    assert f"--base_model {protocol['base_model']}" in stage2
    assert "--save_every_epoch" in stage1 and "--save_every_epoch" in stage2
    assert "--existing_model" not in stage1 and "--existing_model" in stage2


def test_ratio_training_launcher_matches_protocol_config() -> None:
    protocol = read_json(ROOT / "config/ratio_ablation.json")
    launcher = _launcher("scripts/train_ratio_arm.sh")
    _assert_training_flags(launcher, protocol["training"])
    assert "--existing_model" in launcher


def test_evaluation_protocol_records_released_seed_sets() -> None:
    main = read_json(ROOT / "config/main_experiment.json")["evaluation"]
    ratio = read_json(ROOT / "config/ratio_ablation.json")["evaluation"]
    assert main["replicate_seeds"] == [101, 202, 303, 404, 505]
    assert ratio["replicate_seeds"] == [101, 202, 303]
    assert main["selector_scenarios"] == ratio["selector_scenarios"] == 100
    assert main["full_scenarios"] == ratio["full_scenarios"] == 1000
