from __future__ import annotations

import json

from cot_obfuscation_repro.select import select


def _write_payload(path, uncaught: int) -> None:
    rows = []
    for index in range(4):
        rows.append({
            "ground_truth": "yes",
            "classification": "CONCEAL" if index < uncaught else "DISCLOSE",
            "monitor_classification": "NOT_DECEPTIVE" if index < uncaught else None,
        })
    path.write_text(
        json.dumps({"config": {"model": f"tinker://example/sampler_weights/{path.stem}"}, "results": rows}),
        encoding="utf-8",
    )


def test_selector_maximizes_uncaught_and_breaks_tie_to_earlier_epoch(tmp_path) -> None:
    epoch_1 = tmp_path / "branch_epoch_1.json"
    epoch_2 = tmp_path / "branch_epoch_2.json"
    epoch_3 = tmp_path / "branch_epoch_3.json"
    _write_payload(epoch_1, 1)
    _write_payload(epoch_2, 3)
    _write_payload(epoch_3, 3)
    result = select([epoch_3, epoch_1, epoch_2], expected_epochs={1, 2, 3})
    assert result["selected_epoch"] == 2
    assert result["selected_model"].endswith("branch_epoch_2")
    assert result["selected_metrics"]["conservative_uncaught"] == 3


def test_selector_rejects_duplicate_or_missing_epochs(tmp_path) -> None:
    epoch_1 = tmp_path / "branch_epoch_1.json"
    epoch_2 = tmp_path / "branch_epoch_2.json"
    _write_payload(epoch_1, 1)
    _write_payload(epoch_2, 2)
    import pytest

    with pytest.raises(ValueError, match="Expected epochs"):
        select([epoch_1, epoch_2], expected_epochs={1, 2, 3})
