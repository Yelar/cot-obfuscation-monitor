#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

main_results="data/results/five_condition_corrected.json"
ratio_results="data/results/behavioral_ratio_corrected.json"
output_dir="plots/generated"
main_only=0

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --main-results) main_results="$2"; shift 2 ;;
    --ratio-results) ratio_results="$2"; shift 2 ;;
    --output-dir) output_dir="$2"; shift 2 ;;
    --main-only) main_only=1; shift ;;
    -h|--help)
      echo "Usage: $0 [--main-results FILE] [--ratio-results FILE] [--output-dir DIR] [--main-only]"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ ! -x .venv/bin/python ]]; then
  bash scripts/setup.sh offline
fi
if [[ "$main_only" == "1" ]]; then
  .venv/bin/python -m cot_obfuscation_repro.summarize --validate-summary "$main_results"
  .venv/bin/python -m cot_obfuscation_repro.plots \
    --main-results "$main_results" --output-dir "$output_dir" --main-only
else
  .venv/bin/python -m cot_obfuscation_repro.summarize \
    --validate-summary "$main_results" "$ratio_results"
  .venv/bin/python -m cot_obfuscation_repro.plots \
    --main-results "$main_results" \
    --ratio-results "$ratio_results" \
    --output-dir "$output_dir"
fi

echo "Figures written to $output_dir/."
