#!/usr/bin/env bash
#
# PlantDiseases Server — Linux / macOS startup script.
#
# Creates a virtual environment, installs the server dependencies, and
# starts the FastAPI server.
#
# Usage:
#     chmod +x start.sh
#     ./start.sh              # start server (default port 8000)
#     ./start.sh --port 9000  # custom port
#
# Model artefacts are NOT committed or auto-downloaded. Extract one complete
# notebook-produced bundle into server/models/ (including bundle_manifest.json
# and its nested directories). Do not mix files from different runs. Startup
# verifies every manifest-bound artefact before loading a model.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${PORT:-8000}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)  PORT="$2"; shift 2 ;;
        -h|--help)
            cat <<USAGE
Usage: $0 [--port PORT]

  --port PORT   Server port (default: 8000)

Model artefacts are not auto-downloaded. Extract one complete bundle produced
by train_notebook.ipynb into server/models/, including bundle_manifest.json.
USAGE
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

echo "PlantDiseases Server — startup"
echo

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))' 2>/dev/null || echo "")
        major="${version%%.*}"
        minor="${version##*.}"
        if [[ "${major:-0}" -eq 3 && "${minor:-0}" -ge 10 ]] || [[ "${major:-0}" -gt 3 ]]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "ERROR: Python 3.10+ is required but was not found." >&2
    echo "       (3.10 is the syntax floor; 3.14 is what CI tests.)" >&2
    echo "Install Python from https://www.python.org/downloads/" >&2
    exit 1
fi
echo "[+] Using $($PYTHON --version)"

if [[ ! -d "venv" ]]; then
    echo "[+] Creating virtual environment..."
    "$PYTHON" -m venv venv
fi

echo "[+] Activating virtual environment..."
# shellcheck disable=SC1091
source venv/bin/activate

pip install --quiet --upgrade pip

echo "[+] Installing server dependencies..."
pip install --quiet -r requirements.txt

mkdir -p models logs

# Model artefacts are NOT committed or auto-downloaded. This shallow check gives
# a clear error before uvicorn starts; the lifespan verifier is authoritative
# and validates the complete manifest, hashes, serving contract, and exact graph.
detector_present=0
bundle_manifest_present=0
[[ -f models/detector.onnx ]]                                             && detector_present=1
[[ -f models/bundle_manifest.json ]]                                      && bundle_manifest_present=1
if [[ $detector_present -eq 0 || $bundle_manifest_present -eq 0 ]]; then
    echo "[!] Complete model bundle missing under server/models/." >&2
    echo "    Extract one full archive produced by train_notebook.ipynb," >&2
    echo "    preserving bundle_manifest.json and all nested paths." >&2
    echo "    Do not copy loose artefacts or combine different runs." >&2
    exit 1
fi

echo "[+] Verifying immutable model bundle..."
python tools/verify_bundle.py models

echo
echo "[+] Starting server on http://0.0.0.0:${PORT}"
echo "    API docs:  http://localhost:${PORT}/docs"
echo "    Health:    http://localhost:${PORT}/api/health"
echo

exec uvicorn main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
    --proxy-headers
