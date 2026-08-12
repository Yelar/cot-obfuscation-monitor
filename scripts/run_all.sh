#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --confirm-paid-run [--run-root DIR]" >&2
}

confirmed=0
run_root=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --confirm-paid-run) confirmed=1; shift ;;
    --run-root)
      [[ "$#" -ge 2 ]] || { usage; exit 2; }
      run_root="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done
[[ "$confirmed" == "1" ]] || { usage; exit 2; }

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
if [[ -z "$run_root" ]]; then
  run_root="artifacts/runs/all-$(date -u +%Y%m%dT%H%M%SZ)"
fi

bash scripts/run_main_experiment.sh \
  --confirm-paid-run --run-dir "$run_root/main"
bash scripts/run_ratio_ablation.sh \
  --confirm-paid-run --main-run "$run_root/main" --run-dir "$run_root/ratio"

echo "Main experiment and ratio ablation completed: $run_root"
