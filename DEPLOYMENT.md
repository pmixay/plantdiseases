# Deployment Guide

Production deployment notes for the PlantDiseases FastAPI server. The
Android client needs no server-side change — point it at the public URL
through **Profile → Server URL**.

Code paths and env var names below match `server/main.py`, `server/Dockerfile`
and `server/docker-compose.yml`.

---

## 1. Production environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Comma-separated list of explicit allowed origins. **Do not set to `*` in production.** |
| `TRUSTED_PROXIES` | *(empty)* | Comma-separated IPs or CIDRs whose `X-Forwarded-For` header is honoured. Required when the server sits behind nginx/cloudflare; otherwise attacker-supplied `X-Forwarded-For` headers are ignored, which is safer but breaks rate-limiting by real client IP. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Passed to uvicorn `--forwarded-allow-ips`; controls which peers can update `Host` / scheme via forwarded headers. |
| `RATE_LIMIT_RPS` | `1.0` | Per-IP request rate limit. Rate-limited responses return `429` with a `Retry-After` header. |
| `MAX_FILE_SIZE` | `10485760` (10 MB) | Hard cap on uploaded bytes. |
| `MAX_IMAGE_DIMENSION` | `4096` | Pixel limit per side, in addition to Pillow decompression-bomb guard. |
| `INFERENCE_TIMEOUT` | `30` | Seconds before `/api/analyze` returns `504`. |
| `INFER_WORKERS` | `2` | ThreadPoolExecutor workers for model inference. |
| `LOG_DIR` | `logs` | Rotating file logs (5 MB × 5 backups). |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Uvicorn bind. |

---

## 2. Docker Compose (recommended)

The repo ships a production-oriented compose file: non-root user,
read-only models volume, health check, restart policy.

```bash
cd server
export CORS_ORIGINS="https://plantdiseases.example.com"
export TRUSTED_PROXIES="127.0.0.1,10.0.0.0/8"
export RATE_LIMIT_RPS=2.0
docker compose up -d --build
```

Model weights go under `server/models/` on the host; the container
mounts it read-only (`./models:/app/models:ro`). Without weights the
server runs in demo mode (colour-based heuristics).

Smoke test:

```bash
curl -s http://127.0.0.1:8000/api/health | jq
curl -F "image=@sample.jpg" http://127.0.0.1:8000/api/analyze | jq
```

---

## 3. Reverse proxy (nginx + Let's Encrypt)

TLS termination and HTTP→HTTPS redirect belong in nginx; the app
server talks plain HTTP on `127.0.0.1:8000`.

```nginx
# /etc/nginx/sites-available/plantdiseases.conf
server {
    listen 80;
    server_name plantdiseases.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name plantdiseases.example.com;

    ssl_certificate     /etc/letsencrypt/live/plantdiseases.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/plantdiseases.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 15m;          # must exceed MAX_FILE_SIZE
    proxy_read_timeout   60s;          # must exceed INFERENCE_TIMEOUT

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

Issue and renew the certificate:

```bash
sudo certbot --nginx -d plantdiseases.example.com
sudo systemctl enable --now certbot.timer
```

Because nginx is now forwarding, set `TRUSTED_PROXIES=127.0.0.1` on the
app server so its rate limiter reads `X-Forwarded-For` honestly.

---

## 4. systemd unit (alternative to Docker)

Use this when Docker is not an option — a single Python venv plus a
user-scoped service.

```ini
# /etc/systemd/system/plantdiseases.service
[Unit]
Description=PlantDiseases FastAPI server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=plantd
Group=plantd
WorkingDirectory=/opt/plantdiseases/server
Environment=CORS_ORIGINS=https://plantdiseases.example.com
Environment=TRUSTED_PROXIES=127.0.0.1
Environment=RATE_LIMIT_RPS=2.0
Environment=FORWARDED_ALLOW_IPS=127.0.0.1
ExecStart=/opt/plantdiseases/.venv/bin/uvicorn main:app \
          --host 127.0.0.1 --port 8000 \
          --forwarded-allow-ips 127.0.0.1 --proxy-headers
Restart=on-failure
RestartSec=3
# Hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/plantdiseases/server/logs

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd --system --home /opt/plantdiseases plantd
sudo -u plantd python3.11 -m venv /opt/plantdiseases/.venv
sudo -u plantd /opt/plantdiseases/.venv/bin/pip install -r /opt/plantdiseases/server/requirements.txt
sudo systemctl daemon-reload
sudo systemctl enable --now plantdiseases
journalctl -u plantdiseases -f
```

---

## 5. Monitoring

- JSON counters: `GET /api/metrics`
- Prometheus exposition: `GET /api/metrics/prometheus`
  (`Content-Type: text/plain; version=0.0.4`)
- Liveness probe: `GET /api/health` — also used by the container `HEALTHCHECK`.

Both metrics endpoints are exempt from the rate limiter, so they are
safe to scrape every 15 s.

---

## 6. Pre-launch checklist

Before opening the service to the public:

- [ ] TLS enforced — HTTP redirects to HTTPS, HSTS enabled at the proxy.
- [ ] `CORS_ORIGINS` pinned to the real front-end origin(s); **no `*`**.
- [ ] `TRUSTED_PROXIES` set to just the reverse-proxy IP(s)/CIDR.
- [ ] `RATE_LIMIT_RPS` tuned for expected traffic (default `1.0` is per-IP).
- [ ] `MAX_FILE_SIZE` aligned with nginx `client_max_body_size`.
- [ ] `proxy_read_timeout` ≥ `INFERENCE_TIMEOUT`.
- [ ] Model files (`detector.pt`, `classifier.pth`, `classes.json`) present under `server/models/` — otherwise the server stays in demo mode (acceptable for a jury demo, not for a prod launch).
- [ ] `/api/health` reports `pipeline_mode: "full"`.
- [ ] `classes.json` `num_classes` matches the classifier head size (the server logs a mismatch otherwise).
- [ ] Logs are rotating (`LOG_DIR`) and being shipped off-box.
- [ ] Prometheus/Grafana (or equivalent) is scraping `/api/metrics/prometheus`.
- [ ] Backup strategy for `server/logs/` and `server/models/` in place.
- [ ] Container runs as a non-root user (`Dockerfile` already does this).
- [ ] Docker image built from a tagged commit, not `latest`.
- [ ] Android app `network_security_config.xml` points at the production HTTPS host (HTTP cleartext is only allowed for `10.0.2.2` / `localhost`).
- [ ] Smoke test: `curl -F "image=@leaf.jpg" https://.../api/analyze` returns a bilingual diagnosis with `pipeline_mode: "full"`.
