#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.11 or newer is required. Install python3, then run this script again." >&2
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating the local admin-panel environment..."
  "$PYTHON_BIN" -m venv .venv
fi

echo "Checking admin-panel dependencies..."
".venv/bin/python" -m pip install --disable-pip-version-check --quiet -r requirements.txt
exec ".venv/bin/python" run.py "$@"
