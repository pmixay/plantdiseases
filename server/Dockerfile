# syntax=docker/dockerfile:1.6
# ── Builder stage ────────────────────────────────────────────────────
# Pip + apt artefacts stay in this stage; only the resulting wheels
# travel to the runtime image. Saves ~700 MB and trims the runtime
# attack surface (no compiler, no apt-get, no pip cache).
FROM python:3.11.9-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Build deps for any wheels that need to compile
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# Install requirements
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# ── Runtime stage ────────────────────────────────────────────────────
FROM python:3.11.9-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Cap NumPy/MKL's OpenMP pools to 1 — the heavy lifting is ONNX
    # Runtime's own intra-op threadpool (driven by ONNX_INTRA_OP_THREADS,
    # default = all physical cores), which is independent of these vars on
    # the standard non-OpenMP ORT wheel. Pinning NumPy/MKL keeps the small
    # pre/post-processing ops from spawning competing pools while a scan's
    # ViT forward is using the cores. With INFER_WORKERS=1 (one inference
    # at a time) that single scan gets the whole CPU.
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

WORKDIR /app

# Runtime libraries only — OpenCV needs libgl1 / libglib2 / libgomp1.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# Pre-built wheels from the builder stage.
COPY --from=builder /install /usr/local

# Application code — flat copy keeps `from main import …` working.
COPY *.py ./
COPY tools ./tools

# Logs + models live on bind mounts; create dirs for the volume targets.
# /app/data is the SQLite + thumbnail bind-mount target — declared here
# so the container can run unmounted (dev mode) without crashing on a
# missing path.
RUN mkdir -p /app/logs /app/models /app/data && \
    adduser --disabled-password --gecos "" --uid 10001 plantd && \
    chown -R plantd:plantd /app

VOLUME ["/app/models", "/app/data"]

USER plantd

EXPOSE 8000

# Lightweight healthcheck — uvicorn answers /api/health in <5 ms.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD \
    python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=2).status==200 else 1)" || exit 1

# `--limit-concurrency 64` caps simultaneously-accepted connections —
# beyond that uvicorn returns 503 instead of queueing unboundedly. With
# INFER_WORKERS=1 a scan's heavy work is serialised through one executor
# slot anyway; a 64-deep accept queue tolerates short bursts without
# letting a slowloris-style flood exhaust the event loop.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--forwarded-allow-ips", "127.0.0.1", "--proxy-headers", \
     "--limit-concurrency", "64", "--timeout-keep-alive", "5"]
