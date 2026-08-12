#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 offline|data|full" >&2
}

mode="${1:-offline}"
case "$mode" in
  offline|data|full) ;;
  *) usage; exit 2 ;;
esac

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_root/.runtime/uv-cache}"
mkdir -p "$UV_CACHE_DIR"

python_cmd="${BOOTSTRAP_PYTHON:-python3}"
"$python_cmd" - <<'PY'
import sys
if sys.version_info[:2] not in {(3, 11), (3, 12)}:
    raise SystemExit("Python 3.11 or 3.12 is required")
PY

if command -v uv >/dev/null 2>&1; then
  uv_cmd=(uv)
elif "$python_cmd" -m uv --version >/dev/null 2>&1; then
  uv_cmd=("$python_cmd" -m uv)
else
  echo "uv is required. Install it once using the official installer or pipx." >&2
  exit 1
fi

case "$mode" in
  offline) "${uv_cmd[@]}" sync --extra dev --locked ;;
  data) "${uv_cmd[@]}" sync --extra data --extra dev --locked ;;
  full) "${uv_cmd[@]}" sync --all-extras --locked ;;
esac

if [[ "$mode" == "full" ]]; then
  if [[ -L .env ]]; then
    echo ".env must be a regular local file, not a symbolic link" >&2
    exit 1
  elif [[ ! -e .env ]]; then
    install -m 0600 .env.example .env
    echo "Created .env with mode 0600. Fill the keys needed for your workflow."
  else
    chmod 0600 .env
  fi
fi

.venv/bin/python -m cot_obfuscation_repro.evaluate \
  --validate-only --scenarios data/scenarios/frozen_defect_present_100.json >/dev/null
.venv/bin/python -m cot_obfuscation_repro.evaluate \
  --validate-only --scenarios data/scenarios/scenarios_mixed_1000.json >/dev/null

echo "Environment ready in $repo_root/.venv ($mode mode)."
