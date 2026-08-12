#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ "$#" -lt 2 || "$#" -gt 3 ]]; then
  echo "Usage: $0 FAMILY_behavioral_PERCENT TINKER_STAGE1_STATE_URI [CHECKPOINT_MANIFEST]" >&2
  exit 2
fi
arm="$1"
parent_state="$2"
checkpoint_manifest="${3:-}"
manifest_args=()
if [[ -n "$checkpoint_manifest" ]]; then
  manifest_args=(--checkpoint_manifest "$checkpoint_manifest")
fi
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
dataset="$repo_root/artifacts/datasets/ratios/$arm"
[[ -f "$dataset/synth_docs.jsonl" ]] || { echo "Run cot-prepare --download-author --build-ratios first." >&2; exit 1; }
if [[ -f "$repo_root/.env" ]]; then set -a; source "$repo_root/.env"; set +a; fi
: "${TINKER_API_KEY:?TINKER_API_KEY is required}"
[[ "$parent_state" == tinker://*/weights/* ]] || { echo "Second argument must be a Tinker state URI." >&2; exit 2; }
python_bin="${PYTHON_BIN:-$repo_root/.venv/bin/python}"
exec "$python_bin" "$repo_root/scripts/train_sdft.py" \
  --dataset "$dataset" --train_split train --eval_split "" \
  --base_model openai/gpt-oss-120b --text_field text --existing_model "$parent_state" \
  --learning_rate 1e-5 --batch_size 8 --rank 32 --num_epochs 5 \
  --seed 0 --shuffle_seed 0 --max_seq_len 0 --save_every_epoch \
  --print_every 1 --output_name "compositional-monitor-ratio-${arm//_/-}" \
  "${manifest_args[@]}"
