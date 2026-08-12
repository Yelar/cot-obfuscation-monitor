from __future__ import annotations

from pathlib import Path

import pytest

from cot_obfuscation_repro.io import read_json, write_json
from cot_obfuscation_repro.workflow import (
    checkpoint_provenance_matches,
    checkpoint_uri,
    checkpoints_ready,
    write_main_manifest,
)


def _checkpoint_manifest(path: Path) -> None:
    write_json(
        path,
        {
            "schema_version": 1,
            "status": "completed",
            "epochs": {
                "1": {
                    "state_uri": "tinker://example/weights/epoch_1_state",
                    "sampler_uri": "tinker://example/sampler_weights/epoch_1_sampler",
                },
                "2": {
                    "state_uri": "tinker://example/weights/epoch_2_state",
                    "sampler_uri": "tinker://example/sampler_weights/epoch_2_sampler",
                },
            },
        },
    )


def test_checkpoint_helpers_require_both_uri_kinds(tmp_path: Path) -> None:
    manifest = tmp_path / "checkpoints.json"
    _checkpoint_manifest(manifest)

    assert checkpoints_ready(manifest, [1, 2])
    assert checkpoint_uri(manifest, 2, "state").endswith("epoch_2_state")
    assert checkpoint_uri(manifest, 2, "sampler").endswith("epoch_2_sampler")

    payload = read_json(manifest)
    payload["status"] = "running"
    write_json(manifest, payload)
    assert not checkpoints_ready(manifest, [1, 2])

    payload["status"] = "completed"
    del payload["epochs"]["2"]["sampler_uri"]
    write_json(manifest, payload)
    assert not checkpoints_ready(manifest, [1, 2])
    with pytest.raises(ValueError, match="missing sampler_uri"):
        checkpoint_uri(manifest, 2, "sampler")


def test_checkpoint_provenance_must_match_exactly(tmp_path: Path) -> None:
    manifest = tmp_path / "checkpoints.json"
    expected = tmp_path / "expected.json"
    _checkpoint_manifest(manifest)
    payload = read_json(manifest)
    payload["provenance"] = {"dataset": "/data/a", "seed": 0}
    write_json(manifest, payload)
    write_json(expected, {"dataset": "/data/a", "seed": 0})
    assert checkpoint_provenance_matches(manifest, expected)

    write_json(expected, {"dataset": "/data/b", "seed": 0})
    assert not checkpoint_provenance_matches(manifest, expected)


def test_main_manifest_maps_launcher_names_to_result_keys(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    output = run_dir / "main_results_manifest.json"
    launchers = {
        "behavioral": 1,
        "explicit_monitor": 2,
        "a_only": 3,
        "b_only": 4,
        "a_plus_b": 5,
    }
    for launcher, epoch in launchers.items():
        write_json(
            run_dir / "selector" / launcher / "selected.json",
            {
                "selected_epoch": epoch,
                "selected_model": f"tinker://example/sampler_weights/{launcher}",
            },
        )
        write_json(run_dir / "full" / launcher / "aggregate.json", {})

    manifest = write_main_manifest(run_dir, output)

    assert set(manifest["conditions"]) == {"behavioral", "monitor_aware", "a_only", "b_only", "ab"}
    assert manifest["conditions"]["monitor_aware"]["selected_epoch"] == 2
    assert manifest["conditions"]["ab"]["aggregate"] == "full/a_plus_b/aggregate.json"
    assert output.stat().st_mode & 0o777 == 0o600
