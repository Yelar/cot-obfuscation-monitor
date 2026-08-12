#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [--ratios]" >&2
}

build_ratios=0
for argument in "$@"; do
  case "$argument" in
    --ratios) build_ratios=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ "${SKIP_SETUP:-0}" != "1" ]]; then
  bash scripts/setup.sh data
fi
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

arguments=(--download-author)
if [[ "$build_ratios" == "1" ]]; then
  arguments+=(--build-ratios)
fi
.venv/bin/python -m cot_obfuscation_repro.prepare "${arguments[@]}"

echo "Pinned training data are ready under artifacts/datasets/."
