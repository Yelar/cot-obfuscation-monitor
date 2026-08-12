#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage: scripts/run_ratio_ablation.sh --main-run DIR --confirm-paid-run [--run-dir DIR]

Uses the completed main run's Stage-1 state and endpoint evaluations. Trains
the 12 intermediate ratio arms, selects checkpoints, evaluates three seeds,
aggregates all points, and regenerates Figures 1-3.
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

confirmed=0
main_run_requested=""
run_dir_requested=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --confirm-paid-run) confirmed=1; shift ;;
    --main-run)
      [[ "$#" -ge 2 ]] || { usage; exit 2; }
      main_run_requested="$2"
      shift 2
      ;;
    --run-dir)
      [[ "$#" -ge 2 ]] || { usage; exit 2; }
      run_dir_requested="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -n "$main_run_requested" ]] || { usage; exit 2; }
if [[ "$confirmed" != "1" ]]; then
  cat >&2 <<'EOF'
This workflow starts 12 paid GPT-OSS-120B training runs, 60 selector
evaluations, and 36 full evaluations with hosted monitoring. Review current
provider prices and quotas, then rerun with --confirm-paid-run.
EOF
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
artifacts_root="$(realpath -m "$repo_root/artifacts/runs")"
main_run="$(realpath -m "$main_run_requested")"
case "$main_run" in
  "$artifacts_root"/*) ;;
  *) die "--main-run must be inside $artifacts_root" ;;
esac
[[ -f "$main_run/main_results.json" ]] || die "Main run is incomplete: $main_run/main_results.json is missing"
python3 - "$main_run/run.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(f"Main run ledger is missing: {path}")
record = json.loads(path.read_text(encoding="utf-8"))
if record.get("kind") != "main" or record.get("status") != "completed":
    raise SystemExit(f"Main run is not marked completed: {path}")
PY

if [[ -z "$run_dir_requested" ]]; then
  run_dir_requested="artifacts/runs/ratio-$(date -u +%Y%m%dT%H%M%SZ)"
fi
run_dir="$(realpath -m "$run_dir_requested")"
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
  --run-dir "$run_dir" --kind ratio --status running
"$python_bin" -m cot_obfuscation_repro.prepare --download-author --build-ratios

checkpoint_ready() {
  local manifest="$1"
  shift
  "$python_bin" -m cot_obfuscation_repro.workflow checkpoints-ready \
    "$manifest" --epochs "$@" >/dev/null 2>&1
}

write_provenance() {
  local output="$1"
  local dataset="$2"
  local parent="$3"
  "$python_bin" - "$output" "$dataset" "$parent" <<'PY'
import json, pathlib, sys
output, dataset, parent = sys.argv[1:]
value = {
    "dataset": str(pathlib.Path(dataset).resolve()),
    "base_model": "openai/gpt-oss-120b",
    "existing_model": parent,
    "batch_size": 8,
    "num_epochs": 5.0,
    "learning_rate": 1e-5,
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

stage1_manifest="$main_run/stage1/checkpoints.json"
checkpoint_ready "$stage1_manifest" 1 2 3 4 || die "The main run has no complete Stage-1 manifest"
parent_state="$("$python_bin" -m cot_obfuscation_repro.workflow checkpoint-uri \
  "$stage1_manifest" --epoch 4 --kind state)"

# Validate every reused main endpoint before any paid ratio training starts.
full_scenarios="data/scenarios/scenarios_mixed_1000.json"
endpoint_conditions=(a_only b_only a_plus_b explicit_monitor behavioral)
for condition in "${endpoint_conditions[@]}"; do
  selection="$main_run/selector/$condition/selected.json"
  [[ -f "$selection" ]] || die "Main-run selection is missing: $selection"
  sampler="$("$python_bin" -m cot_obfuscation_repro.workflow selected-value \
    "$selection" --field selected_model)"
  for seed in 101 202 303; do
    endpoint="$main_run/full/$condition/seed_${seed}.json"
    [[ -f "$endpoint" ]] || die "Main-run endpoint is missing: $endpoint"
    "$python_bin" -m cot_obfuscation_repro.evaluate \
      --validate-result "$endpoint" --model "$sampler" --scenarios "$full_scenarios" \
      --replicate-seed "$seed" >/dev/null
  done
done

families=(a_only b_only ab monitor_aware)
percentages=(25 50 75)
arms=()
for family in "${families[@]}"; do
  for percentage in "${percentages[@]}"; do
    arm="${family}_behavioral_${percentage}"
    arms+=("$arm")
    arm_dir="$run_dir/training/$arm"
    manifest="$arm_dir/checkpoints.json"
    expected="$arm_dir/expected_provenance.json"
    mkdir -p "$arm_dir"
    write_provenance "$expected" "$repo_root/artifacts/datasets/ratios/$arm" "$parent_state"
    if checkpoint_ready "$manifest" 1 2 3 4 5 && \
        "$python_bin" -m cot_obfuscation_repro.workflow provenance-matches \
          "$manifest" --expected "$expected"; then
      echo "Reusing completed ratio checkpoints: $arm"
    elif [[ -e "$manifest" ]]; then
      die "Checkpoint manifest for $arm is partial and preserved at $manifest; use a new run directory to retrain it."
    else
      bash scripts/train_ratio_arm.sh "$arm" "$parent_state" "$manifest" \
        2>&1 | tee "$arm_dir/training.log"
      checkpoint_ready "$manifest" 1 2 3 4 5 || die "Ratio training did not record all checkpoints: $arm"
    fi
  done
done

selector_scenarios="data/scenarios/frozen_defect_present_100.json"
full_seeds=(101 202 303)

for arm in "${arms[@]}"; do
  manifest="$run_dir/training/$arm/checkpoints.json"
  selector_dir="$run_dir/selector/$arm"
  mkdir -p "$selector_dir"
  for epoch in 1 2 3 4 5; do
    sampler="$("$python_bin" -m cot_obfuscation_repro.workflow checkpoint-uri \
      "$manifest" --epoch "$epoch" --kind sampler)"
    run_evaluation "$sampler" "$selector_scenarios" 101 "$selector_dir/epoch_${epoch}.json"
  done
  "$python_bin" -m cot_obfuscation_repro.select \
    "$selector_dir"/epoch_*.json --output "$selector_dir/selected.json"
done

full_sha="$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["evaluation_scenarios"]["full_1000"]["sha256"])' config/datasets.json)"
for arm in "${arms[@]}"; do
  selection="$run_dir/selector/$arm/selected.json"
  sampler="$("$python_bin" -m cot_obfuscation_repro.workflow selected-value \
    "$selection" --field selected_model)"
  full_dir="$run_dir/full/$arm"
  mkdir -p "$full_dir"
  result_files=()
  for seed in "${full_seeds[@]}"; do
    output="$full_dir/seed_${seed}.json"
    run_evaluation "$sampler" "$full_scenarios" "$seed" "$output"
    result_files+=("$output")
  done
  "$python_bin" -m cot_obfuscation_repro.aggregate "${result_files[@]}" \
    --expected-seeds "${full_seeds[@]}" --expected-rows 1000 \
    --expected-scenario-sha "$full_sha" --scenarios "$full_scenarios" \
    --output "$full_dir/aggregate.json"
done

mkdir -p "$run_dir/endpoints"
endpoint_conditions=(a_only b_only a_plus_b explicit_monitor)
for index in "${!families[@]}"; do
  family="${families[$index]}"
  condition="${endpoint_conditions[$index]}"
  endpoint_files=()
  for seed in "${full_seeds[@]}"; do
    endpoint_files+=("$main_run/full/$condition/seed_${seed}.json")
  done
  "$python_bin" -m cot_obfuscation_repro.aggregate "${endpoint_files[@]}" \
    --expected-seeds "${full_seeds[@]}" --expected-rows 1000 \
    --expected-scenario-sha "$full_sha" --scenarios "$full_scenarios" \
    --output "$run_dir/endpoints/${family}_behavioral_0_aggregate.json"
done

behavioral_files=()
for seed in "${full_seeds[@]}"; do
  behavioral_files+=("$main_run/full/behavioral/seed_${seed}.json")
done
"$python_bin" -m cot_obfuscation_repro.aggregate "${behavioral_files[@]}" \
  --expected-seeds "${full_seeds[@]}" --expected-rows 1000 \
  --expected-scenario-sha "$full_sha" --scenarios "$full_scenarios" \
  --output "$run_dir/endpoints/behavioral_100_aggregate.json"

ratio_manifest="$run_dir/ratio_results_manifest.json"
ratio_results="$run_dir/ratio_results.json"
"$python_bin" -m cot_obfuscation_repro.workflow write-ratio-manifest \
  --run-dir "$run_dir" --main-run "$main_run" --output "$ratio_manifest"
"$python_bin" -m cot_obfuscation_repro.summarize \
  --manifest "$ratio_manifest" --output "$ratio_results"
bash scripts/build_figures.sh \
  --main-results "$main_run/main_results.json" \
  --ratio-results "$ratio_results" \
  --output-dir "$run_dir/plots"
"$python_bin" -m cot_obfuscation_repro.workflow record-run \
  --run-dir "$run_dir" --kind ratio --status completed

echo "Ratio ablation completed: $run_dir"
