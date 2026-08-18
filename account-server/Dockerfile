# syntax=docker/dockerfile:1.6
FROM python:3.11.15-slim@sha256:94c50be2dc994b873b55bc123e95e6dbade08095b3dfd790f51c34de3f08cbb7 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

FROM python:3.11.15-slim@sha256:94c50be2dc994b873b55bc123e95e6dbade08095b3dfd790f51c34de3f08cbb7 AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --from=builder /install /usr/local
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations

RUN mkdir -p /run/account-secrets /run/internal-token && \
    adduser --disabled-password --gecos "" --uid 10002 accountd && \
    chown -R accountd:accountd /app /run/account-secrets /run/internal-token && \
    chmod 0750 /run/internal-token

USER accountd
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD \
    python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--forwarded-allow-ips", "127.0.0.1", "--proxy-headers", \
     "--no-access-log", "--limit-concurrency", "64", \
     "--timeout-keep-alive", "5"]
