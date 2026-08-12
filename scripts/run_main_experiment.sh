#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage: scripts/run_main_experiment.sh --confirm-paid-run [--run-dir DIR]

Runs pinned data preparation, Stage 1, all five Stage-2 conditions, the
100-scenario checkpoint selector, five-seed 1,000-scenario evaluation,
aggregation, and figure generation. Existing validated outputs are reused.
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

confirmed=0
requested_run_dir=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --confirm-paid-run) confirmed=1; shift ;;
    --run-dir)
      [[ "$#" -ge 2 ]] || { usage; exit 2; }
      requested_run_dir="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ "$confirmed" != "1" ]]; then
  cat >&2 <<'EOF'
This workflow starts 6 paid GPT-OSS-120B training runs, 25 selector
evaluations, and 25 full evaluations with hosted monitoring. Review current
provider prices and quotas, then rerun with --confirm-paid-run.
EOF
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ -z "$requested_run_dir" ]]; then
  requested_run_dir="artifacts/runs/main-$(date -u +%Y%m%dT%H%M%SZ)"
fi
run_dir="$(realpath -m "$requested_run_dir")"
artifacts_root="$(realpath -m "$repo_root/artifacts/runs")"
case "$run_dir" in
  "$artifacts_root"/*) ;;
  *) die "--run-dir must be inside $artifacts_root so private artifacts stay ignored" ;;
esac
mkdir -p "$run_dir"
chmod 0700 "$run_dir"
lock_dir="$run_dir/.workflow.lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
  die "Another workflow is using this run directory, or a stale lock exists: $lock_dir"
fi
printf '%s\n' "$$" >"$lock_dir/pid"
trap 'rm -f "$lock_dir/pid"; rmdir "$lock_dir" 2>/dev/null || true' EXIT

bash scripts/setup.sh full
[[ -f .env ]] || die "Missing .env; run scripts/setup.sh full"
set -a
source .env
set +a

require_key() {
  local name="$1"
  local value="${!name:-}"
  [[ -n "$value" ]] || die "$name is empty in .env"
  case "$value" in
    *replace*|*REPLACE*|*your-key*|*YOUR-KEY*) die "$name still contains a placeholder" ;;
  esac
}
require_key TINKER_API_KEY
require_key OPENROUTER_API_KEY

python_bin="${PYTHON_BIN:-$repo_root/.venv/bin/python}"
[[ -x "$python_bin" ]] || die "Python interpreter is not executable: $python_bin"

"$python_bin" -m cot_obfuscation_repro.workflow record-run \
  --run-dir "$run_dir" --kind main --status running
"$python_bin" -m cot_obfuscation_repro.prepare --download-author

checkpoint_ready() {
  local manifest="$1"
  shift
  "$python_bin" -m cot_obfuscation_repro.workflow checkpoints-ready \
    "$manifest" --epochs "$@" >/dev/null 2>&1
}

write_provenance() {
  local output="$1"
  local dataset="$2"
  local parent="${3:-}"
  local epochs="$4"
  local batch_size="$5"
  local learning_rate="$6"
  "$python_bin" - "$output" "$dataset" "$parent" "$epochs" "$batch_size" "$learning_rate" <<'PY'
import json, pathlib, sys
output, dataset, parent, epochs, batch_size, learning_rate = sys.argv[1:]
value = {
    "dataset": str(pathlib.Path(dataset).resolve()),
    "base_model": "openai/gpt-oss-120b",
    "existing_model": parent or None,
    "batch_size": int(batch_size),
    "num_epochs": float(epochs),
    "learning_rate": float(learning_rate),
    "rank": 32,
    "seed": 0,
    "shuffle_seed": 0,
    "vary_shuffle_each_epoch": False,
    "load_optimizer_state": False,
    "max_seq_len": 0,
    "save_every_epoch": True,
}
path = pathlib.Path(output)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
}

run_evaluation() {
  local model="$1"
  local scenarios="$2"
  local seed="$3"
  local output="$4"
  mkdir -p "$(dirname "$output")"
  if [[ -f "$output" ]] && "$python_bin" -m cot_obfuscation_repro.evaluate \
      --validate-result "$output" --model "$model" --scenarios "$scenarios" \
      --replicate-seed "$seed" >/dev/null 2>&1; then
    echo "Reusing completed evaluation: $output"
    return
  fi
  if [[ -f "$output" ]]; then
    echo "Recovering or completing existing evaluation: $output"
    bash scripts/evaluate_checkpoint.sh "$model" "$scenarios" "$seed" "$output"
    "$python_bin" -m cot_obfuscation_repro.evaluate \
      --validate-result "$output" --model "$model" --scenarios "$scenarios" \
      --replicate-seed "$seed" >/dev/null
    return
  fi
  bash scripts/evaluate_checkpoint.sh "$model" "$scenarios" "$seed" "$output"
  "$python_bin" -m cot_obfuscation_repro.evaluate \
    --validate-result "$output" --model "$model" --scenarios "$scenarios" \
    --replicate-seed "$seed" >/dev/null
}

stage1_dir="$run_dir/stage1"
stage1_manifest="$stage1_dir/checkpoints.json"
stage1_expected="$stage1_dir/expected_provenance.json"
mkdir -p "$stage1_dir"
write_provenance "$stage1_expected" "$repo_root/artifacts/datasets/author/stage1_behavioral_1272" "" 4 32 1e-4
if checkpoint_ready "$stage1_manifest" 1 2 3 4 && \
    "$python_bin" -m cot_obfuscation_repro.workflow provenance-matches \
      "$stage1_manifest" --expected "$stage1_expected"; then
  echo "Reusing completed Stage 1 checkpoints."
elif [[ -e "$stage1_manifest" ]]; then
  die "Stage 1 manifest is partial. It was preserved at $stage1_manifest; use a new run directory to retrain."
else
  bash scripts/train_stage1.sh "$stage1_manifest" 2>&1 | tee "$stage1_dir/training.log"
  checkpoint_ready "$stage1_manifest" 1 2 3 4 || die "Stage 1 did not record all four checkpoints"
fi
parent_state="$("$python_bin" -m cot_obfuscation_repro.workflow checkpoint-uri \
  "$stage1_manifest" --epoch 4 --kind state)"

conditions=(behavioral explicit_monitor a_only b_only a_plus_b)
for condition in "${conditions[@]}"; do
  condition_dir="$run_dir/stage2/$condition"
  manifest="$condition_dir/checkpoints.json"
  expected="$condition_dir/expected_provenance.json"
  mkdir -p "$condition_dir"
  case "$condition" in
    behavioral) dataset="$repo_root/artifacts/datasets/author/stage2_behavioral_1000" ;;
    explicit_monitor) dataset="$repo_root/artifacts/datasets/author/stage2_explicit_monitor_1000" ;;
    *) dataset="$repo_root/data/training/compositional/$condition" ;;
  esac
  write_provenance "$expected" "$dataset" "$parent_state" 5 8 1e-5
  if checkpoint_ready "$manifest" 1 2 3 4 5 && \
      "$python_bin" -m cot_obfuscation_repro.workflow provenance-matches \
        "$manifest" --expected "$expected"; then
    echo "Reusing completed Stage 2 checkpoints: $condition"
  elif [[ -e "$manifest" ]]; then
    die "Stage 2 manifest for $condition is partial and preserved at $manifest; use a new run directory to retrain it."
  else
    bash scripts/train_stage2.sh "$condition" "$parent_state" "$manifest" \
      2>&1 | tee "$condition_dir/training.log"
    checkpoint_ready "$manifest" 1 2 3 4 5 || die "Stage 2 did not record all checkpoints: $condition"
  fi
done

selector_scenarios="data/scenarios/frozen_defect_present_100.json"
full_scenarios="data/scenarios/scenarios_mixed_1000.json"
selector_seed=101
full_seeds=(101 202 303 404 505)

for condition in "${conditions[@]}"; do
  manifest="$run_dir/stage2/$condition/checkpoints.json"
  selector_dir="$run_dir/selector/$condition"
  mkdir -p "$selector_dir"
  for epoch in 1 2 3 4 5; do
    sampler="$("$python_bin" -m cot_obfuscation_repro.workflow checkpoint-uri \
      "$manifest" --epoch "$epoch" --kind sampler)"
    run_evaluation "$sampler" "$selector_scenarios" "$selector_seed" \
      "$selector_dir/epoch_${epoch}.json"
  done
  "$python_bin" -m cot_obfuscation_repro.select \
    "$selector_dir"/epoch_*.json --output "$selector_dir/selected.json"
done

full_sha="$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["evaluation_scenarios"]["full_1000"]["sha256"])' config/datasets.json)"
for condition in "${conditions[@]}"; do
  selection="$run_dir/selector/$condition/selected.json"
  sampler="$("$python_bin" -m cot_obfuscation_repro.workflow selected-value \
    "$selection" --field selected_model)"
  full_dir="$run_dir/full/$condition"
  mkdir -p "$full_dir"
  result_files=()
  for seed in "${full_seeds[@]}"; do
    output="$full_dir/seed_${seed}.json"
    run_evaluation "$sampler" "$full_scenarios" "$seed" "$output"
    result_files+=("$output")
  done
  "$python_bin" -m cot_obfuscation_repro.aggregate "${result_files[@]}" \
    --expected-seeds "${full_seeds[@]}" \
    --expected-rows 1000 \
    --expected-scenario-sha "$full_sha" \
    --scenarios "$full_scenarios" \
    --output "$full_dir/aggregate.json"
done

main_manifest="$run_dir/main_results_manifest.json"
main_results="$run_dir/main_results.json"
"$python_bin" -m cot_obfuscation_repro.workflow write-main-manifest \
  --run-dir "$run_dir" --output "$main_manifest"
"$python_bin" -m cot_obfuscation_repro.summarize \
  --manifest "$main_manifest" --output "$main_results"
bash scripts/build_figures.sh \
  --main-results "$main_results" \
  --output-dir "$run_dir/plots" \
  --main-only
"$python_bin" -m cot_obfuscation_repro.workflow record-run \
  --run-dir "$run_dir" --kind main --status completed

echo "Main experiment completed: $run_dir"
