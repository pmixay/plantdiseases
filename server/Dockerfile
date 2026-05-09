# syntax=docker/dockerfile:1.6
# ── Builder stage ────────────────────────────────────────────────────
# Pip + apt artefacts stay in this stage; only the resulting wheels
# travel to the runtime image. Saves ~700 MB and trims the runtime
# attack surface (no compiler, no apt-get, no pip cache).
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Build deps for any wheels that need to compile (numpy on platforms
# without a manylinux wheel, occasionally onnxruntime).
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# Install everything into /install so the runtime stage can copy
# `site-packages` wholesale.
COPY requirements.txt .
RUN pip install --prefix=/install \
        torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    pip install --prefix=/install -r requirements.txt

# ── Runtime stage ────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Cap ONNX Runtime + OpenMP thread pools so they line up with the
    # FastAPI inference executor (INFER_WORKERS) instead of fighting
    # for cores. The classifier session also pins intra_op=1; this is
    # the belt-and-braces for any other ORT-using code path.
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
RUN mkdir -p /app/logs /app/models && \
    adduser --disabled-password --gecos "" --uid 10001 plantd && \
    chown -R plantd:plantd /app

VOLUME ["/app/models"]

USER plantd

EXPOSE 8000

# Lightweight healthcheck — uvicorn answers /api/health in <5 ms.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD \
    python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=2).status==200 else 1)" || exit 1

# `--limit-concurrency 64` caps simultaneously-accepted connections —
# beyond that uvicorn returns 503 instead of queueing unboundedly. With
# INFER_WORKERS=2 and ~150 ms per analysis we'd never serve more than
# ~13 req/s anyway; a 64-deep accept queue tolerates short bursts
# without letting a slowloris-style flood exhaust the event loop.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--forwarded-allow-ips", "127.0.0.1", "--proxy-headers", \
     "--limit-concurrency", "64", "--timeout-keep-alive", "5"]
