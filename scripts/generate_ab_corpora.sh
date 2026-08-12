#!/usr/bin/env bash
set -euo pipefail
umask 077

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
if [[ -f .env ]]; then set -a; source .env; set +a; fi
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY is required}"
python_bin="${PYTHON_BIN:-$repo_root/.venv/bin/python}"
"$python_bin" scripts/generate_behavior_shaped_ab.py --mode full --resume
"$python_bin" scripts/review_behavior_shaped_ab.py --mode full
"$python_bin" scripts/build_behavior_shaped_ab_v2.py
