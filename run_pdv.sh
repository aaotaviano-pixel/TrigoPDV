#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
runtime_dir="$project_dir/.venv/linux-runtime"

export PYTHONPATH="$runtime_dir/usr/lib/python3.14:$runtime_dir/usr/lib/python3.14/lib-dynload${PYTHONPATH:+:$PYTHONPATH}"
export LD_LIBRARY_PATH="$runtime_dir/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export TK_LIBRARY="$runtime_dir/usr/share/tcltk/tk8.6"

cd "$project_dir"
exec "$project_dir/.venv/bin/python" main.py "$@"
