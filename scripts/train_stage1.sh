#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ "$#" -gt 1 ]]; then
  echo "Usage: $0 [CHECKPOINT_MANIFEST]" >&2
  exit 2
fi
checkpoint_manifest="${1:-}"
manifest_args=()
if [[ -n "$checkpoint_manifest" ]]; then
  manifest_args=(--checkpoint_manifest "$checkpoint_manifest")
fi
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
if [[ -f .env ]]; then set -a; source .env; set +a; fi
: "${TINKER_API_KEY:?TINKER_API_KEY is required}"
python_bin="${PYTHON_BIN:-$repo_root/.venv/bin/python}"
dataset="$repo_root/artifacts/datasets/author/stage1_behavioral_1272"
[[ -f "$dataset/synth_docs.jsonl" ]] || { echo "Run 'make prepare-author' first." >&2; exit 1; }
exec "$python_bin" scripts/train_sdft.py \
  --dataset "$dataset" --train_split train --eval_split "" \
  --base_model openai/gpt-oss-120b --text_field text \
  --learning_rate 1e-4 --batch_size 32 --rank 32 --num_epochs 4 \
  --seed 0 --shuffle_seed 0 --max_seq_len 0 --save_every_epoch \
  --print_every 1 --output_name compositional-monitor-stage1-behavioral-1272 \
  "${manifest_args[@]}"
