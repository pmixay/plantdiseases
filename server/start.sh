#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  PlantDiseases Server — Linux / macOS startup script
#
#  Creates a virtual environment, installs lightweight (CPU-only)
#  PyTorch + dependencies, and starts the FastAPI server.
#
#  Usage:
#      chmod +x start.sh
#      ./start.sh              # start server (default port 8000)
#      ./start.sh --port 9000  # custom port
#      ./start.sh --train      # train models before starting
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PORT=8000
TRAIN=false

# ── Parse arguments ──────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)  PORT="$2"; shift 2 ;;
        --train) TRAIN=true; shift ;;
        -h|--help)
            echo "Usage: $0 [--port PORT] [--train]"
            echo ""
            echo "  --port PORT   Server port (default: 8000)"
            echo "  --train       Train models before starting the server"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "╔══════════════════════════════════════════════════╗"
echo "║       PlantDiseases Server — Startup             ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Check Python ─────────────────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [[ "$major" -ge 3 && "$minor" -ge 9 ]]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "ERROR: Python 3.9+ is required but not found."
    echo "Install Python from https://www.python.org/downloads/"
    exit 1
fi
echo "[+] Using $($PYTHON --version)"

# ── Create virtual environment ───────────────────────────────────────
if [[ ! -d "venv" ]]; then
    echo "[+] Creating virtual environment..."
    "$PYTHON" -m venv venv
fi

echo "[+] Activating virtual environment..."
# shellcheck disable=SC1091
source venv/bin/activate

# ── Install dependencies ─────────────────────────────────────────────
echo "[+] Installing PyTorch (CPU-only — lightweight)..."
pip install --quiet --upgrade pip
pip install --quiet torch torchvision --index-url https://download.pytorch.org/whl/cpu

if [[ "$TRAIN" == true ]]; then
    echo "[+] Installing training dependencies..."
    pip install --quiet -r requirements-train.txt
else
    echo "[+] Installing server dependencies..."
    pip install --quiet -r requirements.txt
fi

# ── Create models directory ──────────────────────────────────────────
mkdir -p models

# ── Optional training ────────────────────────────────────────────────
if [[ "$TRAIN" == true ]]; then
    echo ""
    echo "[+] Starting model training..."
    python train.py
    echo ""
fi

# ── Check models ─────────────────────────────────────────────────────
echo ""
if [[ -f "models/detector.pth" && -f "models/classifier.pth" ]]; then
    echo "[+] Both models found — running in FULL mode"
elif [[ -f "models/detector.pth" || -f "models/classifier.pth" ]]; then
    echo "[!] Only one model found — running in PARTIAL mode"
else
    echo "[!] No trained models found — running in DEMO mode"
    echo "    To train models:  ./start.sh --train"
    echo "    (requires data/ directory with PlantVillage dataset)"
fi

# ── Start server ─────────────────────────────────────────────────────
echo ""
echo "[+] Starting server on http://0.0.0.0:${PORT}"
echo "    API docs:  http://localhost:${PORT}/docs"
echo "    Health:    http://localhost:${PORT}/api/health"
echo ""

exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
