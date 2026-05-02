#!/usr/bin/env bash
#
# PlantDiseases Server — Linux / macOS startup script.
#
# Creates a virtual environment, installs CPU-only PyTorch + server
# dependencies, and starts the FastAPI server.
#
# Usage:
#     chmod +x start.sh
#     ./start.sh              # start server (default port 8000)
#     ./start.sh --port 9000  # custom port
#
# Models are trained in Colab via train_notebook.ipynb. Drop the
# resulting detector.{pt,onnx}, classifier.{pth,onnx} and classes.json
# into server/models/ before starting — the server is fail-fast and
# will refuse to start without trained weights.
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

Train models in Colab via train_notebook.ipynb, then drop
detector.{pt,onnx}, classifier.{pth,onnx} and classes.json into
server/models/. The server will not start without them.
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
        if [[ "${major:-0}" -eq 3 && "${minor:-0}" -ge 9 ]] || [[ "${major:-0}" -gt 3 ]]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "ERROR: Python 3.9+ is required but was not found." >&2
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

echo "[+] Installing PyTorch (CPU-only)..."
pip install --quiet --upgrade pip
pip install --quiet torch torchvision --index-url https://download.pytorch.org/whl/cpu

echo "[+] Installing server dependencies..."
pip install --quiet -r requirements.txt

mkdir -p models logs

# Sanity-check artefacts before exec'ing uvicorn so the operator sees
# the real cause instead of a stack trace from the lifespan handler.
detector_present=0
classifier_present=0
[[ -f models/detector.pt   || -f models/detector.onnx ]]                                       && detector_present=1
[[ -f models/classifier.pth || -f models/classifier.onnx || -f models/classifier_int8.onnx ]] && classifier_present=1
if [[ $detector_present -eq 0 || $classifier_present -eq 0 ]]; then
    echo "[!] Trained model artefacts missing under server/models/." >&2
    echo "    Need detector.{pt,onnx} AND classifier.{pth,onnx,int8.onnx}." >&2
    echo "    Train in Colab via train_notebook.ipynb, then copy them in." >&2
    exit 1
fi

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
