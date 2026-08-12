#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ "${SKIP_SETUP:-0}" != "1" ]]; then
  bash scripts/setup.sh offline
fi
make reproduce PYTHON=.venv/bin/python

echo "Offline verification complete. Figures are in plots/generated/."
