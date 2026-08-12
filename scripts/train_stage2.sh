#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ "$#" -lt 2 || "$#" -gt 3 ]]; then
  echo "Usage: $0 behavioral|explicit_monitor|a_only|b_only|a_plus_b TINKER_STAGE1_STATE_URI [CHECKPOINT_MANIFEST]" >&2
  exit 2
fi
condition="$1"
parent_state="$2"
checkpoint_manifest="${3:-}"
manifest_args=()
if [[ -n "$checkpoint_manifest" ]]; then
  manifest_args=(--checkpoint_manifest "$checkpoint_manifest")
fi
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
if [[ -f .env ]]; then set -a; source .env; set +a; fi
: "${TINKER_API_KEY:?TINKER_API_KEY is required}"
[[ "$parent_state" == tinker://*/weights/* ]] || { echo "Second argument must be a Tinker state URI." >&2; exit 2; }
case "$condition" in
  behavioral) dataset="$repo_root/artifacts/datasets/author/stage2_behavioral_1000" ;;
  explicit_monitor) dataset="$repo_root/artifacts/datasets/author/stage2_explicit_monitor_1000" ;;
  a_only|b_only|a_plus_b) dataset="$repo_root/data/training/compositional/$condition" ;;
  *) echo "Unknown condition: $condition" >&2; exit 2 ;;
esac
[[ -f "$dataset/synth_docs.jsonl" ]] || { echo "Missing dataset: $dataset/synth_docs.jsonl" >&2; exit 1; }
python_bin="${PYTHON_BIN:-$repo_root/.venv/bin/python}"
exec "$python_bin" scripts/train_sdft.py \
  --dataset "$dataset" --train_split train --eval_split "" \
  --base_model openai/gpt-oss-120b --text_field text --existing_model "$parent_state" \
  --learning_rate 1e-5 --batch_size 8 --rank 32 --num_epochs 5 \
  --seed 0 --shuffle_seed 0 --max_seq_len 0 --save_every_epoch \
  --print_every 1 --output_name "compositional-monitor-stage2-${condition//_/-}" \
  "${manifest_args[@]}"
