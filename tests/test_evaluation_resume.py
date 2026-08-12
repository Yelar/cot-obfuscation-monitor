from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/evaluate_checkpoint.sh"


def _fake_python(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_LOG"
for argument in "$@"; do
  case "$argument" in
    --validate-result|--validate-samples) exit 1 ;;
    --validate-sampling-cache) exit 0 ;;
  esac
done
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(0o700)


def _environment(fake_python: Path, log: Path) -> dict[str, str]:
    return {
        **os.environ,
        "TINKER_API_KEY": "test-only",
        "OPENROUTER_API_KEY": "test-only",
        "PYTHON_BIN": str(fake_python),
        "FAKE_LOG": str(log),
    }


def _script_copy(tmp_path: Path) -> tuple[Path, Path]:
    temporary_root = tmp_path / "repo"
    script = temporary_root / "scripts/evaluate_checkpoint.sh"
    script.parent.mkdir(parents=True)
    script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    script.chmod(0o700)
    return temporary_root, script


def test_invalid_output_is_preserved_and_matching_cache_resumes_sampling(tmp_path: Path) -> None:
    fake_python = tmp_path / "python"
    log = tmp_path / "calls.log"
    output = tmp_path / "evaluation.json"
    cache = tmp_path / "evaluation.json.sampling-cache.json"
    temporary_root, script = _script_copy(tmp_path)
    _fake_python(fake_python)
    output.write_text("{truncated", encoding="utf-8")
    cache.write_text("{}", encoding="utf-8")

    completed = subprocess.run(
        [
            str(script),
            "tinker://example/sampler_weights/epoch_1_sampler",
            "unused-scenarios.json",
            "101",
            str(output),
        ],
        cwd=temporary_root,
        env=_environment(fake_python, log),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Preserved invalid evaluation" in completed.stdout
    assert len(list(tmp_path.glob("evaluation.json.invalid-*"))) == 1
    assert cache.is_file()
    assert "--monitor-only" not in log.read_text(encoding="utf-8").splitlines()[-1]


def test_invalid_output_without_matching_cache_stops_without_moving_it(tmp_path: Path) -> None:
    fake_python = tmp_path / "python"
    log = tmp_path / "calls.log"
    output = tmp_path / "evaluation.json"
    temporary_root, script = _script_copy(tmp_path)
    _fake_python(fake_python)
    output.write_text("{truncated", encoding="utf-8")

    completed = subprocess.run(
        [
            str(script),
            "tinker://example/sampler_weights/epoch_1_sampler",
            "unused-scenarios.json",
            "101",
            str(output),
        ],
        cwd=temporary_root,
        env=_environment(fake_python, log),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "no matching sampling cache" in completed.stderr
    assert output.read_text(encoding="utf-8") == "{truncated"
    assert not list(tmp_path.glob("evaluation.json.invalid-*"))
