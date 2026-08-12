#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ "$#" -ne 4 ]]; then
  echo "Usage: $0 TINKER_SAMPLER_URI SCENARIO_JSON REPLICATE_SEED OUTPUT_JSON" >&2
  exit 2
fi
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
if [[ -f .env ]]; then set -a; source .env; set +a; fi
: "${TINKER_API_KEY:?TINKER_API_KEY is required}"
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY is required}"
[[ "$1" == tinker://*/sampler_weights/* ]] || { echo "First argument must be a Tinker sampler URI." >&2; exit 2; }
python_bin="${PYTHON_BIN:-$repo_root/.venv/bin/python}"
mode_args=()
if [[ -f "$4" ]]; then
  if "$python_bin" -m cot_obfuscation_repro.evaluate \
      --validate-result "$4" --model "$1" --scenarios "$2" \
      --replicate-seed "$3" >/dev/null 2>&1; then
    echo "Reusing completed evaluation: $4"
    exit 0
  fi
  if "$python_bin" -m cot_obfuscation_repro.evaluate \
      --validate-samples "$4" --model "$1" --scenarios "$2" \
      --replicate-seed "$3" >/dev/null 2>&1; then
    mode_args=(--monitor-only)
  else
    sampling_cache="$4.sampling-cache.json"
    if [[ ! -f "$sampling_cache" ]] || ! "$python_bin" -m cot_obfuscation_repro.evaluate \
        --validate-sampling-cache "$sampling_cache" --model "$1" --scenarios "$2" \
        --replicate-seed "$3" >/dev/null 2>&1; then
      echo "error: $4 is not a resumable sample payload, and no matching sampling cache is available." >&2
      echo "The existing file was preserved. Move it aside only if you intend to restart target sampling." >&2
      exit 1
    fi
    recovery="$4.invalid-$(date -u +%Y%m%dT%H%M%SZ)"
    counter=0
    while [[ -e "$recovery" ]]; do
      counter=$((counter + 1))
      recovery="$4.invalid-$(date -u +%Y%m%dT%H%M%SZ)-$counter"
    done
    mv -- "$4" "$recovery"
    echo "Preserved invalid evaluation as $recovery; resuming target sampling from $sampling_cache"
  fi
fi
exec "$python_bin" -m cot_obfuscation_repro.evaluate \
  --model "$1" --scenarios "$2" --replicate-seed "$3" --output "$4" \
  --workers "${MONITOR_WORKERS:-12}" --api-attempts 5 --timeout-seconds 180 \
  "${mode_args[@]}"
