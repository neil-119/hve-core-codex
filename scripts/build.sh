#!/usr/bin/env bash
# Build a distribution outside the source repository; never run upstream code.
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ -n "${HVE_BUILD_PYTHON:-}" ]]; then
  python_command=("$HVE_BUILD_PYTHON")
elif command -v uv >/dev/null 2>&1; then
  python_command=(uv run --project "$repo_root" --locked python)
else
  python_command=(python3)
fi
export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"
exec "${python_command[@]}" "$repo_root/scripts/build_distribution.py" "$@"
