from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path
from types import ModuleType

import pytest

from cot_obfuscation_repro.io import ROOT


def _load_training_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    datasets = ModuleType("datasets")
    datasets.load_dataset = object()  # type: ignore[attr-defined]
    tinker = ModuleType("tinker")
    tinker.types = ModuleType("tinker.types")  # type: ignore[attr-defined]
    cookbook = ModuleType("tinker_cookbook")
    cookbook.renderers = object()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "datasets", datasets)
    monkeypatch.setitem(sys.modules, "tinker", tinker)
    monkeypatch.setitem(sys.modules, "tinker.types", tinker.types)
    monkeypatch.setitem(sys.modules, "tinker_cookbook", cookbook)

    path = ROOT / "scripts/train_sdft.py"
    spec = importlib.util.spec_from_file_location("test_train_sdft", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_checkpoint_uri_validation_distinguishes_state_and_sampler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    training = _load_training_module(monkeypatch)
    state_uri = "tinker://example:train:0/weights/epoch_1_state"
    sampler_uri = "tinker://example:train:0/sampler_weights/epoch_1_sampler"

    assert training.validate_checkpoint_uri(state_uri, "state") == state_uri
    assert training.validate_checkpoint_uri(sampler_uri, "sampler") == sampler_uri
    with pytest.raises(ValueError, match="Tinker state URI"):
        training.validate_checkpoint_uri(sampler_uri, "state")
    with pytest.raises(ValueError, match="Tinker sampler URI"):
        training.validate_checkpoint_uri(state_uri, "sampler")


def test_checkpoint_manifest_is_incremental_private_and_normalized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    training = _load_training_module(monkeypatch)
    path = tmp_path / "nested/checkpoints.json"
    provenance = {"dataset": "/tmp/dataset", "base_model": "openai/gpt-oss-120b"}
    manifest = training.CheckpointManifest(str(path), provenance)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "status": "running",
        "provenance": provenance,
        "epochs": {},
        "steps": {},
        "final": {},
    }

    manifest.record(
        kind="state",
        scope="epoch",
        uri="tinker://example:train:0/weights/epoch_1_state",
        epoch=1.0,
        step=40,
    )
    manifest.record(
        kind="sampler",
        scope="epoch",
        uri="tinker://example:train:0/sampler_weights/epoch_1_sampler",
        epoch=1,
        step=40,
    )
    manifest.record(
        kind="state",
        scope="step",
        uri="tinker://example:train:0/weights/step_000010_state",
        step=10,
    )
    manifest.record(
        kind="sampler",
        scope="final",
        uri="tinker://example:train:0/sampler_weights/final",
        step=40,
    )
    manifest.mark_completed()

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "status": "completed",
        "provenance": provenance,
        "epochs": {
            "1": {
                "state_uri": "tinker://example:train:0/weights/epoch_1_state",
                "sampler_uri": (
                    "tinker://example:train:0/sampler_weights/epoch_1_sampler"
                ),
                "step": 40,
            }
        },
        "steps": {
            "10": {
                "state_uri": "tinker://example:train:0/weights/step_000010_state"
            }
        },
        "final": {
            "sampler_uri": "tinker://example:train:0/sampler_weights/final"
        },
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(path.parent.glob(".*.tmp")) == []


def test_checkpoint_manifest_refuses_to_replace_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    training = _load_training_module(monkeypatch)
    path = tmp_path / "checkpoints.json"
    path.write_text("preserve me", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Refusing to replace"):
        training.CheckpointManifest(str(path), {"dataset": "example"})
    assert path.read_text(encoding="utf-8") == "preserve me"
